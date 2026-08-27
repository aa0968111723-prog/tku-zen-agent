"""NVIDIA Build（NIM）用戶端 —— OpenAI 相容的 /chat/completions。

開源模型的工具呼叫沒有 Claude 那麼穩，所以這裡多做了幾層容錯：
  1. arguments 有時是字串、有時是物件、有時是壞掉的 JSON → 三種都試著修好
  2. 有些模型不用 tool_calls 欄位，而是把 JSON 直接吐在 content 裡 → 額外解析
  3. 429 / 5xx → 指數退避重試
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from . import config

logger = logging.getLogger(__name__)

# 單次 chat 呼叫（含全部重試與退避）的總時限，秒。
TOTAL_DEADLINE_SECONDS = 240.0


class LLMError(RuntimeError):
    pass


# ── 供應商顯示名（錯誤訊息用；不要在訊息裡硬編碼某一家）────────
_PROVIDER_NAMES = {"nvidia": "NVIDIA Build", "zeabur": "Zeabur AI Hub"}
_PROVIDER_KEY_ENV = {"nvidia": "NVIDIA_API_KEY", "zeabur": "ZEABUR_API_KEY"}
_PROVIDER_SIGNUP = {
    "nvidia": "https://build.nvidia.com/settings/api-keys",
    "zeabur": "https://zeabur.com/dashboard（AI Hub → API Keys）",
}


def _provider_name() -> str:
    return _PROVIDER_NAMES.get(config.LLM_PROVIDER, "模型服務")


def _key_env_name() -> str:
    return _PROVIDER_KEY_ENV.get(config.LLM_PROVIDER, "LLM_API_KEY")


def _missing_key_message() -> str:
    return (
        f"沒有 {_provider_name()} 的 API 金鑰。請到 "
        f"{_PROVIDER_SIGNUP.get(config.LLM_PROVIDER, '供應商後台')} 取得一組，"
        f"填進 .env 或部署環境變數的 {_key_env_name()}。"
    )


# ── 模型路由 ─────────────────────────────────────────────────
# 不同階段對模型的要求不一樣：分類/改寫要快，長文與工具呼叫要穩。
# 使用者在介面上選的模型永遠優先（override），這裡只是沒指定時的預設。
MODEL_ROUTES: dict[str, str] = {
    "classify": config.LLM_FAST_MODEL,         # 短、量大、要求低
    "execute": "",                              # 空 = 用 config.NVIDIA_MODEL
    "longform": config.LLM_STRONG_MODEL,
}


def route_model(task: str = "execute") -> str:
    """依任務類型挑模型。沒設定就退回使用者的主力模型。"""
    return (MODEL_ROUTES.get(task) or config.LLM_MODEL).strip()


def select_model(
    *,
    message: str,
    task_type: str,
    needs_artifact: bool,
    composite: bool = False,
    explicit: str | None = None,
) -> dict[str, str]:
    """用可解釋且可測的規則選模型，不額外花一次模型分類呼叫。"""
    if explicit:
        return {"model": explicit.strip(), "tier": "manual", "reason": "使用者指定模型"}
    if task_type == "knowledge" and not needs_artifact and not composite and len(message) <= 240:
        return {"model": route_model("classify"), "tier": "economy", "reason": "簡短知識查詢"}
    if composite or task_type in {"social_research", "composite"} or needs_artifact:
        return {"model": route_model("longform"), "tier": "strong", "reason": "研究、產出或多步驟任務"}
    return {"model": route_model("execute"), "tier": "standard", "reason": "一般任務"}


def estimate_cost(input_tokens: int, output_tokens: int) -> float:
    return round(
        input_tokens / 1_000_000 * config.NVIDIA_INPUT_COST_PER_MILLION
        + output_tokens / 1_000_000 * config.NVIDIA_OUTPUT_COST_PER_MILLION,
        8,
    )


# ── 連線池 ───────────────────────────────────────────────────
# 原本每次呼叫都 `async with httpx.AsyncClient()`，等於每一輪工具呼叫
# 都重新做一次 TCP + TLS 握手。改成共用一個 client。
_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()

_telemetry: dict[str, Any] = {
    "requests": 0,
    "retries": 0,
    "failures": 0,
    "timeouts": 0,
    "total_seconds": 0.0,
    "by_model": {},
}


async def get_http_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        async with _client_lock:
            if _client is None or _client.is_closed:
                _client = httpx.AsyncClient(
                    timeout=httpx.Timeout(180.0, connect=15.0),
                    limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
                    headers={"Accept": "application/json", "Content-Type": "application/json"},
                    follow_redirects=False,
                )
    return _client


async def close_http_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


def pool_stats() -> dict[str, Any]:
    n = _telemetry["requests"] or 1
    return {
        "requests": _telemetry["requests"],
        "retries": _telemetry["retries"],
        "failures": _telemetry["failures"],
        "timeouts": _telemetry["timeouts"],
        "avg_seconds": round(_telemetry["total_seconds"] / n, 2),
        "by_model": _telemetry["by_model"],
        "pool_open": _client is not None and not _client.is_closed,
    }


def reset_telemetry() -> None:
    _telemetry.update(
        {"requests": 0, "retries": 0, "failures": 0, "timeouts": 0, "total_seconds": 0.0, "by_model": {}}
    )


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Reply:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


# ── JSON 修補 ────────────────────────────────────────────────────

def _loads_loose(text: str) -> Any:
    """盡量把模型吐出來的東西解析成 JSON。"""
    text = text.strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 去掉 markdown code fence
    fenced = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.S)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            text = fenced.group(1).strip()

    # 抓第一個平衡的 { } 或 [ ]
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start == -1:
            continue
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
    raise ValueError(f"無法解析成 JSON：{text[:200]}")


def _coerce_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if raw in (None, ""):
        return {}
    if isinstance(raw, str):
        parsed = _loads_loose(raw)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    return {"value": raw}


_INLINE_CALL = re.compile(
    r'\{\s*"(?:name|tool|function|tool_name)"\s*:\s*"(?P<name>[a-zA-Z0-9_.\-]+)"'
    r'\s*,\s*"(?:arguments|parameters|args|input)"\s*:\s*(?P<args>\{.*)',
    re.S,
)


def _salvage_inline_tool_call(content: str, valid_names: set[str]) -> list[ToolCall]:
    """有些模型把工具呼叫寫在 content 裡而不是 tool_calls 欄位。"""
    if not content or "{" not in content:
        return []
    m = _INLINE_CALL.search(content)
    if not m or m.group("name") not in valid_names:
        return []
    try:
        args = _loads_loose(m.group("args"))
    except ValueError:
        return []
    if not isinstance(args, dict):
        return []
    return [ToolCall(id="salvaged_0", name=m.group("name"), arguments=args)]


# ── 用戶端 ───────────────────────────────────────────────────────

class NvidiaClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None, model: str | None = None):
        self.api_key = api_key or config.LLM_API_KEY
        self.base_url = (base_url or config.LLM_BASE_URL).rstrip("/")
        self.model = model or config.LLM_MODEL

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        max_retries: int = 4,
    ) -> Reply:
        if not self.api_key:
            raise LLMError(_missing_key_message())

        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        model_name = str(payload["model"])
        _telemetry["requests"] += 1
        _telemetry["by_model"][model_name] = _telemetry["by_model"].get(model_name, 0) + 1
        started = time.perf_counter()

        last_error: Exception | None = None
        # 重試不能沒有總時限：180s 逾時 × 4 次 × 退避最壞可掛 12 分鐘，
        # 前端 watchdog 早就放棄了，伺服器卻還在燒（稽核不可靠 #5）。
        deadline = started + TOTAL_DEADLINE_SECONDS
        for attempt in range(max_retries):
            try:
                client = await get_http_client()
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                )
                # 注意順序：具體診斷（401/404）必須在任何泛化錯誤之前，
                # 不然金鑰貼錯永遠只看得到「模型服務無法使用」（稽核不可靠 #2）。
                if resp.status_code == 401:
                    logger.error("NVIDIA API key rejected (401)")
                    raise LLMError(
                        f"{_provider_name()} 的 API 金鑰被拒（401）。"
                        f"請確認環境變數 {_key_env_name()} 正確且未過期。"
                    )
                if resp.status_code == 404:
                    logger.error("model not found (404): %s", payload["model"])
                    raise LLMError(
                        f"找不到模型 {payload['model']}（404）。"
                        "請到 https://build.nvidia.com/models 確認模型代號，或改用 .env 裡建議的其他模型。"
                    )
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise httpx.HTTPStatusError("retryable", request=resp.request, response=resp)
                if resp.status_code >= 400:
                    logger.error("upstream LLM request failed: status=%s", resp.status_code)
                    raise LLMError("模型服務暫時無法回應，請稍後再試")
                _telemetry["total_seconds"] += time.perf_counter() - started
                return self._parse(resp.json(), valid_names={t["function"]["name"] for t in (tools or [])})
            except LLMError:
                _telemetry["failures"] += 1
                _telemetry["total_seconds"] += time.perf_counter() - started
                raise
            except (httpx.HTTPStatusError, httpx.TransportError, httpx.TimeoutException) as exc:
                last_error = exc
                if isinstance(exc, httpx.TimeoutException):
                    _telemetry["timeouts"] += 1
                if attempt == max_retries - 1 or time.perf_counter() >= deadline:
                    break
                _telemetry["retries"] += 1
                await asyncio.sleep(min(2**attempt, max(0.0, deadline - time.perf_counter())))

        _telemetry["failures"] += 1
        _telemetry["total_seconds"] += time.perf_counter() - started
        raise LLMError(
            f"連續呼叫 {_provider_name()} 失敗（可能是額度或點數用完、達到速率上限，或網路問題）。"
        )

    @staticmethod
    def _parse(data: dict[str, Any], valid_names: set[str]) -> Reply:
        choices = data.get("choices") or []
        if not choices:
            raise LLMError("模型服務回應格式異常，請稍後再試")
        msg = choices[0].get("message") or {}
        content = (msg.get("content") or "").strip()

        calls: list[ToolCall] = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function") or {}
            name = fn.get("name")
            if not name:
                continue
            try:
                args = _coerce_args(fn.get("arguments"))
            except ValueError:
                args = {"__parse_error__": str(fn.get("arguments"))[:500]}
            calls.append(ToolCall(id=tc.get("id") or f"call_{i}", name=name, arguments=args))

        if not calls:
            calls = _salvage_inline_tool_call(content, valid_names)
            if calls:
                content = ""

        return Reply(content=content, tool_calls=calls, raw=data)
