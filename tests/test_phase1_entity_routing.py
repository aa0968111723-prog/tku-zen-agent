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


def test_artifact_request_with_ne_suffix_is_not_a_followup():
    """grok 反例：「幫我寫一份社課企劃書呢」是新的淡江產檔任務，
    不能因為語尾「呢」被鎖回外校研究。"""
    prev = _previous_awaiting_zhengda()
    state, routing = planner.understand("幫我寫一份社課企劃書呢", previous=prev)
    assert state.research_mode == "internal"
    assert routing.skill.name != "social_research"
    assert state.artifacts_expected


def test_womens_sentence_is_home_not_followup():
    """grok 反例：「那我們的茶會呢」講的是本社，不繼承外校 scope。"""
    prev_state, _ = planner.understand("研究北醫禪學社的 IG")
    state, _ = planner.understand("那我們的茶會呢", previous=prev_state)
    assert state.research_mode == "internal"


def test_artifact_for_counterpart_becomes_comparative():
    """「幫我寫給對方的邀請函」：淡江的產出＋外校語境 → 比較分析、保留產檔。"""
    prev_state, _ = planner.understand("研究北醫禪學社的 IG")
    prev_state.completion_status = "in_progress"
    state, routing = planner.understand("幫我寫給對方的邀請函", previous=prev_state)
    assert state.research_mode == "comparative"
    assert state.target_entities == ["tmu-zen"]
    assert state.artifacts_expected, "比較模式的產檔請求不得被改走純研究"


def _completed_tmu_research() -> OrchestrationState:
    state, _ = planner.understand("幫我研究北醫禪學社的IG經營")
    state.completion_status = "completed"
    return state


def test_internal_questions_after_completed_research_not_hijacked():
    """對抗審查 P0：外校研究完成後，內部問題不得被外部 scope 劫持。"""
    prev = _completed_tmu_research()
    for msg in ("今年社費多少呢", "接下來我該做什麼呢？", "幫我列出幹部他們的分工"):
        state, _ = planner.understand(msg, previous=prev)
        assert state.research_mode == "internal", msg
        assert not state.metrics.get("scope_inherited"), msg


def test_awaiting_internal_fact_question_gets_internal_answer():
    """awaiting 期間問「今年社費多少呢」→ 內部回答，不吞進政大反問。"""
    prev = _previous_awaiting_zhengda()
    state, _ = planner.understand("今年社費多少呢", previous=prev)
    assert state.research_mode == "internal"
    assert not state.clarification_pending


def test_strong_pronoun_after_completed_research_still_inherits():
    """「他們的招生貼文呢」在研究完成後仍是合法 follow-up。"""
    prev = _completed_tmu_research()
    state, _ = planner.understand("那他們的招生貼文呢", previous=prev)
    assert state.target_entities == ["tmu-zen"]


def test_compare_them_with_us_becomes_comparative():
    """對抗審查發現 6：「比較他們跟本社的差異」→ 比較分析，不是純內部。"""
    prev = _completed_tmu_research()
    state, _ = planner.understand("幫我比較他們跟本社的差異", previous=prev)
    assert state.research_mode == "comparative"
    assert state.target_entities == ["tmu-zen"]


def test_jinxing_zhengda_is_still_zhengda():
    """對抗審查發現 1：「進行政大…研究」的政大不能被「行」黑名單吞掉。"""
    state, _ = planner.understand("我想進行政大禪學社社課的研究")
    assert state.target_schools == ["國立政治大學"]
    assert state.clarification_pending
    # 行政大樓仍不誤觸
    assert not has_external_intent("在行政大樓前集合")


def test_taipei_hospital_is_not_tmu():
    """對抗審查發現 4：「台北醫院」是醫院，不是北醫禪學社。"""
    state, routing = planner.understand("幫我寫一份社員住進台北醫院的慰問公告")
    assert state.research_mode == "internal"
    assert routing.skill.name != "social_research"


def test_generic_external_phrases_get_external_scope():
    """對抗審查發現 7：「研究其他大學的禪學社」要走 external scope，
    不能 skill 是研究、scope 卻停在 internal（工具會被自家閘門擋死）。"""
    for msg in ("幫我研究其他大學的禪學社怎麼經營 IG", "研究跨校社團的招生做法", "幫我分析大專院校的社團經營"):
        state, routing = planner.understand(msg)
        if routing.skill.name == "social_research":
            assert state.research_mode in {"external", "comparative"}, msg


def test_restart_with_new_target_rebuilds_task():
    """對抗審查發現 2：續接換研究對象時整個任務圖重建，
    不得沿用淡江產檔管線做「北醫茶會」文件。"""
    prev, _ = planner.understand("幫我寫期初茶會的活動企劃書")
    state, routing = planner.continue_previous("取消，重新執行這個任務，改成研究北醫禪學社的茶會", prev)
    assert state.research_mode in {"external", "comparative"}
    if state.research_mode == "external":
        assert routing.skill.name == "social_research"
        assert not state.artifacts_expected


def test_awaiting_unrelated_uncertain_is_not_clarified():
    """對抗審查發現 5：awaiting 期間講「我還不確定活動日期」不算澄清回覆。"""
    res = E.resolve("我還不確定活動日期，但先幫我查政大的社團", awaiting_clarification=True)
    assert not res.clarified
    assert res.needs_clarification


def test_requested_external_with_pronoun_inherits():
    """對抗審查發現 8：表單選外校研究＋代名詞句 → 繼承，不得退回內部。"""
    prev = _previous_awaiting_zhengda()
    state, _ = planner.understand("那他們的茶會呢", previous=prev, requested={"mode": "external"})
    assert state.research_mode == "external"
    assert state.target_schools == ["國立政治大學"]
    assert state.clarification_pending


def test_requested_external_without_any_target_asks():
    """表單選外校研究但沒有任何對象 → 反問，不得靜默退回內部。"""
    state, _ = planner.understand("幫我看看茶會怎麼辦比較好", requested={"mode": "external"})
    assert state.clarification_pending


def test_requested_internal_with_external_mention_asks_conflict():
    """對抗審查發現 11：內部模式＋訊息點名政大 → 衝突確認卡，不靜默清空。"""
    state, _ = planner.understand("政大的茶會都怎麼辦？", requested={"mode": "internal"})
    assert state.research_mode == "internal"
    assert state.clarification_pending
    assert state.metrics.get("mode_conflict_school") == "國立政治大學"


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


def test_external_intent_not_triggered_by_next_char_swallow():
    """grok 反例：後一個字接走簡稱——成大事、台北醫院、台北藝術節。"""
    assert not has_external_intent("幫我寫社長致詞，主題是希望社員成大事")
    assert not has_external_intent("明天下午在台北醫院集合，幫我寫行前通知")
    assert not has_external_intent("幫我寫台北藝術節志工招募文宣")
    # 正常稱呼仍要觸發
    assert has_external_intent("北醫呢")
    assert has_external_intent("成大領袖社的社課")
    assert has_external_intent("北藝禪學社怎麼經營 IG")


def test_external_mode_blocks_get_current_term():
    """grok 反例：外校研究回合不得拿到淡江本學期社長／社課時間。"""
    from app.orchestrator import _scope_tool_guard

    external = E.ResearchScope(mode=E.ResearchMode.EXTERNAL, target_entities=["tmu-zen"])
    blocked = _scope_tool_guard(external, "get_current_term")
    assert blocked is not None and blocked["code"] == "scope_blocked"
    comparative = E.ResearchScope(mode=E.ResearchMode.COMPARATIVE, target_entities=["tmu-zen"])
    assert _scope_tool_guard(comparative, "get_current_term") is None


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
    """內部模式覆寫：訊息沒點名外校時直接生效、不反問。
    （點名外校的衝突情境見 test_requested_internal_with_external_mention_asks_conflict）"""
    state, _ = planner.understand("研究禪學社的茶會怎麼辦", requested={"mode": "internal"})
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


def test_extract_facts_comma_clause_isolation():
    """grok 反例：同一句話裡逗號隔開的淡江事實不能被外校名整筆丟棄。"""
    facts = memory_service.extract_facts("我們社長是林小華，北科的社長是王小明")
    assert facts.get("社長") == "林小華"
    facts = memory_service.extract_facts("北科的社長是王小明，我們社長是林小華")
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
