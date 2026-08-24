"""Working Memory / Project State。

原本靠「最近 40 則對話」保存任務狀態，長任務一定會掉東西。
這裡把「會改變結果的事實」抽出來獨立存進 working_memory，
對話再怎麼壓縮都不會遺失。

壓縮刻意用**抽取式**而不是叫模型寫摘要：
  · 模型摘要會漏掉或改寫關鍵事實，那正是這一層要防的事
  · 不花額度、不增加延遲
  · 確定性，測得起來
"""

from __future__ import annotations

import json
import hashlib
import re
from typing import Any

from ..orchestrator.state import OrchestrationState, Stage, WorkflowStatus
from . import context as ctx_mod
from .session_store import SessionStore

# 對話保留多少則不壓縮
KEEP_RECENT_MESSAGES = 24
COMPRESS_THRESHOLD = 40

STATE_KEY = "__orchestration_state__"
SUMMARY_KEY = "__conversation_summary__"

# 使用者親口講出來的事實。只抓有把握的句型，寧可漏抓也不要抓錯。
FACT_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("社長", re.compile(r"社長(?:是|叫|為)\s*([^\s，,。、；;!！?？]{2,10})")),
    ("社課時間", re.compile(r"社課(?:時間)?(?:是|在|訂在)\s*([^，,。；;\n]{2,30})")),
    ("社課地點", re.compile(r"(?:社課)?(?:地點|教室)(?:是|在|訂在|改成)\s*([^\s，,。、；;\n]{1,20})")),
    ("社費", re.compile(r"社費(?:是|為|收)\s*([^\s，,。、；;\n]{1,20})")),
    ("預算上限", re.compile(r"預算(?:上限|大概|約|是|為)\s*([^\s，,。、；;\n]{1,20})")),
    ("目標人數", re.compile(r"(?:目標|預計)\s*(\d{1,4})\s*(?:人|位)")),
    ("活動日期", re.compile(r"(?:日期|時間)(?:訂在|是|定在)\s*([0-9]{1,2}\s*[/月]\s*[0-9]{1,2}[^\s，,。；;\n]{0,10})")),
)


# ── 專案 ─────────────────────────────────────────────────────

def ensure_project(store: SessionStore, ctx: ctx_mod.RequestContext, state: OrchestrationState) -> str:
    """取得這個 session 綁定的專案；沒有就開一個。

    同一個 session 的後續對話都算同一個專案，這樣「幫我改上一份企劃」
    才找得到上一份是什麼。
    """
    if ctx.project_id:
        return ctx.project_id
    if ctx.session_id:
        sess = store.get_session(ctx.session_id, ctx.user_id)
        if sess and sess.get("project_id"):
            return str(sess["project_id"])

    name = state.intent[:60] or "未命名任務"
    pid = store.create_project(ctx.user_id, name=name, task_type=state.task_type.value)
    if ctx.session_id:
        store.bind_session_project(ctx.session_id, pid)
        if not (store.get_session(ctx.session_id, ctx.user_id) or {}).get("title"):
            store.set_session_title(ctx.session_id, name)
    return pid


def save_state(store: SessionStore, project_id: str, state: OrchestrationState) -> None:
    store.remember(
        project_id,
        STATE_KEY,
        json.dumps(state.to_dict(), ensure_ascii=False),
        source="orchestrator",
        max_len=24000,
    )
    store.touch_project(project_id)


def load_state(store: SessionStore, project_id: str) -> OrchestrationState | None:
    raw = store.recall(project_id).get(STATE_KEY)
    if not raw:
        return None
    try:
        return OrchestrationState.from_dict(json.loads(raw["value"]))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def set_workflow_status(
    store: SessionStore, project_id: str, status: WorkflowStatus,
    *, next_action: str = "", error_code: str = "",
) -> OrchestrationState | None:
    """由 API 控制工作流；只改公開生命週期，不改已完成步驟。"""
    state = load_state(store, project_id)
    if state is None:
        return None
    state.workflow_status = status
    state.next_action = next_action
    state.last_error_code = error_code
    if status == WorkflowStatus.PAUSED:
        state.completion_status = "in_progress"
    elif status == WorkflowStatus.CANCELLED:
        state.completion_status = "failed"
        state.stage = Stage.FAILED
    elif status == WorkflowStatus.IN_PROGRESS:
        state.completion_status = "in_progress"
    save_state(store, project_id, state)
    return state


def reconcile_progress(current: OrchestrationState, previous: OrchestrationState | None) -> OrchestrationState:
    """把相同描述的步驟進度帶到延續任務，新增步驟維持 pending。"""
    if previous is None:
        return current
    old = {step.description: step for step in previous.plan_steps}
    for step in current.plan_steps:
        prior = old.get(step.description)
        if not prior:
            continue
        step.done = prior.done
        step.status = prior.status
        step.attempts = prior.attempts
        step.note = prior.note
        step.last_error = prior.last_error
        step.step_id = prior.step_id
    current.artifacts_produced = list(previous.artifacts_produced)
    current.retrieved_sources = list(previous.retrieved_sources)
    current.verification_results = list(previous.verification_results)
    current.repair_attempts = previous.repair_attempts
    current.metrics.update(previous.metrics)
    return current


# ── 事實 ─────────────────────────────────────────────────────

def _sentence_around(text: str, pos: int) -> str:
    """取出包住 pos 的那一句（以中文句讀切）。"""
    seps = "。！？!?；;\n"
    start = max((text.rfind(ch, 0, pos) for ch in seps), default=-1)
    ends = [i for i in (text.find(ch, pos) for ch in seps) if i != -1]
    end = min(ends) if ends else len(text)
    return text[start + 1 : end]


def extract_facts(text: str) -> dict[str, str]:
    """從使用者訊息裡抓出已確認的事實。

    句子裡提到其他學校的一律不收——「北科的社長是王小明」寫進
    working_memory 後，每一輪都會以「可以直接採用」注入 prompt，
    變成無人把關的跨校事實污染（稽核漏洞 32）。
    """
    from ..research import entities as research_entities

    found: dict[str, str] = {}
    for label, pattern in FACT_PATTERNS:
        m = pattern.search(text)
        if m:
            value = m.group(1).strip()
            if not value or value in {"什麼", "多少", "誰", "哪裡"}:
                continue
            if research_entities.mentions_external_school(_sentence_around(text, m.start())):
                continue
            found[label] = value[:120]
    return found


def remember_facts_from(store: SessionStore, project_id: str, text: str) -> dict[str, str]:
    facts = extract_facts(text)
    for k, v in facts.items():
        store.remember(project_id, k, v, source="使用者訊息")
    return facts


def recall_facts(store: SessionStore, project_id: str) -> dict[str, str]:
    """給提示詞用的專案事實（不含內部狀態鍵）。"""
    return {
        k: v["value"]
        for k, v in store.recall(project_id).items()
        if not k.startswith("__")
    }


def record_artifact_fact(store: SessionStore, project_id: str, artifact: dict[str, Any]) -> None:
    """記住這個專案產出過什麼，之後才能「更新上一份」。"""
    name = artifact.get("filename")
    if not name:
        return
    store.remember(
        project_id,
        f"產出：{name}",
        f"版本 {artifact.get('version', 1)}"
        + (f"，雲端 {artifact['drive_url']}" if artifact.get("drive_url") else ""),
        source="artifact",
    )


def recent_artifacts(store: SessionStore, user_id: str, project_id: str, limit: int = 8) -> list[dict[str, Any]]:
    """給延續任務看的最新 artifact，不把整條版本鏈塞進模型。"""
    return [a.public() for a in store.list_artifacts(user_id, project_id=project_id, limit=limit)]


def retrieval_cache_key(queries: list[str], task_type: str, scope: dict[str, Any] | None = None) -> str:
    """快取鍵一定要含研究範圍——沒有它，換了研究對象仍會命中舊 context
    （拿淡江 context 回外校問題），這是政大事故的助長因素之一。"""
    value = json.dumps(
        {"queries": queries, "task_type": task_type, "scope": scope or {}},
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def get_cached_context(
    store: SessionStore, project_id: str, queries: list[str], task_type: str, fingerprint: str,
    scope: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    return store.get_retrieval_cache(project_id, retrieval_cache_key(queries, task_type, scope), fingerprint)


def save_cached_context(
    store: SessionStore, project_id: str, queries: list[str], task_type: str, fingerprint: str,
    context_text: str, meta: dict[str, Any],
    scope: dict[str, Any] | None = None,
) -> None:
    store.cache_retrieval(
        project_id, retrieval_cache_key(queries, task_type, scope), fingerprint,
        "\n".join(queries), context_text, meta,
    )


def record_research_sources(store: SessionStore, project_id: str, session_id: str | None, result: dict[str, Any]) -> list[str]:
    sources = result.get("source_records") or result.get("references") or []
    if not isinstance(sources, list):
        return []
    ids = store.record_research_sources(project_id, sources, session_id=session_id)
    if ids:
        store.remember(project_id, "最近研究來源", "、".join(ids), source="research")
    return ids


# ── 訊息 ─────────────────────────────────────────────────────

def persist(store: SessionStore, session_id: str, message: dict[str, Any]) -> None:
    if session_id:
        store.append_message(session_id, message)


def persist_user_message(store: SessionStore, session_id: str, text: str) -> None:
    persist(store, session_id, {"role": "user", "content": text})


def build_messages(
    store: SessionStore,
    *,
    session_id: str,
    project_id: str,
    system_prompt: str,
    user_message: str,
) -> list[dict[str, Any]]:
    """組出送給模型的訊息串。

    system prompt 每次重新組（當期資料、專案事實、檢索結果都會變），
    所以歷史訊息裡不保留舊的 system。
    """
    if project_id:
        remember_facts_from(store, project_id, user_message)

    history = [m for m in store.load_messages(session_id)] if session_id else []
    history = [m for m in history if m.get("role") != "system"]

    summary = ""
    if project_id:
        raw = store.recall(project_id).get(SUMMARY_KEY)
        summary = raw["value"] if raw else ""

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    if summary:
        messages.append(
            {
                "role": "system",
                "content": "## 這個專案先前的進度\n\n" + summary,
            }
        )

    messages.extend(_trim(history))
    messages.append({"role": "user", "content": user_message})
    return messages


def _trim(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """截斷歷史，但不能讓開頭變成孤兒 tool 訊息（沒有對應的 assistant tool_calls）。"""
    if len(history) <= KEEP_RECENT_MESSAGES:
        return history
    keep = history[-KEEP_RECENT_MESSAGES:]
    while keep and keep[0].get("role") == "tool":
        keep.pop(0)
    return keep


def maybe_compress(store: SessionStore, session_id: str, project_id: str) -> bool:
    """對話太長時，把舊訊息壓成摘要。

    抽取式：只留使用者說過什麼、產出過什麼。這些是重看對話時真正需要的，
    而且不會因為模型改寫而失真。
    """
    if not session_id or not project_id:
        return False
    if store.message_count(session_id) < COMPRESS_THRESHOLD:
        return False

    messages = store.load_messages(session_id)
    old = messages[:-KEEP_RECENT_MESSAGES]
    if not old:
        return False

    requests = [
        (m.get("content") or "").strip().split("\n")[0][:100]
        for m in old
        if m.get("role") == "user" and (m.get("content") or "").strip()
    ]
    artifacts = sorted(
        {
            k.removeprefix("產出：")
            for k in store.recall(project_id)
            if k.startswith("產出：")
        }
    )
    facts = recall_facts(store, project_id)

    lines: list[str] = []
    if requests:
        lines.append("使用者先前要求過：")
        lines += [f"- {r}" for r in requests[-12:]]
    if artifacts:
        lines.append("")
        lines.append("已經產出的檔案：" + "、".join(artifacts))
    if facts:
        lines.append("")
        lines.append("已確認的事實：" + "；".join(f"{k}={v}" for k, v in facts.items()))

    store.remember(project_id, SUMMARY_KEY, "\n".join(lines), source="compression")
    return True
