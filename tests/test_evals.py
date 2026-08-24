"""把 evals 掛進 pytest，讓核心 evals 可以自動跑（規格要求）。

完整報表用 `python -m evals`；這裡只驗關鍵門檻，讓 CI 一旦退步就紅。
"""

from __future__ import annotations

import pytest

from evals import cases as eval_cases
from evals.runner import run_all


@pytest.fixture(scope="module")
def summary():
    import asyncio

    return asyncio.run(run_all())


def test_at_least_fifty_scenarios():
    assert len(eval_cases.CASES) >= 50


def test_spec_mandatory_cases_are_covered():
    """規格點名的 10 個必測案例，前 7 個是情境（後 3 個是失敗模式，在
    test_failure_modes.py）。"""
    ids = {c.id for c in eval_cases.CASES}
    for cid in ["plan-01", "cur-01", "rec-02", "hist-01", "eval-01", "plan-02", "dest-01"]:
        assert cid in ids, f"缺少必測案例 {cid}"


def test_zero_hallucination(summary):
    """關鍵門檻：current facts hallucination = 0。"""
    bad = summary.hallucinations()
    assert not bad, "\n".join(f"{r.case_id}: {r.notes}" for r in bad)


def test_no_fabrication_when_term_unset(summary):
    """不可因找不到今年資料而偷填歷史資料。"""
    grounding_cases = [r for r in summary.results if r.case_id.startswith("cur-")]
    assert grounding_cases
    for r in grounding_cases:
        assert r.scores["hallucination"] == 1.0, f"{r.case_id}: {r.notes}"


def test_skill_routing_accuracy(summary):
    assert summary.dimension("skill_routing") >= 0.95, summary.report()


def test_intent_accuracy(summary):
    assert summary.dimension("intent") >= 0.95, summary.report()


def test_tool_selection_accuracy(summary):
    assert summary.dimension("tool_selection") >= 0.9, summary.report()


def test_retrieval_relevance(summary):
    assert summary.dimension("retrieval_relevance") >= 0.9, summary.report()


def test_task_completion(summary):
    assert summary.dimension("task_completion") >= 0.95, summary.report()


def test_artifact_validity(summary):
    """artifact tool execution success rate 要可量測且夠高。"""
    assert summary.dimension("artifact_validity") >= 0.95, summary.report()


def test_grounding(summary):
    assert summary.dimension("grounding") >= 0.9, summary.report()


def test_overall_pass_rate(summary):
    assert summary.pass_rate() >= 0.9, summary.report()


def test_latency_is_reasonable(summary):
    """不含真實模型呼叫的框架開銷。"""
    assert summary.avg_latency_ms() < 2000, f"平均 {summary.avg_latency_ms():.0f} ms"


def test_evals_run_without_api_key(monkeypatch):
    """核心 evals 不可以依賴付費外部 API。"""
    import asyncio

    from app import config

    monkeypatch.setattr(config, "NVIDIA_API_KEY", "")
    result = asyncio.run(run_all(eval_cases.MUST_TEST))
    assert result.results
    assert not result.hallucinations()


# ── 政大類資料混淆防護（對抗稽核 44/63）──────────────────────


def test_research_event_flow(summary):
    """res-01~04 全數宣告事件流斷言，且必須全過。

    這批斷言驗證的是 SSE 事件流本身：「政大呢」要發 clarification_needed、
    不得檢索與產檔；查無來源要回報 no_reliable_source。任何維度平均
    都救不回 event_flow=0（硬門檻）。
    """
    res = [r for r in summary.results if r.case_id.startswith("res-")]
    assert len(res) >= 4
    for r in res:
        assert r.scores.get("event_flow", 1.0) == 1.0, f"{r.case_id}: {r.notes}"
    assert not summary.event_failures()


def test_research_cases_declare_event_assertions():
    """防止有人把 res 案例的事件斷言拿掉後 CI 依然綠。"""
    for cid in ("res-01", "res-02", "res-03", "res-04"):
        case = eval_cases.BY_ID[cid]
        assert case.expected_events, f"{cid} 必須宣告 expected_events"
    assert "clarification_needed" in eval_cases.BY_ID["res-02"].expected_events
    assert "artifact_ready" in eval_cases.BY_ID["res-02"].forbidden_events
    assert "research_status=no_reliable_source" in eval_cases.BY_ID["res-04"].expected_events


def test_school_attribution_scan_word_boundaries():
    """學校歸屬掃描沿用 entities.alias_mentioned 的詞界防護，不裸比子字串。"""
    from evals.runner import _external_school_mentions

    # 「完成大合照」不可誤判成「成大」；「行政大樓」不可誤判成「政大」
    assert "國立成功大學" not in _external_school_mentions("大家完成大合照後離場")
    assert "國立政治大學" not in _external_school_mentions("在行政大樓前集合")
    # 真的點名外校要抓得到（學校簡稱與正式社團名都算）
    assert "國立政治大學" in _external_school_mentions("政大的茶會流程如下")
    assert "國立臺北科技大學" in _external_school_mentions("北科禪心領袖社的招生文案")


def test_school_attribution_guard_flags_wrong_school():
    """案例沒點名的外校出現在回答裡 → 一定在允許範圍之外（歸屬幻覺）。"""
    from evals.runner import _allowed_schools, _external_school_mentions

    case = eval_cases.BY_ID["plan-01"]   # 「幫我做期初茶會企劃」——純內部案例
    allowed = _allowed_schools(case)
    assert allowed == set()              # 不允許提到任何外校
    mentioned = _external_school_mentions("政大禪學社的茶會通常會安排體驗禪")
    assert "國立政治大學" in mentioned
    assert not set(mentioned) <= allowed

    # res-01 有點名北科 → 北科在允許範圍內，政大不在
    res01_allowed = _allowed_schools(eval_cases.BY_ID["res-01"])
    assert "國立臺北科技大學" in res01_allowed
    assert "國立政治大學" not in res01_allowed


def test_clarification_gate_regression_alarm(monkeypatch):
    """防護退化警報：模擬 orchestrator 的反問閘門被移除，evals 必須變紅。

    不動 app/ 程式——用 monkeypatch 讓所有反問卡生成器都回 None，
    orchestrator 的閘門（app/orchestrator/__init__.py 的
    `if state.clarification_pending:` 區塊）就拿不到反問卡而直接放行，
    效果等同整段閘門被註解掉：流程會往下跑檢索與生成。

    此時 res-02「政大呢」不再發 clarification_needed，
    事件流斷言（硬門檻）必須把案例判失敗、把整套 evals 判紅。
    未來任何人改壞閘門，CI 都會在這裡抓到。
    """
    import asyncio

    from app.research import entities as research_entities

    monkeypatch.setattr(research_entities.EntityResolution, "clarification", lambda self: None)
    monkeypatch.setattr(research_entities, "clarification_for_school", lambda school: None)
    monkeypatch.setattr(research_entities, "mode_conflict_clarification", lambda school: None)
    monkeypatch.setattr(research_entities, "generic_clarification", lambda: None)

    broken = asyncio.run(run_all([eval_cases.BY_ID["res-02"]]))
    r = broken.results[0]
    assert r.scores.get("event_flow") == 0.0, f"閘門失效卻沒被抓到：{r.events}"
    assert not r.passed
    assert broken.event_failures(), "Summary.event_failures() 必須列出退化案例"
    # python -m evals 的紅綠邏輯：有 event_failures 就回傳非零
    assert any("clarification_needed" in n for n in r.notes)
