"""Understand + Plan 兩個階段。

刻意不叫模型做規劃：
  · 確定性 —— 同一句話永遠得到同一個計畫，evals 才測得起來
  · 不花額度、不增加一輪延遲
  · 開源模型產出的計畫品質不穩，還常常把計畫當成回覆吐給使用者

模型負責的是「內容怎麼寫」，計畫骨架由這裡決定。
"""

from __future__ import annotations

import re

from ..research import entities as research_entities
from ..research.entities import EntityResolution, ResearchMode, ResearchScope
from ..services import current_term as term_service
from ..skills import SKILL_BY_NAME, Routing, is_continuation_only, route
from .state import OrchestrationState, PlanStep, Stage, TaskType, WorkflowStatus

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


def build_retrieval_queries(
    message: str,
    routing: Routing,
    scope: ResearchScope | None = None,
    resolution: EntityResolution | None = None,
) -> list[str]:
    """把一句自然語言拆成幾個互補的檢索查詢。

    一定會包含「劇本層」與「歷年範例層」各至少一個，
    這樣 context 裡永遠同時有「該怎麼做」與「以前怎麼做的」。
    外部研究時，另外用「正式社團名＋主題」下查詢——研究對象是誰，
    查詢就寫誰，不讓 BM25 拿相似內容亂配對。
    """
    queries: list[str] = []
    cleaned = re.sub(r"[，。！？、\n]+", " ", message).strip()

    # 外部研究對象的專屬查詢放最前面
    if scope is not None and scope.mode in {ResearchMode.EXTERNAL, ResearchMode.COMPARATIVE}:
        for eid in scope.target_entities:
            entity = research_entities.entity_by_id(eid)
            if entity is not None:
                queries.append(f"{entity.name} {cleaned[:40]}".strip())
                queries.append(entity.name)
        if scope.generic_external:
            queries.append(f"外校 社群 {cleaned[:40]}".strip())

    # 1. 使用者原句（抓專有名詞、活動名稱最準）
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
    return out[:5]


def plan_for(routing: Routing, needs_artifact: bool, scope: ResearchScope | None = None) -> list[PlanStep]:
    skill = routing.skill
    if routing.task_sequence:
        steps = [
            PlanStep("研究指定的外校公開資料", kind="research"),
            PlanStep("整理研究來源與可用洞察", kind="synthesis"),
            PlanStep("建立淡江網宣", kind="artifact"),
            PlanStep("檢查產出是否符合社團規範", kind="verify"),
            PlanStep("交付並說明後續步驟", kind="deliver"),
        ]
        # 依描述產生穩定 id，避免依賴 hash 的跨程序不一致。
        for i, step in enumerate(steps, 1):
            step.step_id = f"composite-{i}"
        steps[1].depends_on = [steps[0].step_id]
        steps[2].depends_on = [steps[0].step_id]
        return steps

    # 純研究（不產檔）的外部／比較模式有自己的計畫骨架
    if scope is not None and scope.mode == ResearchMode.EXTERNAL:
        return [
            PlanStep("確認研究對象（學校與正式社團）", kind="research"),
            PlanStep("查外校官方公開資料", kind="research"),
            PlanStep("只依可驗證來源整理，推測分開標示", kind="synthesis"),
            PlanStep("檢查來源與研究對象一致", kind="verify"),
        ]
    if scope is not None and scope.mode == ResearchMode.COMPARATIVE:
        return [
            PlanStep("確認研究對象（學校與正式社團）", kind="research"),
            PlanStep("查外校官方公開資料", kind="research"),
            PlanStep("查淡江內部資料（分開整理）", kind="retrieval"),
            PlanStep("比較差異並提出淡江可採用建議", kind="synthesis"),
            PlanStep("檢查來源與研究對象一致", kind="verify"),
        ]

    steps = [PlanStep("查社團知識庫與歷年範例", kind="retrieval")]

    if skill.name == "activity_management":
        steps.append(PlanStep("讀取或更新活動、分工與待辦", kind="activity"))
        steps.append(PlanStep("整理活動缺口、逾期與下一步", kind="deliver"))
        return steps

    if skill.name == "event_planning" and routing.preferred_tool == "create_activity" and not needs_artifact:
        steps.append(PlanStep("建立這場活動的正式資料", kind="activity"))
        steps.append(PlanStep("回報活動資料與待填事項", kind="deliver"))
        return steps

    if not needs_artifact:
        steps.append(PlanStep("依知識庫內容回答"))
        return steps

    activity_step: PlanStep | None = None
    if skill.name == "event_planning":
        activity_step = PlanStep("建立或更新這場活動的正式資料", kind="activity")
        steps.append(activity_step)

    kinds = [routing.preferred_artifact] if routing.preferred_artifact else list(skill.artifacts_expected)
    kinds = kinds or ["document"]
    for kind in kinds:
        artifact_step = PlanStep(f"建立{ARTIFACT_LABEL.get(kind, kind)}", kind="artifact")
        if activity_step:
            artifact_step.depends_on = [activity_step.step_id]
        steps.append(artifact_step)
    steps.append(PlanStep("檢查產出是否符合社團規範"))
    steps.append(PlanStep("交付並說明後續步驟"))
    return steps


def verification_rules_for(routing: Routing) -> list[str]:
    rules = ["no_fabricated_current_facts", "no_stale_year_as_current", "placeholder_for_unknown"]
    if routing.skill.name == "social_publicity" or "social_publicity" in routing.task_sequence:
        rules += ["verify_social_copy", "no_health_claims", "not_religious_recruitment", "external_tone"]
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
    """Understand → Plan。回傳初始化好的狀態。

    政大事故後：這裡先做**實體解析**再做其他事。研究對象是誰、
    用哪種研究模式，在任何檢索發生之前就定案，並寫進 state 讓
    檢索與回答閘門共用同一份範圍。
    """
    routing = route(message)

    # ── 實體解析與研究模式 ────────────────────────────────
    resolution = research_entities.resolve(message)
    scope = research_entities.decide_scope(message, resolution, routing.skill.task_type)
    # 複合任務（外校研究 → 淡江網宣）一定是比較分析：網宣步驟需要淡江內部資料
    if routing.task_sequence and scope.mode == ResearchMode.EXTERNAL:
        scope.mode = ResearchMode.COMPARATIVE

    # 訊息點名了外校，但路由落在一般查詢／文件 → 改走外校社群研究，
    # 讓後面的檢索與驗證都按外部研究的規矩來。
    if (
        scope.mode in {ResearchMode.EXTERNAL, ResearchMode.COMPARATIVE}
        and routing.skill.name in {"knowledge", "documents"}
        and not routing.task_sequence
    ):
        import dataclasses

        routing = dataclasses.replace(
            routing,
            skill=SKILL_BY_NAME["social_research"],
            runner_up=routing.skill.name,
            produce_artifact=False,
        )

    needs_artifact = routing.produce_artifact and bool(routing.skill.artifacts_expected)

    state = OrchestrationState()
    state.intent = message.strip()[:300]
    try:
        state.task_type = TaskType("composite" if routing.task_sequence else routing.skill.task_type)
    except ValueError:
        state.task_type = TaskType.UNKNOWN
    state.selected_skill = routing.skill.name
    state.research_mode = scope.mode.value
    state.research_scope = scope.to_dict()
    state.target_entities = list(scope.target_entities)
    state.target_schools = list(scope.target_schools)
    state.clarification_pending = (
        scope.mode in {ResearchMode.EXTERNAL, ResearchMode.COMPARATIVE}
        and resolution.needs_clarification
    )
    state.required_facts = detect_required_facts(message, routing.skill.required_facts)
    state.retrieval_queries = build_retrieval_queries(message, routing, scope, resolution)
    expected = [routing.preferred_artifact] if routing.preferred_artifact else list(routing.skill.artifacts_expected)
    state.artifacts_expected = expected if needs_artifact else []
    state.verification_rules = verification_rules_for(routing) if needs_artifact else []
    state.plan_steps = plan_for(routing, needs_artifact, scope)

    # 對照當期狀態，看看缺哪些今年的事實
    term = term_service.load()
    known = term.known()
    state.known_facts = {k: v for k, v in known.items() if k in state.required_facts}
    state.missing_facts = [k for k in state.required_facts if k not in known]

    state.stage = Stage.PLAN
    state.workflow_status = WorkflowStatus.IN_PROGRESS
    state.next_action = state.next_step().description if state.next_step() else ""
    return state, routing


def continue_previous(
    message: str, previous: OrchestrationState,
) -> tuple[OrchestrationState, Routing]:
    """還原上一個未完成任務，不建立新的任務圖，也不丟掉已完成步驟。"""
    routing = route(previous.intent)
    state = OrchestrationState.from_dict(previous.to_dict())
    state.intent = message.strip()[:300] or previous.intent
    state.stage = Stage.EXECUTE
    state.workflow_status = WorkflowStatus.IN_PROGRESS
    state.completion_status = "in_progress"
    # tool_rounds 是單次請求的安全上限，不可跨續接累加，否則長任務幾輪後會被誤判成無限迴圈。
    state.tool_rounds = 0
    state.next_action = state.next_step().description if state.next_step() else ""
    return state, routing


def describe_plan(state: OrchestrationState) -> str:
    """給 UI 看的一行計畫描述。不是 chain-of-thought，是可驗證的步驟清單。"""
    return " → ".join(s.description for s in state.plan_steps)
