"""第二批深度功能：活動營運、衍生產出、模型成本與工具可靠性。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from app import main, tools
from app.llm import LLMError, Reply, ToolCall, select_model
from app.orchestrator import planner
from app.services import activities as domain
from app.services import context as ctx_mod
from app.services.context import RequestContext
from app.tools import activity, artifact as artifact_tool, document
from app.tools.social import _date_bounds, search_social_references
from tests.fakes import FakeLLM, say, tool


def test_activity_and_assignments_persist_and_are_user_scoped(tmp_db, tmp_path):
    owner = tmp_db.ensure_user("u_owner")
    stranger = tmp_db.ensure_user("u_stranger")
    project_id = tmp_db.create_project(owner, "期初茶會")
    record = tmp_db.create_activity(
        owner, "期初茶會", project_id=project_id, activity_type="茶會", start_at="2026-09-20",
    )
    task = tmp_db.create_activity_task(
        owner, record["id"], "確認場地", group_name="活動組", assignee="小明", due_at="2026-09-10",
    )

    assert tmp_db.get_activity(record["id"], stranger) is None
    assert tmp_db.get_activity_task(task["id"], stranger) is None
    path = tmp_db.path
    tmp_db.close()

    from app.services.session_store import SessionStore

    reopened = SessionStore(path)
    try:
        assert reopened.get_activity(record["id"], owner)["name"] == "期初茶會"
        assert reopened.list_activity_tasks(record["id"], owner)[0]["assignee"] == "小明"
    finally:
        reopened.close()


def test_readiness_reports_missing_unassigned_overdue_and_next_deadline(tmp_db):
    uid = tmp_db.ensure_user("u_readiness")
    record = tmp_db.create_activity(uid, "招生活動", activity_type="招生", start_at="2026-10-01")
    tmp_db.create_activity_task(uid, record["id"], "申請場地", due_at="2020-01-01", assignee="活動組")
    tmp_db.create_activity_task(uid, record["id"], "完成報名表", due_at="2099-01-01")
    tasks = tmp_db.list_activity_tasks(record["id"], uid)

    report = domain.readiness_report(
        record, tasks, output_type="網宣", now=datetime(2026, 8, 24, tzinfo=timezone.utc),
    )

    assert {item["key"] for item in report["missing_fields"]} == {"location", "audience", "signup_url"}
    assert report["counts"] == {"total": 2, "completed": 0, "remaining": 2, "overdue": 1, "unassigned": 1}
    assert report["overdue_tasks"][0]["title"] == "申請場地"
    assert report["next_deadline"]["title"] == "完成報名表"


def test_latest_artifact_query_deduplicates_before_limit(tmp_db, tmp_path):
    uid = tmp_db.ensure_user("u_versions")
    file_path = tmp_path / "artifact.docx"
    file_path.write_text("x", encoding="utf-8")
    for _ in range(35):
        tmp_db.record_artifact(user_id=uid, filename="企劃書.docx", local_path=str(file_path))
    tmp_db.record_artifact(user_id=uid, filename="簡報.pptx", local_path=str(file_path))

    latest = tmp_db.list_artifacts(uid, limit=30)
    assert {item.filename for item in latest} == {"企劃書.docx", "簡報.pptx"}
    assert next(item for item in latest if item.filename == "企劃書.docx").version == 35


def test_current_term_fingerprint_changes_when_term_changes(clean_term):
    from app.services import current_term

    before = current_term.fingerprint()
    current_term.update({"academic_year": "115", "president": "林小明"})
    after = current_term.fingerprint()
    assert before != after


def test_iso_date_range_keeps_date_hyphens_and_filters_sources():
    assert _date_bounds("2026-01-01~2026-08-24") == ("2026-01-01", "2026-08-24")
    included = search_social_references("招生", schools="北藝", date_range="2026-01-01~2026-08-24")
    excluded = search_social_references("招生", schools="北藝", date_range="2025-01-01~2025-12-31")
    assert included["references"]
    assert excluded["references"] == []


def test_activity_tools_are_idempotent_and_artifact_keeps_activity_lineage(tmp_db, tmp_output_dir):
    uid = tmp_db.ensure_user("u_tools", is_local=True)
    project_id = tmp_db.create_project(uid, "期初茶會")
    session_id = tmp_db.create_session(uid, project_id=project_id)
    ctx = RequestContext(user_id=uid, session_id=session_id, project_id=project_id)

    with ctx_mod.use(ctx):
        first = activity.create_activity(name="期初茶會", activity_type="茶會", location="待填")
        second = activity.create_activity(name="期初茶會", activity_type="茶會")
        task_result = activity.add_activity_task(
            title="確認場地", activity_name="期初茶會", group_name="活動組", assignee="小明",
        )
        artifact = document.create_document(
            filename="期初茶會企劃書", markdown="# 企劃書\n\n## 目標\n\n凝聚新生。\n\n## 流程\n\n報到與交流。",
        )

    assert first["activity_id"] == second["activity_id"]
    assert task_result["task"]["assignee"] == "小明"
    saved = tmp_db.get_artifact(artifact["artifact_id"], uid)
    assert saved.meta["activity_id"] == first["activity_id"]
    assert tmp_db.list_activities(uid, project_id=project_id)[0]["id"] == first["activity_id"]

    other = tmp_db.ensure_user("u_tools_other")
    with ctx_mod.use(RequestContext(user_id=other, project_id=project_id)):
        denied = artifact_tool.read_artifact(artifact["artifact_id"])
    assert denied["ok"] is False
    assert denied["code"] == "artifact_not_found"


def test_activity_api_supports_crud_readiness_and_user_scope(tmp_db, monkeypatch):
    from fastapi.testclient import TestClient
    from app import config

    monkeypatch.setattr(config, "AUTH_MODE", "local")
    uid = tmp_db.ensure_user("u_local", is_local=True)
    client = TestClient(main.app)
    created = client.post(
        "/api/activities",
        json={"name": "期初茶會", "activity_type": "茶會", "start_at": "2026-09-20"},
    )
    assert created.status_code == 200
    activity_id = created.json()["id"]

    added = client.post(
        f"/api/activities/{activity_id}/tasks",
        json={"title": "確認教室", "group_name": "活動組", "due_at": "2020-01-01"},
    )
    assert added.status_code == 200
    task_id = added.json()["id"]
    patched = client.patch(
        f"/api/activities/{activity_id}/tasks/{task_id}",
        json={"assignee": "小華", "status": "進行中"},
    )
    assert patched.status_code == 200
    status = client.get(f"/api/activities/{activity_id}?output_type=企劃書").json()
    assert status["readiness"]["counts"]["overdue"] == 1
    assert status["tasks"][0]["assignee"] == "小華"

    stranger = tmp_db.ensure_user("u_other")
    assert tmp_db.get_activity(activity_id, stranger) is None
    assert uid != stranger


def test_activity_queries_route_to_scoped_operational_tools():
    state, routing = planner.understand("這場活動目前缺什麼")
    assert routing.skill.name == "activity_management"
    assert routing.tool_names() == ("search_knowledge", "get_current_term", "get_activity_status")
    assert state.artifacts_expected == []

    _state, list_routing = planner.understand("列出活動")
    assert list_routing.tool_names() == ("search_knowledge", "get_current_term", "list_activities")

    plan_state, plan_routing = planner.understand("將活動資料產生企劃書")
    assert plan_routing.skill.name == "event_planning"
    assert "get_activity_status" in plan_routing.tool_names()
    assert [step.kind for step in plan_state.plan_steps[:3]] == ["retrieval", "activity", "artifact"]


def test_model_policy_uses_economy_for_queries_and_strong_for_outputs():
    simple = select_model(message="社費多少？", task_type="knowledge", needs_artifact=False)
    complex_task = select_model(message="研究後產生完整企劃書", task_type="composite", needs_artifact=True, composite=True)
    manual = select_model(message="查詢", task_type="knowledge", needs_artifact=False, explicit="custom/model")
    assert simple["tier"] == "economy"
    assert complex_task["tier"] == "strong"
    assert manual == {"model": "custom/model", "tier": "manual", "reason": "使用者指定模型"}

    previous, _routing = planner.understand("規劃期初茶會")
    previous.tool_rounds = 8
    continued, _routing = planner.continue_previous("接續剛才", previous)
    assert continued.tool_rounds == 0


@pytest.mark.asyncio
async def test_activity_workflow_persists_tasks_and_feeds_followup_artifact(
    tmp_db, tmp_output_dir, clean_term, monkeypatch,
):
    from app import orchestrator as orch
    from app.services import current_term

    current_term.update({"academic_year": "115", "semester": "上學期"})
    uid = tmp_db.ensure_user("u_activity_flow", is_local=True)
    sid = tmp_db.create_session(uid)
    ctx = RequestContext(user_id=uid, session_id=sid)
    first_model = FakeLLM(
        script=[
            Reply(tool_calls=[ToolCall(
                id="c_create_activity", name="create_activity", arguments={
                    "name": "期初茶會", "activity_type": "茶會", "location": "待填",
                    "tasks": [
                        {"title": "確認教室", "group_name": "活動組", "assignee": "小華", "due_at": "2026-09-10"},
                        {"title": "完成報名表", "group_name": "文書組"},
                    ],
                },
            )]),
            tool(
                "create_document", filename="期初茶會企劃書",
                markdown="# 期初茶會企劃書\n\n## 目標\n\n凝聚新生。\n\n## 流程\n\n報到、交流與社團介紹。\n\n日期：待填\n地點：待填",
            ),
            say("活動與企劃書已建立。"),
        ]
    )
    monkeypatch.setattr(orch, "NvidiaClient", lambda **_: first_model)
    first_events = [
        event async for event in orch.run_turn(ctx, "幫我規劃期初茶會企劃書", destination="local")
    ]
    assert any(event["type"] == "task_completed" for event in first_events)
    project_id = tmp_db.get_session(sid, uid)["project_id"]
    saved_activity = tmp_db.list_activities(uid, project_id=project_id)[0]
    assert len(tmp_db.list_activity_tasks(saved_activity["id"], uid)) == 2
    first_artifact = next(event for event in first_events if event["type"] == "artifact_ready")
    assert first_artifact["activity_id"] == saved_activity["id"]

    second_model = FakeLLM(
        script=[
            tool("read_artifact", artifact_id=first_artifact["artifact_id"]),
            tool(
                "create_slides", filename="期初茶會簡報", title="期初茶會",
                slides_markdown="## 活動目標\n- 凝聚新生\n\n## 活動資訊\n- 日期：待填\n- 地點：待填",
            ),
            say("已沿用活動資料轉成簡報。"),
        ]
    )
    monkeypatch.setattr(orch, "NvidiaClient", lambda **_: second_model)
    second_events = [
        event async for event in orch.run_turn(ctx, "把上一份改成簡報", destination="local")
    ]
    assert "## 目前活動的正式資料" in second_model.system_prompt()
    assert "確認教室" in second_model.system_prompt()
    assert any("凝聚新生" in str(result.get("content") or "") for result in second_model.tool_results())
    second_artifact = next(event for event in second_events if event["type"] == "artifact_ready")
    assert second_artifact["activity_id"] == saved_activity["id"]


@pytest.mark.asyncio
async def test_duplicate_read_tool_calls_are_reused_and_usage_is_saved(tmp_db, monkeypatch):
    from app import orchestrator as orch

    uid = tmp_db.ensure_user("u_cache_tool", is_local=True)
    sid = tmp_db.create_session(uid)
    ctx = RequestContext(user_id=uid, session_id=sid)
    fake = FakeLLM(
        script=[
            tool("search_knowledge", query="青年領袖十二項特質"),
            tool("search_knowledge", query="青年領袖十二項特質"),
            Reply(content="完成", raw={"usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}}),
        ]
    )
    monkeypatch.setattr(orch, "NvidiaClient", lambda **_: fake)
    events = [event async for event in orch.run_turn(ctx, "青年領袖十二項特質是什麼", destination="local")]

    completed = [event for event in events if event["type"] == "tool_completed"]
    assert [event["cached"] for event in completed] == [False, True]
    summary = next(event for event in events if event["type"] == "task_completed")["summary"]
    assert summary["metrics"]["tool_requests"] == 2
    assert summary["metrics"]["tool_calls"] == 1
    assert summary["metrics"]["tool_cache_hits"] == 1
    assert summary["metrics"]["total_tokens"] == 120
    assert summary["metrics"]["model_tier"] == "economy"


@pytest.mark.asyncio
async def test_retryable_read_tool_failure_retries_once(tmp_db, monkeypatch):
    from app import orchestrator as orch

    uid = tmp_db.ensure_user("u_retry_tool", is_local=True)
    sid = tmp_db.create_session(uid)
    original = tools.dispatch
    attempts = {"count": 0}

    def flaky(name, arguments, **kwargs):
        if name == "search_knowledge" and attempts["count"] == 0:
            attempts["count"] += 1
            return {"ok": False, "code": "tool_failed", "message": "暫時失敗"}
        return original(name, arguments, **kwargs)

    monkeypatch.setattr(tools, "dispatch", flaky)
    monkeypatch.setattr(
        orch, "NvidiaClient",
        lambda **_: FakeLLM(script=[tool("search_knowledge", query="社團宗旨"), say("完成")]),
    )
    events = [
        event async for event in orch.run_turn(
            RequestContext(user_id=uid, session_id=sid), "社團宗旨是什麼", destination="local",
        )
    ]
    assert any(event["type"] == "tool_retrying" for event in events)
    completed = next(event for event in events if event["type"] == "tool_completed")
    assert completed["ok"] is True
    assert completed["attempts"] == 2


@pytest.mark.asyncio
async def test_tool_timeout_emits_classified_failure_and_alternative(tmp_db, monkeypatch):
    import time
    from app import config
    from app import orchestrator as orch

    uid = tmp_db.ensure_user("u_timeout_tool", is_local=True)
    sid = tmp_db.create_session(uid)

    def slow_dispatch(_name, _arguments, **_kwargs):
        time.sleep(0.05)
        return {"ok": True, "message": "太晚完成"}

    monkeypatch.setattr(config, "TOOL_TIMEOUT_SECONDS", 0.005)
    monkeypatch.setattr(tools, "dispatch", slow_dispatch)
    monkeypatch.setattr(
        orch, "NvidiaClient",
        lambda **_: FakeLLM(script=[tool("search_knowledge", query="社團宗旨"), say("已改用既有檢索內容回答")]),
    )
    events = [
        event async for event in orch.run_turn(
            RequestContext(user_id=uid, session_id=sid), "社團宗旨是什麼", destination="local",
        )
    ]
    completed = next(event for event in events if event["type"] == "tool_completed")
    assert completed["ok"] is False
    assert completed["error_code"] == "tool_timeout"
    assert "重試" in completed["alternative"]


@pytest.mark.asyncio
async def test_pause_during_model_call_is_not_overwritten(tmp_db, monkeypatch):
    from app import orchestrator as orch
    from app.services import memory
    from app.orchestrator.state import WorkflowStatus

    started = asyncio.Event()
    release = asyncio.Event()

    class WaitingModel:
        model = "waiting/model"

        async def chat(self, *_args, **_kwargs):
            started.set()
            await release.wait()
            return Reply(content="不應繼續交付")

    monkeypatch.setattr(orch, "NvidiaClient", lambda **_: WaitingModel())
    uid = tmp_db.ensure_user("u_pause_race", is_local=True)
    sid = tmp_db.create_session(uid)

    async def collect():
        return [
            event async for event in orch.run_turn(
                RequestContext(user_id=uid, session_id=sid), "規劃期初茶會", destination="local",
            )
        ]

    running = asyncio.create_task(collect())
    await asyncio.wait_for(started.wait(), timeout=2)
    project_id = tmp_db.get_session(sid, uid)["project_id"]
    await main._control_task(project_id, "pause", uid)
    release.set()
    events = await running

    assert any(event["type"] == "task_paused" for event in events)
    assert not any(event["type"] == "task_completed" for event in events)
    assert memory.load_state(tmp_db, project_id).workflow_status == WorkflowStatus.PAUSED


@pytest.mark.asyncio
async def test_model_failure_marks_a_retryable_step(tmp_db, monkeypatch):
    from app import orchestrator as orch
    from app.services import memory

    class BrokenModel:
        model = "broken/model"

        async def chat(self, *_args, **_kwargs):
            raise LLMError("暫時無法使用")

    monkeypatch.setattr(orch, "NvidiaClient", lambda **_: BrokenModel())
    uid = tmp_db.ensure_user("u_model_retry", is_local=True)
    sid = tmp_db.create_session(uid)
    events = [
        event async for event in orch.run_turn(
            RequestContext(user_id=uid, session_id=sid), "規劃期初茶會", destination="local",
        )
    ]
    assert any(event["type"] == "error" for event in events)
    project_id = tmp_db.get_session(sid, uid)["project_id"]
    state = memory.load_state(tmp_db, project_id)
    assert any(step.status == "failed" for step in state.plan_steps)
    retried = await main._control_task(project_id, "retry", uid)
    assert retried["action"] == "retry"
    assert not any(step["status"] == "failed" for step in retried["steps"])
