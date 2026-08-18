"""Understand + Plan 兩個階段。

刻意不叫模型做規劃：
  · 確定性 —— 同一句話永遠得到同一個計畫，evals 才測得起來
  · 不花額度、不增加一輪延遲
  · 開源模型產出的計畫品質不穩，還常常把計畫當成回覆吐給使用者

模型負責的是「內容怎麼寫」，計畫骨架由這裡決定。
"""

from __future__ import annotations

import re

from ..services import current_term as term_service
from ..skills import Routing, route
from .state import OrchestrationState, PlanStep, Stage, TaskType

# 這些問題問的是「今年的事實」，一定要先查當期狀態
_CURRENT_FACT_HINTS = re.compile(
    r"(今年|本學期|這學期|現在|目前|這個?學期|當期|最新|今天|這禮拜|這週|下週|下個月)"
)
# 問到具體會逐年變動的欄位
_FACT_KEYWORDS = {
    "president": ("社長", "誰是社長", "會長"),
    "officers": ("幹部", "組長", "總召", "家族長", "組輔"),
    "regular_meeting_time": ("社課時間", "幾點", "星期幾", "禮拜幾", "什麼時候上課"),
    "regular_meeting_location": ("社課地點", "在哪", "教室", "地點"),
    "club_fee": ("社費", "多少錢", "費用"),
    "recruitment_period": ("招生期間", "招生到什麼時候", "報名期限"),
    "signup_url": ("報名連結", "報名網址", "怎麼報名", "表單連結"),
}

# 產出類型 → 中文說法（給 UI）
ARTIFACT_LABEL = {
    "document": "文件",
    "spreadsheet": "試算表",
    "slides": "簡報",
    "form": "Google 表單",
}


def detect_required_facts(message: str, skill_required: tuple[str, ...]) -> list[str]:
    """判斷這個請求需要哪些「今年的事實」。"""
    needed: list[str] = list(skill_required)
    asks_current = bool(_CURRENT_FACT_HINTS.search(message))
    for key, words in _FACT_KEYWORDS.items():
        if any(w in message for w in words):
            needed.append(key)
        elif asks_current and key in {"president", "regular_meeting_time"} and "社團" in message:
            needed.append(key)
    # 去重但保留順序
    out: list[str] = []
    for k in needed:
        if k not in out:
            out.append(k)
    return out


def build_retrieval_queries(message: str, routing: Routing) -> list[str]:
    """把一句自然語言拆成幾個互補的檢索查詢。

    一定會包含「劇本層」與「歷年範例層」各至少一個，
    這樣 context 裡永遠同時有「該怎麼做」與「以前怎麼做的」。
    """
    queries: list[str] = []

    # 1. 使用者原句（抓專有名詞、活動名稱最準）
    cleaned = re.sub(r"[，。！？、\n]+", " ", message).strip()
    if cleaned:
        queries.append(cleaned[:80])

    # 2. 該 skill 的劇本
    queries.extend(routing.skill.playbook_hints)

    # 3. 命中的關鍵字組合（拉出活動名稱這類專有詞）
    hits = [kw for kw in routing.skill.all_terms() if kw.lower() in message.lower()]
    if hits:
        queries.append(" ".join(hits[:4]))

    out: list[str] = []
    for q in queries:
        q = q.strip()
        if q and q not in out:
            out.append(q)
    return out[:4]


def plan_for(routing: Routing, needs_artifact: bool) -> list[PlanStep]:
    skill = routing.skill
    steps = [PlanStep("查社團知識庫與歷年範例")]

    if not needs_artifact:
        steps.append(PlanStep("依知識庫內容回答"))
        return steps

    kinds = list(skill.artifacts_expected) or ["document"]
    for kind in kinds:
        steps.append(PlanStep(f"建立{ARTIFACT_LABEL.get(kind, kind)}"))
    steps.append(PlanStep("檢查產出是否符合社團規範"))
    steps.append(PlanStep("交付並說明後續步驟"))
    return steps


def verification_rules_for(routing: Routing) -> list[str]:
    rules = ["no_fabricated_current_facts", "no_stale_year_as_current", "placeholder_for_unknown"]
    if routing.skill.name == "recruitment":
        rules += ["no_health_claims", "not_religious_recruitment", "external_tone"]
    if routing.skill.name == "evaluation":
        rules += ["no_health_claims", "required_sections"]
    if routing.skill.name == "finance":
        rules += ["formulas_present", "numeric_types"]
    if routing.skill.name in {"event_planning", "handover", "documents"}:
        rules += ["no_health_claims", "required_sections"]
    return rules


def understand(message: str) -> tuple[OrchestrationState, Routing]:
    """Understand → Plan。回傳初始化好的狀態。"""
    routing = route(message)
    needs_artifact = routing.produce_artifact and bool(routing.skill.artifacts_expected)

    state = OrchestrationState()
    state.intent = message.strip()[:300]
    try:
        state.task_type = TaskType(routing.skill.task_type)
    except ValueError:
        state.task_type = TaskType.UNKNOWN
    state.selected_skill = routing.skill.name
    state.required_facts = detect_required_facts(message, routing.skill.required_facts)
    state.retrieval_queries = build_retrieval_queries(message, routing)
    state.artifacts_expected = list(routing.skill.artifacts_expected) if needs_artifact else []
    state.verification_rules = verification_rules_for(routing) if needs_artifact else []
    state.plan_steps = plan_for(routing, needs_artifact)

    # 對照當期狀態，看看缺哪些今年的事實
    term = term_service.load()
    known = term.known()
    state.known_facts = {k: v for k, v in known.items() if k in state.required_facts}
    state.missing_facts = [k for k in state.required_facts if k not in known]

    state.stage = Stage.PLAN
    return state, routing


def describe_plan(state: OrchestrationState) -> str:
    """給 UI 看的一行計畫描述。不是 chain-of-thought，是可驗證的步驟清單。"""
    return " → ".join(s.description for s in state.plan_steps)
