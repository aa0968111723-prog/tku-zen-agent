"""NVIDIA Build（NIM）用戶端 —— OpenAI 相容的 /chat/completions。

開源模型的工具呼叫沒有 Claude 那麼穩，所以這裡多做了幾層容錯：
  1. arguments 有時是字串、有時是物件、有時是壞掉的 JSON → 三種都試著修好
  2. 有些模型不用 tool_calls 欄位，而是把 JSON 直接吐在 content 裡 → 額外解析
  3. 429 / 5xx → 指數退避重試
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from . import config


class LLMError(RuntimeError):
    pass


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
        self.api_key = api_key or config.NVIDIA_API_KEY
        self.base_url = (base_url or config.NVIDIA_BASE_URL).rstrip("/")
        self.model = model or config.NVIDIA_MODEL

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
            raise LLMError(
                "沒有 NVIDIA API 金鑰。請到 https://build.nvidia.com/settings/api-keys "
                "免費申請一組，填進專案根目錄的 .env 檔的 NVIDIA_API_KEY。"
            )

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

        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=180.0) as client:
                    resp = await client.post(
                        f"{self.base_url}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                            "Accept": "application/json",
                        },
                        json=payload,
                    )
                if resp.status_code == 401:
                    raise LLMError("NVIDIA API 金鑰被拒（401）。請確認 .env 裡的 NVIDIA_API_KEY 正確且未過期。")
                if resp.status_code == 404:
                    raise LLMError(
                        f"找不到模型 {payload['model']}（404）。"
                        "請到 https://build.nvidia.com/models 確認模型代號，或改用 .env 裡建議的其他模型。"
                    )
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise httpx.HTTPStatusError("retryable", request=resp.request, response=resp)
                if resp.status_code >= 400:
                    raise LLMError(f"NVIDIA API 回應 {resp.status_code}：{resp.text[:500]}")
                return self._parse(resp.json(), valid_names={t["function"]["name"] for t in (tools or [])})
            except LLMError:
                raise
            except (httpx.HTTPStatusError, httpx.TransportError, httpx.TimeoutException) as exc:
                last_error = exc
                if attempt == max_retries - 1:
                    break
                await asyncio.sleep(2**attempt)

        raise LLMError(
            "連續呼叫 NVIDIA API 失敗（可能是免費額度用完、達到每分鐘 40 次上限，或網路問題）。"
            f"最後一次錯誤：{last_error}"
        )

    @staticmethod
    def _parse(data: dict[str, Any], valid_names: set[str]) -> Reply:
        choices = data.get("choices") or []
        if not choices:
            raise LLMError(f"NVIDIA API 回應沒有 choices：{json.dumps(data, ensure_ascii=False)[:400]}")
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
