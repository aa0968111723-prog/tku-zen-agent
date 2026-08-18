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
