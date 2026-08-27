"""工作階段續接：還原對話、任務類型、產出與未完成步驟。

驗收重點是「點了繼續之後看得到東西」——不能清空畫面只留任務標題。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config
from app.orchestrator.state import OrchestrationState, PlanStep, TaskType
from app.services import memory as memory_service


@pytest.fixture()
def client(tmp_db, tmp_output_dir, monkeypatch):
    monkeypatch.setattr(config, "AUTH_MODE", "local")
    monkeypatch.setattr(config, "APP_ACCESS_TOKEN", "")
    from app.main import app

    with TestClient(app) as c:
        yield c


def _seed(store, sid, user_id="u_local"):
    """建一個有對話、有專案狀態、有產出的工作階段。"""
    pid = store.create_project(user_id, name="期初茶會網宣", task_type="social_publicity")
    store.bind_session_project(sid, pid)
    store.set_session_title(sid, "期初茶會網宣")
    for i in range(6):
        store.append_message(sid, {"role": "user", "content": f"使用者第 {i} 則"})
        store.append_message(sid, {"role": "assistant", "content": f"助理第 {i} 則"})

    state = OrchestrationState()
    state.task_type = TaskType.SOCIAL_PUBLICITY
    state.selected_skill = "social_publicity"
    state.completion_status = "in_progress"
    state.plan_steps = [
        PlanStep("查社團知識庫與歷年範例", done=True),
        PlanStep("建立輪播草稿", done=False),
        PlanStep("檢查產出是否符合社團規範", done=False),
    ]
    memory_service.save_state(store, pid, state)
    store.record_artifact(
        user_id=user_id, filename="115-1-期初茶會-輪播草稿.md",
        local_path="/tmp/x.md", project_id=pid, session_id=sid,
    )
    return pid


def test_resume_restores_conversation_and_context(client, tmp_db):
    sid = client.post("/api/session", json={}).json()["session_id"]
    _seed(tmp_db, sid)

    data = client.get(f"/api/session/resume?session_id={sid}").json()

    assert data["title"] == "期初茶會網宣"
    assert data["task_type"] == "social_publicity"
    assert data["task_label"] == "社群網宣", "要顯示中文任務類型"
    # 還原最近 3–5 輪對話（預設 10 則訊息 = 5 輪）
    assert 6 <= len(data["messages"]) <= 10
    assert data["messages"][-1]["text"] == "助理第 5 則"
    assert data["message_count"] == 12
    assert data["truncated"] is True, "訊息比顯示上限多時要標記為截斷"


def test_resume_restores_artifacts(client, tmp_db):
    sid = client.post("/api/session", json={}).json()["session_id"]
    _seed(tmp_db, sid)
    data = client.get(f"/api/session/resume?session_id={sid}").json()
    names = [a["filename"] for a in data["artifacts"]]
    assert "115-1-期初茶會-輪播草稿.md" in names


def test_resume_restores_pending_steps(client, tmp_db):
    sid = client.post("/api/session", json={}).json()["session_id"]
    _seed(tmp_db, sid)
    data = client.get(f"/api/session/resume?session_id={sid}").json()
    assert data["pending_steps"] == ["建立輪播草稿", "檢查產出是否符合社團規範"]
    assert data["completion_status"] == "in_progress"


def test_resume_full_returns_everything(client, tmp_db):
    sid = client.post("/api/session", json={}).json()["session_id"]
    _seed(tmp_db, sid)
    data = client.get(f"/api/session/resume?session_id={sid}&full=1").json()
    assert len(data["messages"]) == 12
    assert data["truncated"] is False


def test_resume_excludes_tool_traffic(client, tmp_db):
    """工具往返是內部細節，不能倒進使用者的對話還原裡。"""
    sid = client.post("/api/session", json={}).json()["session_id"]
    tmp_db.append_message(sid, {"role": "user", "content": "做輪播"})
    tmp_db.append_message(sid, {
        "role": "assistant", "content": "",
        "tool_calls": [{"id": "1", "type": "function",
                        "function": {"name": "create_social_carousel", "arguments": "{}"}}],
    })
    tmp_db.append_message(sid, {"role": "tool", "tool_call_id": "1",
                                "name": "create_social_carousel", "content": "{\"ok\": true}"})
    tmp_db.append_message(sid, {"role": "assistant", "content": "做好了"})

    data = client.get(f"/api/session/resume?session_id={sid}").json()
    texts = [m["text"] for m in data["messages"]]
    assert texts == ["做輪播", "做好了"]
    assert "create_social_carousel" not in str(data["messages"])


def test_resume_expired_session_returns_chinese_404(client):
    resp = client.get("/api/session/resume?session_id=s_does_not_exist")
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert "過期" in detail and "新任務" in detail


def test_resume_other_users_session_is_404(tmp_db, tmp_output_dir, monkeypatch):
    monkeypatch.setattr(config, "AUTH_MODE", "token")
    monkeypatch.setattr(config, "APP_ACCESS_TOKEN", "tok")
    from app.main import app

    with (
        TestClient(app, base_url="https://testserver") as a,
        TestClient(app, base_url="https://testserver") as b,
    ):
        a.post("/api/auth", json={"token": "tok"})
        b.post("/api/auth", json={"token": "tok"})
        sid = a.post("/api/session", json={}).json()["session_id"]
        assert b.get(f"/api/session/resume?session_id={sid}").status_code == 404


def test_resume_requires_login(tmp_db, tmp_output_dir, monkeypatch):
    monkeypatch.setattr(config, "AUTH_MODE", "token")
    monkeypatch.setattr(config, "APP_ACCESS_TOKEN", "tok")
    from app.main import app

    with TestClient(app, base_url="https://testserver") as c:
        assert c.get("/api/session/resume?session_id=s_x").status_code == 401


# ── 前端行為（靜態驗收）─────────────────────────────────────

def test_frontend_renders_resume_context():
    from pathlib import Path

    js = (Path(__file__).resolve().parent.parent / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert "/api/session/resume" in js
    assert "renderResume" in js and "renderResumeExpired" in js
    # 不可以只留一句「直接輸入下一步即可」就清空畫面
    assert "查看完整紀錄" in js
    assert "建立新任務" in js
    assert "尚未完成的步驟" in js
    assert "這個任務目前的產出" in js
