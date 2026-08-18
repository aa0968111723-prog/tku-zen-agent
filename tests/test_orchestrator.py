"""Phase 3 + 6：Orchestrator 狀態機與 verify/repair 迴圈（端到端，用假模型）。"""

from __future__ import annotations

import pytest

from app.orchestrator import planner
from app.orchestrator.state import Stage, TaskType
from app.services import current_term as term
from app.services.context import RequestContext
from tests.fakes import FakeLLM, say, tool


@pytest.fixture()
def ctx(tmp_db):
    uid = tmp_db.ensure_user("u_o", is_local=True)
    return RequestContext(user_id=uid, session_id=tmp_db.create_session(uid))


async def run(orch, monkeypatch, script, message, ctx):
    fake = FakeLLM(script=script)
    monkeypatch.setattr(orch, "NvidiaClient", lambda **_: fake)
    events = [ev async for ev in orch.run_turn(ctx, message, destination="local")]
    return fake, events


def kinds(events):
    return [e["type"] for e in events]


# ── Understand / Plan ────────────────────────────────────────

def test_understand_classifies_and_plans():
    state, routing = planner.understand("幫我做期初茶會的企劃書")
    assert state.task_type == TaskType.EVENT_PLANNING
    assert state.selected_skill == "event_planning"
    assert state.stage == Stage.PLAN
    assert state.plan_steps
    assert "document" in state.artifacts_expected


def test_question_does_not_plan_artifacts():
    state, _ = planner.understand("青年領袖十二項特質是什麼")
    assert state.artifacts_expected == []


def test_required_facts_detected():
    state, _ = planner.understand("今年社長是誰")
    assert "president" in state.required_facts


def test_missing_facts_reflect_term_state(clean_term):
    term.update({"academic_year": "115"})
    state, _ = planner.understand("幫我做這學期的期初茶會企劃書")
    assert "academic_year" not in state.missing_facts
    assert "semester" in state.missing_facts


def test_retrieval_queries_include_playbook():
    state, _ = planner.understand("幫我做招生企劃")
    assert any("招生企劃" in q for q in state.retrieval_queries)


def test_verification_rules_match_task():
    _s, r = planner.understand("幫我寫招生 IG 貼文")
    rules = planner.verification_rules_for(r)
    assert "no_health_claims" in rules
    assert "external_tone" in rules


# ── 事件序列 ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_emits_full_structured_event_sequence(tmp_output_dir, monkeypatch, ctx, clean_term):
    from app import orchestrator as orch

    term.update({"academic_year": "115", "semester": "上學期", "president": "林小明"})
    _fake, events = await run(
        orch,
        monkeypatch,
        [
            tool(
                "create_document",
                filename="期初茶會企劃書",
                markdown="# 期初茶會企劃書\n\n## 目標\n\n六十人出席，三十人入社。\n\n## 流程\n\n報到、體驗禪、手作、社團介紹。",
            ),
            say("做好了。"),
        ],
        "幫我做期初茶會的企劃書",
        ctx,
    )
    seq = kinds(events)
    for expected in [
        "task_understood", "plan_created", "retrieval_started", "retrieval_result",
        "tool_started", "tool_completed", "verification_started", "verification_result",
        "artifact_ready", "task_completed",
    ]:
        assert expected in seq, f"缺少 {expected} 事件：{seq}"
    assert seq.index("task_understood") < seq.index("plan_created") < seq.index("retrieval_started")


@pytest.mark.asyncio
async def test_retrieval_happens_before_model_is_called(tmp_output_dir, monkeypatch, ctx):
    """檢索要先做完再進 system prompt，不是等模型自己想到要查。"""
    from app import orchestrator as orch

    fake, _events = await run(orch, monkeypatch, [say("好")], "幫我做期初茶會企劃書", ctx)
    prompt = fake.system_prompt()
    assert "## 檢索結果" in prompt
    assert "歷年範例" in prompt or "任務劇本" in prompt or "社團知識庫" in prompt


@pytest.mark.asyncio
async def test_prompt_carries_current_term_and_priority(tmp_output_dir, monkeypatch, ctx, clean_term):
    from app import orchestrator as orch

    term.update({"academic_year": "115", "president": "林小明"})
    fake, _ = await run(orch, monkeypatch, [say("好")], "幫我做企劃", ctx)
    prompt = fake.system_prompt()
    assert "林小明" in prompt
    assert "本學期真實資料" in prompt
    assert "優先順序" in prompt


@pytest.mark.asyncio
async def test_unset_term_warns_model_not_to_guess(tmp_output_dir, monkeypatch, ctx, clean_term):
    from app import orchestrator as orch

    fake, events = await run(orch, monkeypatch, [say("好")], "幫我做這學期的社課排程", ctx)
    prompt = fake.system_prompt()
    assert "完全沒有設定" in prompt or "還沒設定" in prompt
    plan = next(e for e in events if e["type"] == "plan_created")
    assert plan["missing_facts"], "應該回報缺哪些今年的資料"


# ── verify / repair ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_bad_artifact_triggers_repair(tmp_output_dir, monkeypatch, ctx, clean_term):
    """產出有療效宣稱 → verify 擋下 → 自動要求修 → 第二次通過。"""
    from app import orchestrator as orch

    term.update({"academic_year": "115"})
    _fake, events = await run(
        orch,
        monkeypatch,
        [
            tool(
                "create_document",
                filename="招生文案",
                markdown="# 招生文案\n\n## 目標\n\n禪定可以治療焦慮，保證成功。\n\n## 管道\n\n路宣與個接。",
            ),
            tool(
                "create_document",
                filename="招生文案",
                markdown="# 招生文案\n\n## 目標\n\n幫助放鬆、練習專注，讓腦袋休息。\n\n## 管道\n\n路宣與個接。",
            ),
            say("修好了。"),
        ],
        "幫我寫招生文案",
        ctx,
    )
    seq = kinds(events)
    assert "repair_started" in seq, seq
    results = [e for e in events if e["type"] == "verification_result"]
    assert results[0]["ok"] is False
    assert results[-1]["ok"] is True
    ready = [e for e in events if e["type"] == "artifact_ready"]
    assert ready and ready[-1]["verified"] is True


@pytest.mark.asyncio
async def test_repair_gives_up_after_two_attempts(tmp_output_dir, monkeypatch, ctx, clean_term):
    from app import orchestrator as orch

    term.update({"academic_year": "115"})
    bad = tool(
        "create_document",
        filename="招生文案",
        markdown="# 招生文案\n\n## 目標\n\n禪定可以治療焦慮。\n\n## 管道\n\n路宣。",
    )
    _fake, events = await run(orch, monkeypatch, [bad, bad, bad, say("盡力了")], "幫我寫招生文案", ctx)

    repairs = [e for e in events if e["type"] == "repair_started"]
    assert len(repairs) == orch.MAX_REPAIRS
    ready = [e for e in events if e["type"] == "artifact_ready"]
    assert ready and ready[-1].get("warning"), "放棄修正時要標記警告"


@pytest.mark.asyncio
async def test_clean_artifact_skips_repair(tmp_output_dir, monkeypatch, ctx, clean_term):
    from app import orchestrator as orch

    term.update({"academic_year": "115"})
    _fake, events = await run(
        orch,
        monkeypatch,
        [
            tool(
                "create_spreadsheet",
                filename="預算表",
                sheets=[{"name": "預算", "csv": "項目,單價,數量,小計\n海報,120,10,=B2*C2"}],
            ),
            say("好了"),
        ],
        "幫我做經費預算表",
        ctx,
    )
    assert "repair_started" not in kinds(events)
    assert [e for e in events if e["type"] == "artifact_ready"]


# ── 狀態持久化 ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_state_is_saved_for_resume(tmp_output_dir, monkeypatch, ctx, tmp_db):
    from app import orchestrator as orch
    from app.services import memory

    await run(orch, monkeypatch, [say("好")], "幫我做期初茶會企劃書", ctx)

    sess = tmp_db.get_session(ctx.session_id, ctx.user_id)
    pid = sess["project_id"]
    assert pid

    state = memory.load_state(tmp_db, pid)
    assert state is not None
    assert state.completion_status == "completed"
    assert state.selected_skill == "event_planning"


@pytest.mark.asyncio
async def test_messages_persist_across_turns(tmp_output_dir, monkeypatch, ctx, tmp_db):
    from app import orchestrator as orch

    await run(orch, monkeypatch, [say("第一輪")], "第一個問題", ctx)
    fake, _ = await run(orch, monkeypatch, [say("第二輪")], "第二個問題", ctx)

    sent = " ".join(str(m.get("content") or "") for m in fake.calls[0]["messages"])
    assert "第一個問題" in sent, "第二輪應該看得到第一輪的對話"


@pytest.mark.asyncio
async def test_artifact_events_never_leak_server_paths(tmp_output_dir, monkeypatch, ctx, clean_term):
    from app import orchestrator as orch

    term.update({"academic_year": "115"})
    _fake, events = await run(
        orch,
        monkeypatch,
        [
            tool(
                "create_spreadsheet",
                filename="表",
                sheets=[{"name": "A", "csv": "欄1,欄2\n1,2"}],
            ),
            say("好"),
        ],
        "幫我做一份表",
        ctx,
    )
    blob = str(events)
    assert str(tmp_output_dir) not in blob, "事件裡出現了伺服器絕對路徑"
    ready = [e for e in events if e["type"] == "artifact_ready"]
    assert ready and ready[0]["artifact_id"], "要給 artifact_id"
