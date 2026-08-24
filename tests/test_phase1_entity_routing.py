"""第一階段深度強化：資料來源、實體辨識、檢索路由。

政大事故的殘餘重演路徑（稽核 §11）：
  A. 代名詞 follow-up（「那他們的茶會呢」）只看最後一句 → scope 跨輪繼承
  B. 工具旁路（search_knowledge 無 scope）→ 工具層與 dispatch 層雙閘門
  C. alias 覆蓋面（全名不觸發）→ registry 單一來源化
  D. _CLARIFIED_MARKERS 首句誤觸 → 狀態式澄清
  E. 續接沿用舊 scope → continue_previous 重新解析
"""

from __future__ import annotations

import pytest

from app.orchestrator import planner
from app.orchestrator.state import OrchestrationState
from app.research import entities as E
from app.services import context as ctx_mod
from app.services import memory as memory_service
from app.skills import has_external_intent, route


# ── A. 代名詞 follow-up 的 scope 跨輪繼承 ────────────────────

def _previous_awaiting_zhengda() -> OrchestrationState:
    """模擬上一輪「政大呢」被反問後保存的狀態。"""
    state, _routing = planner.understand("政大呢")
    assert state.clarification_pending
    state.completion_status = "needs_clarification"
    return state


def test_pronoun_followup_inherits_external_scope():
    prev = _previous_awaiting_zhengda()
    state, routing = planner.understand("那他們的茶會呢", previous=prev)
    assert state.research_mode == "external"
    assert state.target_schools == ["國立政治大學"]
    assert state.clarification_pending, "對象還沒確認，follow-up 也要先反問"
    assert state.metrics.get("scope_inherited") is True


def test_bare_topic_followup_with_ne_inherits_scope():
    prev = _previous_awaiting_zhengda()
    state, _ = planner.understand("招生文案呢", previous=prev)
    assert state.research_mode == "external"
    assert state.clarification_pending


def test_new_internal_task_does_not_inherit_external_scope():
    """使用者換主題（沒有指代語）就是新任務，不能被舊研究範圍卡住。"""
    prev = _previous_awaiting_zhengda()
    state, _ = planner.understand("幫我寫一份社課企劃書", previous=prev)
    assert state.research_mode == "internal"
    assert not state.clarification_pending


def test_followup_after_resolved_external_targets_entity():
    """北醫研究成功後接「他們的招生貼文呢」→ 繼承 tmu-zen，不再反問。"""
    prev_state, _ = planner.understand("研究北醫禪學社的 IG")
    prev_state.completion_status = "in_progress"
    state, _ = planner.understand("那他們的招生貼文呢", previous=prev_state)
    assert state.research_mode in {"external", "comparative"}
    assert state.target_entities == ["tmu-zen"]
    assert not state.clarification_pending


def test_clarified_option_reply_inherits_and_proceeds():
    """反問卡選項回覆（含「正式社團」）→ 視為澄清完成，走誠實查無來源。"""
    prev = _previous_awaiting_zhengda()
    state, _ = planner.understand(
        "我指的是政大的正式社團，請只使用可驗證的官方公開來源研究。", previous=prev,
    )
    assert state.research_mode == "external"
    assert not state.clarification_pending


# ── B. 工具旁路閘門 ──────────────────────────────────────────

def _external_scope_dict(school: str = "國立政治大學") -> dict:
    return E.ResearchScope(
        mode=E.ResearchMode.EXTERNAL, target_schools=[school],
    ).to_dict()


def test_search_knowledge_blocked_in_external_mode():
    from app.tools.knowledge import search_knowledge

    token = ctx_mod._research_scope.set(_external_scope_dict())
    try:
        result = search_knowledge("茶會 文宣")
    finally:
        ctx_mod._research_scope.reset(token)
    assert result["ok"] is False
    assert result["code"] == "scope_blocked"
    assert "淡江" in result["message"] and "證據" in result["message"]


def test_search_previous_examples_blocked_in_external_mode():
    from app.tools.examples import search_previous_examples

    token = ctx_mod._research_scope.set(_external_scope_dict())
    try:
        result = search_previous_examples("茶會 企劃書")
    finally:
        ctx_mod._research_scope.reset(token)
    assert result["ok"] is False
    assert result["code"] == "scope_blocked"


def test_search_knowledge_comparative_mode_carries_attribution():
    from app.tools.knowledge import search_knowledge

    scope = E.ResearchScope(
        mode=E.ResearchMode.COMPARATIVE, target_entities=["tmu-zen"],
    ).to_dict()
    token = ctx_mod._research_scope.set(scope)
    try:
        result = search_knowledge("期初茶會 企劃")
    finally:
        ctx_mod._research_scope.reset(token)
    assert result["ok"] is True
    assert "【資料歸屬】" in result["message"]
    assert "北醫禪學社" in result["message"]  # 點名研究對象，不得冒充
    assert "淡江內部資料" in result["message"]


def test_search_knowledge_warns_when_query_names_external_school():
    from app.tools.knowledge import search_knowledge

    result = search_knowledge("政大 茶會 文宣")
    if result.get("hit_count"):
        assert "沒有" in result["message"] and "其他學校" in result["message"]


def test_scope_tool_guard_blocks_both_directions():
    from app.orchestrator import _scope_tool_guard

    external = E.ResearchScope(mode=E.ResearchMode.EXTERNAL, target_schools=["國立政治大學"])
    internal = E.ResearchScope(mode=E.ResearchMode.INTERNAL)

    blocked = _scope_tool_guard(external, "search_knowledge")
    assert blocked is not None and blocked["code"] == "scope_blocked"
    blocked = _scope_tool_guard(internal, "search_social_references")
    assert blocked is not None and blocked["code"] == "scope_blocked"
    assert _scope_tool_guard(internal, "search_knowledge") is None
    assert _scope_tool_guard(external, "search_social_references") is None


# ── C. registry 單一來源化（全名與詞界）──────────────────────

def test_external_intent_recognizes_full_school_names():
    assert has_external_intent("研究政治大學的社團")
    assert has_external_intent("成功大學領袖社怎麼經營")
    assert has_external_intent("政大呢")


def test_external_intent_not_triggered_by_swallowed_aliases():
    assert not has_external_intent("挑戰營最後要完成大合照")
    assert not has_external_intent("老師大概會晚到")
    assert not has_external_intent("在行政大樓前集合")


def test_route_swallowed_alias_stays_internal():
    r = route("幫我把完成大合照排進挑戰營細流")
    assert r.skill.name != "social_research"


# ── EXTERNAL 模式全面改走研究路由 ───────────────────────────

def test_external_event_planning_message_reroutes_to_research():
    """「北醫禪學社的茶會怎麼做」不得留在 event_planning 拿淡江範本假裝北醫做法。"""
    state, routing = planner.understand("北醫禪學社的茶會怎麼做")
    assert state.research_mode == "external"
    assert routing.skill.name == "social_research"
    assert not state.artifacts_expected


# ── D. 狀態式澄清（詳見 test_entity_resolution）──────────────

def test_resolve_requires_awaiting_for_markers():
    res = E.resolve("我不確定政大是哪個社團")
    assert not res.clarified
    res = E.resolve("我不確定政大是哪個社團", awaiting_clarification=True)
    assert res.clarified


# ── E. 續接時重新解析研究對象 ────────────────────────────────

def test_continue_previous_keeps_intent():
    prev, _ = planner.understand("幫我做一份期初茶會企劃書")
    state, _ = planner.continue_previous("接續剛才", prev)
    assert state.intent == prev.intent  # 不被「接續剛才」覆寫（稽核漏洞 10）


def test_continue_previous_updates_research_target():
    prev, _ = planner.understand("研究北醫禪學社的 IG")
    state, _ = planner.continue_previous("接續剛才，改研究世新禪學社", prev)
    assert state.target_entities == ["shu-zen"]


# ── 結構化研究欄位 ───────────────────────────────────────────

def test_requested_mode_internal_overrides_message():
    state, _ = planner.understand("研究政大的茶會", requested={"mode": "internal"})
    assert state.research_mode == "internal"
    assert not state.clarification_pending


def test_requested_school_creates_clarification():
    state, _ = planner.understand("幫我研究他們的茶會", requested={"school": "政大"})
    assert state.target_schools == ["國立政治大學"]
    assert state.clarification_pending


def test_requested_entity_id_resolves_directly():
    state, _ = planner.understand("幫我研究他們的 IG", requested={"entity_id": "tmu-zen"})
    assert state.target_entities == ["tmu-zen"]
    assert not state.clarification_pending


# ── 事實隔離與快取鍵 ─────────────────────────────────────────

def test_extract_facts_skips_external_school_facts():
    facts = memory_service.extract_facts("北科的社長是王小明")
    assert facts == {}
    facts = memory_service.extract_facts("我們社長是林小華")
    assert facts.get("社長") == "林小華"


def test_retrieval_cache_key_varies_with_scope():
    q = ["期初茶會"]
    internal = memory_service.retrieval_cache_key(q, "knowledge", None)
    external = memory_service.retrieval_cache_key(q, "knowledge", _external_scope_dict())
    assert internal != external


# ── 本學期資料歸屬聲明 ───────────────────────────────────────

def test_current_term_prompt_block_declares_ownership(monkeypatch, tmp_path):
    from app.services import current_term as term

    yaml_path = tmp_path / "current_term.yaml"
    yaml_path.write_text(
        "academic_year: '115'\nsemester: 上學期\npresident: 測試社長\n", encoding="utf-8",
    )
    monkeypatch.setattr(term.config, "CURRENT_TERM_FILE", yaml_path)
    term.invalidate()
    block = term.load().prompt_block()
    assert "僅屬淡江大學領袖禪學社" in block
    term.invalidate()


# ── ingest：跨校名冊與全格式防線 ─────────────────────────────

def test_cross_school_roster_rows_detected():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "ingest_drive", Path(__file__).resolve().parent.parent / "scripts" / "ingest_drive.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    rows = [
        "總召 | 國立臺灣大學領袖社 | 某某某 | 社長 | 已邀約",
        "顧問 | 國立政治大學領袖社 | 某某某 | 師資 | 已邀約",
        "組輔長 | 國立臺北科技大學禪心領袖社 | 某某某 | 社長 | 已邀約",
        "場務組長 | 輔仁大學禪學社 | 某某某 | 社長 | 已邀約",
        "生活組長 | 淡江大學領袖禪學社 | 某某某 | 家族長 | 已邀約",
        "生活組員 | 臺北醫學大學禪學社 | 某某某 | 家族長 | 已邀約",
    ]
    assert mod.rows_look_like_roster(rows)

    text, note = mod._guard_flat_text("\n".join(rows), False)
    assert "未擷取" in text and note == "僅欄位結構"

    # 一般活動內文即使提到大學也不是名冊
    prose = [f"第 {i} 段：我們與其他大學的交流心得，內容都是活動紀錄。" for i in range(10)]
    kept, note2 = mod._guard_flat_text("\n".join(prose), False)
    assert note2 == "" and kept


def test_plus886_phone_is_redacted():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "ingest_drive2", Path(__file__).resolve().parent.parent / "scripts" / "ingest_drive.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = mod.redact("聯絡 +886-912-345-678 或 +886912345678")
    assert "912" not in out and "［手機］" in out


# ── 知識庫資料：跨校名冊已離開語料 ───────────────────────────

def test_knowledge_corpus_has_no_cross_school_roster_rows():
    """實際語料檢查：不得存在「外校名＋人名」的名冊列（稽核漏洞 54）。"""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "knowledge"
    pattern = re.compile(r"(政治大學|臺灣大學|師範大學|宜蘭大學)[^\n|]*\|\s*[一-鿿]{2,4}\s*\|")
    offenders = []
    for path in root.rglob("*.md"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in pattern.finditer(text):
            line = text[max(0, m.start() - 40): m.end() + 40].replace("\n", " ")
            offenders.append(f"{path.name}: {line[:100]}")
    assert not offenders, offenders
