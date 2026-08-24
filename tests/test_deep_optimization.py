"""第一批功能深度優化的驗收：工作流、複合任務、來源追溯與版本鏈。"""

from __future__ import annotations

import pytest

from app import main, retrieval
from app.orchestrator import planner
from app.orchestrator.state import OrchestrationState, PlanStep, Stage, TaskType, WorkflowStatus
from app.services import memory
from app.services.context import RequestContext
from app.tools.social import search_social_references
from app.rag.conflicts import detect_conflicts
from app.rag.hybrid import Scored
from app.rag.metadata import ChunkMeta
from app.retrieval import Chunk
from tests.fakes import FakeLLM, say, tool


def test_composite_routing_has_explicit_dependency_graph():
    message = "研究其他學校招生方式後，幫我產生淡江招生輪播"
    state, routing = planner.understand(message)

    assert routing.task_sequence == ("social_research", "social_publicity")
    assert state.task_type.value == "composite"
    assert [s.kind for s in state.plan_steps[:3]] == ["research", "synthesis", "artifact"]
    assert state.plan_steps[1].depends_on == [state.plan_steps[0].step_id]
    assert "search_social_references" in routing.tool_names()
    assert "create_social_carousel" in routing.tool_names()

    slides_state, slides_routing = planner.understand("把上一份企劃改成簡報")
    assert slides_state.artifacts_expected == ["slides"]
    assert slides_routing.tool_names() == ("search_knowledge", "get_current_term", "create_slides")

    reels_state, reels_routing = planner.understand("將輪播改成 Reels 腳本")
    assert reels_state.artifacts_expected == ["document"]
    assert "create_reels_script" in reels_routing.tool_names()


def test_query_and_restart_phrases_keep_their_task_intent():
    query_state, query_routing = planner.understand("查詢本學期活動")
    assert query_routing.skill.name == "knowledge"
    assert query_state.artifacts_expected == []
    assert "create_document" not in query_routing.tool_names()

    restart_state, restart_routing = planner.understand("取消長任務後重新執行")
    assert restart_routing.skill.name == "knowledge"
    assert restart_state.artifacts_expected == []


def test_workflow_step_retry_preserves_completed_steps(tmp_db):
    uid = tmp_db.ensure_user("u_workflow")
    pid = tmp_db.create_project(uid, "研究後產生輪播")
    state = OrchestrationState(
        task_type=TaskType.COMPOSITE,
        plan_steps=[
            PlanStep("研究", done=True, kind="research"),
            PlanStep("產出", kind="artifact", status="failed", last_error="逾時", attempts=2),
        ],
        stage=Stage.FAILED,
        completion_status="failed",
        workflow_status=WorkflowStatus.FAILED,
    )
    memory.save_state(tmp_db, pid, state)

    loaded = memory.load_state(tmp_db, pid)
    assert loaded is not None
    assert loaded.reset_failed_steps() == 1
    assert loaded.plan_steps[0].status == "completed"
    assert loaded.plan_steps[1].status == "pending"
    assert loaded.plan_steps[1].attempts == 2


def test_task_control_api_is_user_scoped_and_reports_next_step(tmp_db, monkeypatch):
    from fastapi.testclient import TestClient
    from app import config

    monkeypatch.setattr(config, "AUTH_MODE", "local")
    uid = tmp_db.ensure_user("u_local", is_local=True)
    pid = tmp_db.create_project(uid, "可暫停任務")
    state = OrchestrationState(plan_steps=[PlanStep("查資料"), PlanStep("產出")])
    memory.save_state(tmp_db, pid, state)

    client = TestClient(main.app)
    paused = client.post(f"/api/tasks/{pid}/pause")
    assert paused.status_code == 200
    assert paused.json()["workflow_status"] == "paused"
    assert paused.json()["next_action"]

    resumed = client.post(f"/api/tasks/{pid}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["workflow_status"] == "in_progress"


def test_chat_sse_keeps_legacy_events_and_adds_workflow_state(tmp_db, monkeypatch, tmp_output_dir):
    from fastapi.testclient import TestClient
    from app import config, orchestrator as orch

    monkeypatch.setattr(config, "AUTH_MODE", "local")
    monkeypatch.setattr(orch, "NvidiaClient", lambda **_: FakeLLM(script=[say("可以")]))
    client = TestClient(main.app)
    with client.stream("POST", "/api/chat", json={"message": "青年領袖十二項特質是什麼"}) as response:
        assert response.status_code == 200
        payloads = [
            line[6:] for line in response.iter_lines()
            if line.startswith("data: ")
        ]
    events = [__import__("json").loads(payload) for payload in payloads]
    kinds = [event["type"] for event in events]
    assert kinds[0] == "session"
    assert "task_understood" in kinds
    assert "plan_created" in kinds
    assert "task_completed" in kinds
    assert kinds[-1] == "done"


@pytest.mark.asyncio
async def test_natural_language_controls_do_not_start_a_new_llm_task(tmp_db):
    from app import orchestrator as orch

    uid = tmp_db.ensure_user("u_command", is_local=True)
    sid = tmp_db.create_session(uid)
    pid = tmp_db.create_project(uid, "長任務")
    tmp_db.bind_session_project(sid, pid)
    memory.save_state(tmp_db, pid, OrchestrationState(plan_steps=[PlanStep("產出")]))
    ctx = RequestContext(user_id=uid, session_id=sid)
    events = [event async for event in orch.run_turn(ctx, "暫停", destination="local")]
    assert [event["type"] for event in events] == ["task_paused"]
    assert memory.load_state(tmp_db, pid).workflow_status == WorkflowStatus.PAUSED


@pytest.mark.asyncio
async def test_restart_phrase_reuses_previous_task_graph(tmp_db, monkeypatch):
    from app import orchestrator as orch

    uid = tmp_db.ensure_user("u_restart", is_local=True)
    sid = tmp_db.create_session(uid)
    pid = tmp_db.create_project(uid, "長任務")
    tmp_db.bind_session_project(sid, pid)
    previous, _ = planner.understand("期初茶會企劃書")
    previous.workflow_status = WorkflowStatus.CANCELLED
    memory.save_state(tmp_db, pid, previous)
    fake = FakeLLM(script=[say("已重新執行")])
    monkeypatch.setattr(orch, "NvidiaClient", lambda **_: fake)

    events = [
        event async for event in orch.run_turn(
            RequestContext(user_id=uid, session_id=sid),
            "取消長任務後重新執行",
            destination="local",
        )
    ]

    understood = next(event for event in events if event["type"] == "task_understood")
    assert understood["skill"] == "活動籌備"
    assert any(event["type"] == "task_completed" for event in events)


def test_artifact_list_exposes_latest_version_only_and_keeps_parent(tmp_db, tmp_path):
    uid = tmp_db.ensure_user("u_artifact")
    first_path = tmp_path / "plan-v1.docx"
    second_path = tmp_path / "plan-v2.docx"
    first_path.write_text("v1", encoding="utf-8")
    second_path.write_text("v2", encoding="utf-8")
    v1 = tmp_db.record_artifact(user_id=uid, filename="企劃書.docx", local_path=str(first_path))
    v2 = tmp_db.record_artifact(user_id=uid, filename="企劃書.docx", local_path=str(second_path))

    latest = tmp_db.list_artifacts(uid)
    history = tmp_db.list_artifacts(uid, include_history=True)
    assert len(latest) == 1
    assert latest[0].version == 2
    assert latest[0].parent_id == v1.id
    assert len(history) == 2
    assert v2.parent_id == v1.id


def test_retrieval_cache_is_invalidated_by_index_fingerprint(tmp_db):
    uid = tmp_db.ensure_user("u_cache")
    pid = tmp_db.create_project(uid, "快取")
    queries = ["期初茶會企劃書"]
    memory.save_cached_context(tmp_db, pid, queries, "event_planning", "fingerprint-a", "內容 A", {"count": 1})
    assert memory.get_cached_context(tmp_db, pid, queries, "event_planning", "fingerprint-a")["context_text"] == "內容 A"
    assert memory.get_cached_context(tmp_db, pid, queries, "event_planning", "fingerprint-b") is None


def test_external_research_can_filter_school_and_returns_provenance():
    result = search_social_references("招生", schools="北藝", platform="Instagram")
    assert result["references"]
    source = result["references"][0]
    assert "臺北藝術" in source["school"]
    assert source["title"]
    assert source["url"].startswith("https://")
    assert source["date"] == "2026-08-24"
    assert source["verification"] == "verified"
    assert "〔來源" in result["message"]


def test_source_conflicts_are_explicit_and_current_term_wins():
    first = Chunk("來源 A", "a.md", "社長：甲甲\n社課時間：週三", meta=ChunkMeta(source_type="archive", academic_year="115"))
    second = Chunk("來源 B", "b.md", "社長：乙乙\n社課時間：週四", meta=ChunkMeta(source_type="archive", academic_year="115"))
    conflicts = detect_conflicts([Scored(first, 1), Scored(second, 0.9)], {"president": "丙丙"})
    kinds = {(item["field"], item["kind"]) for item in conflicts}
    assert ("president", "same_year_conflict") in kinds
    assert ("president", "stale_reference") in kinds


@pytest.mark.asyncio
async def test_composite_run_emits_workflow_and_research_events(tmp_output_dir, tmp_db, monkeypatch, clean_term):
    from app import orchestrator as orch

    uid = tmp_db.ensure_user("u_composite", is_local=True)
    sid = tmp_db.create_session(uid)
    ctx = RequestContext(user_id=uid, session_id=sid)
    fake = FakeLLM(
        script=[
            tool("search_social_references", query="招生", schools="北藝", platform="Instagram"),
            tool("create_social_carousel", filename="淡江招生輪播", content="# 第一張\n\n淡江招生資訊待確認。"),
            say("研究完成，已產生淡江版本。"),
        ]
    )
    monkeypatch.setattr(orch, "NvidiaClient", lambda **_: fake)
    events = [
        event async for event in orch.run_turn(
            ctx, "研究其他學校招生方式後，幫我產生淡江招生輪播", destination="local"
        )
    ]
    kinds = [event["type"] for event in events]
    assert "step_started" in kinds
    assert "research_sources_saved" in kinds
    assert "artifact_ready" in kinds
    assert "task_completed" in kinds
    retrieval_event = next(event for event in events if event["type"] == "retrieval_result")
    assert retrieval_event["external_reference"] >= 1
    project_id = tmp_db.get_session(sid, uid)["project_id"]
    assert project_id
    saved_sources = tmp_db.list_research_sources(project_id)
    assert saved_sources and saved_sources[0]["verification"] in {"verified", "needs_verification"}


@pytest.mark.asyncio
async def test_continuation_reuses_cached_retrieval(tmp_output_dir, tmp_db, monkeypatch, clean_term):
    from app import orchestrator as orch

    uid = tmp_db.ensure_user("u_continue", is_local=True)
    sid = tmp_db.create_session(uid)
    ctx = RequestContext(user_id=uid, session_id=sid)
    fake1 = FakeLLM(script=[say("第一輪完成")])
    monkeypatch.setattr(orch, "NvidiaClient", lambda **_: fake1)
    first = [event async for event in orch.run_turn(ctx, "期初茶會企劃書要怎麼寫", destination="local")]
    assert any(event["type"] == "task_completed" for event in first)

    fake2 = FakeLLM(script=[say("接續完成")])
    monkeypatch.setattr(orch, "NvidiaClient", lambda **_: fake2)
    second = [event async for event in orch.run_turn(ctx, "接續剛才", destination="local")]
    cached = next(event for event in second if event["type"] == "retrieval_result")
    assert cached["cached"] is True
