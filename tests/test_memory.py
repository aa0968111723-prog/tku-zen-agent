"""Phase 7：Working Memory / Project State。

核心要求：長任務不因 history trim 遺失狀態。
"""

from __future__ import annotations

import pytest

from app.orchestrator.state import OrchestrationState, PlanStep, Stage, TaskType
from app.services import memory
from app.services.context import RequestContext


@pytest.fixture()
def project(tmp_db):
    uid = tmp_db.ensure_user("u_m", is_local=True)
    sid = tmp_db.create_session(uid)
    pid = tmp_db.create_project(uid, "期初茶會")
    tmp_db.bind_session_project(sid, pid)
    return tmp_db, uid, sid, pid


# ── 事實抽取 ─────────────────────────────────────────────────

@pytest.mark.parametrize(
    "text,key,value",
    [
        ("我們社長是林小明", "社長", "林小明"),
        ("社課時間是每週三晚上", "社課時間", "每週三晚上"),
        ("地點在 H116", "社課地點", "H116"),
        ("社費是每學期300元", "社費", "每學期300元"),
        ("預算上限 8000 元", "預算上限", "8000"),
        ("目標 60 人出席", "目標人數", "60"),
    ],
)
def test_extract_confirmed_facts(text, key, value):
    facts = memory.extract_facts(text)
    assert key in facts, f"{text} → {facts}"
    assert value in facts[key]


def test_questions_are_not_treated_as_facts():
    assert "社長" not in memory.extract_facts("今年社長是誰？")


def test_facts_persist_and_are_recalled(project):
    store, _uid, _sid, pid = project
    memory.remember_facts_from(store, pid, "這次目標 80 人，預算上限 12000 元")
    facts = memory.recall_facts(store, pid)
    assert facts["目標人數"] == "80"
    assert "12000" in facts["預算上限"]


def test_internal_keys_are_hidden_from_prompt_facts(project):
    store, _uid, _sid, pid = project
    state = OrchestrationState(intent="x", plan_steps=[PlanStep("a")])
    memory.save_state(store, pid, state)
    assert not any(k.startswith("__") for k in memory.recall_facts(store, pid))


# ── 狀態存取 ─────────────────────────────────────────────────

def test_state_round_trips(project):
    store, _uid, _sid, pid = project
    state = OrchestrationState(
        intent="幫我做期初茶會企劃",
        task_type=TaskType.EVENT_PLANNING,
        stage=Stage.EXECUTE,
        plan_steps=[PlanStep("查資料", done=True), PlanStep("建立文件")],
        missing_facts=["president"],
        artifacts_expected=["document"],
        tool_rounds=3,
    )
    memory.save_state(store, pid, state)

    loaded = memory.load_state(store, pid)
    assert loaded is not None
    assert loaded.intent == state.intent
    assert loaded.task_type == TaskType.EVENT_PLANNING
    assert loaded.stage == Stage.EXECUTE
    assert loaded.progress() == (1, 2)
    assert loaded.missing_facts == ["president"]
    assert loaded.tool_rounds == 3


def test_corrupt_state_does_not_crash(project):
    store, _uid, _sid, pid = project
    store.remember(pid, memory.STATE_KEY, "{ not json")
    assert memory.load_state(store, pid) is None


def test_state_survives_restart(project, tmp_path):
    from app.services.session_store import SessionStore

    store, _uid, _sid, pid = project
    memory.save_state(store, pid, OrchestrationState(intent="長任務", tool_rounds=5))
    path = store.path
    store.close()

    reopened = SessionStore(path)
    try:
        loaded = memory.load_state(reopened, pid)
        assert loaded and loaded.tool_rounds == 5
    finally:
        reopened.close()


# ── 壓縮 ─────────────────────────────────────────────────────

def test_no_compression_below_threshold(project):
    store, _uid, sid, pid = project
    for i in range(10):
        store.append_message(sid, {"role": "user", "content": f"訊息{i}"})
    assert memory.maybe_compress(store, sid, pid) is False


def test_compression_preserves_facts_and_artifacts(project):
    """規格要求：壓縮舊訊息成 summary，但不能丟 project facts。"""
    store, _uid, sid, pid = project
    memory.remember_facts_from(store, pid, "目標 60 人，預算上限 9000 元")
    memory.record_artifact_fact(store, pid, {"filename": "期初茶會企劃書.docx", "version": 1})

    for i in range(memory.COMPRESS_THRESHOLD + 5):
        store.append_message(sid, {"role": "user", "content": f"第{i}個要求：請幫我調整一下"})

    assert memory.maybe_compress(store, sid, pid) is True

    summary = store.recall(pid)[memory.SUMMARY_KEY]["value"]
    assert "期初茶會企劃書.docx" in summary
    assert "60" in summary
    assert "9000" in summary

    # 事實本身仍然獨立存在，不是只活在摘要裡
    facts = memory.recall_facts(store, pid)
    assert facts["目標人數"] == "60"


def test_build_messages_includes_summary(project):
    store, _uid, sid, pid = project
    store.remember(pid, memory.SUMMARY_KEY, "先前做過期初茶會企劃書")
    msgs = memory.build_messages(
        store, session_id=sid, project_id=pid, system_prompt="SYS", user_message="繼續"
    )
    joined = " ".join(str(m["content"]) for m in msgs)
    assert "先前做過期初茶會企劃書" in joined


def test_build_messages_trims_but_keeps_valid_shape(project):
    store, _uid, sid, pid = project
    for i in range(60):
        store.append_message(sid, {"role": "user", "content": f"u{i}"})
    msgs = memory.build_messages(
        store, session_id=sid, project_id=pid, system_prompt="SYS", user_message="最新"
    )
    assert msgs[0]["role"] == "system"
    assert msgs[-1]["content"] == "最新"
    # 截斷後開頭不可以是孤兒 tool 訊息
    body = [m for m in msgs if m["role"] not in {"system"}]
    assert body[0]["role"] != "tool"


def test_orphan_tool_message_is_dropped_after_trim(project):
    store, _uid, sid, pid = project
    store.append_message(sid, {"role": "user", "content": "start"})
    for i in range(memory.KEEP_RECENT_MESSAGES + 4):
        store.append_message(sid, {"role": "tool", "tool_call_id": f"c{i}", "name": "t", "content": "{}"})
    msgs = memory.build_messages(
        store, session_id=sid, project_id=pid, system_prompt="SYS", user_message="x"
    )
    first_non_system = next(m for m in msgs if m["role"] != "system")
    assert first_non_system["role"] != "tool"


# ── 專案綁定 ─────────────────────────────────────────────────

def test_session_reuses_its_project(tmp_db):
    uid = tmp_db.ensure_user("u_p", is_local=True)
    sid = tmp_db.create_session(uid)
    ctx = RequestContext(user_id=uid, session_id=sid)
    state = OrchestrationState(intent="做企劃")

    first = memory.ensure_project(tmp_db, ctx, state)
    second = memory.ensure_project(tmp_db, ctx, state)
    assert first == second, "同一個 session 應該綁同一個專案"


def test_artifact_history_enables_follow_up(project):
    """『幫我改上一份企劃』要找得到上一份是什麼。"""
    store, _uid, _sid, pid = project
    memory.record_artifact_fact(store, pid, {"filename": "企劃書.docx", "version": 1})
    memory.record_artifact_fact(store, pid, {"filename": "企劃書.docx", "version": 2})
    facts = memory.recall_facts(store, pid)
    assert "產出：企劃書.docx" in facts
    assert "版本 2" in facts["產出：企劃書.docx"]
