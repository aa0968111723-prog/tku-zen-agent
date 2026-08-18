"""Phase 1：持久化與隔離。"""

from __future__ import annotations

import pytest

from app.services.session_store import SessionStore


def test_survives_restart(tmp_db, tmp_path):
    """規格驗收：server restart 後仍能讀到最近 session/project。"""
    uid = tmp_db.ensure_user("u_a")
    sid = tmp_db.create_session(uid)
    pid = tmp_db.create_project(uid, "期初茶會")
    tmp_db.bind_session_project(sid, pid)
    tmp_db.append_message(sid, {"role": "user", "content": "幫我做企劃"})
    tmp_db.remember(pid, "目標人數", "60")
    path = tmp_db.path
    tmp_db.close()

    # 模擬伺服器重開
    reopened = SessionStore(path)
    try:
        assert reopened.get_session(sid, uid) is not None
        assert reopened.load_messages(sid)[0]["content"] == "幫我做企劃"
        assert reopened.recall(pid)["目標人數"]["value"] == "60"
        assert reopened.list_projects(uid)[0]["name"] == "期初茶會"
    finally:
        reopened.close()


def test_user_cannot_read_another_users_session(tmp_db):
    a = tmp_db.ensure_user("u_a")
    b = tmp_db.ensure_user("u_b")
    sid = tmp_db.create_session(a)
    assert tmp_db.get_session(sid, a) is not None
    assert tmp_db.get_session(sid, b) is None, "B 讀得到 A 的 session"


def test_user_cannot_read_another_users_artifact(tmp_db, tmp_path):
    a = tmp_db.ensure_user("u_a")
    b = tmp_db.ensure_user("u_b")
    f = tmp_path / "secret.xlsx"
    f.write_text("x", encoding="utf-8")
    art = tmp_db.record_artifact(user_id=a, filename="secret.xlsx", local_path=str(f))

    assert tmp_db.get_artifact(art.id, a) is not None
    assert tmp_db.get_artifact(art.id, b) is None, "B 拿得到 A 的 artifact"


def test_guessing_session_id_gets_nothing(tmp_db):
    a = tmp_db.ensure_user("u_a")
    for guess in ["s_aaaa", "1", "", "'; DROP TABLE sessions;--", "s_" + "x" * 40]:
        assert tmp_db.get_session(guess, a) is None


def test_delete_session_only_own(tmp_db):
    a = tmp_db.ensure_user("u_a")
    b = tmp_db.ensure_user("u_b")
    sid = tmp_db.create_session(a)
    assert tmp_db.delete_session(sid, b) is False, "B 刪掉了 A 的 session"
    assert tmp_db.delete_session(sid, a) is True


def test_artifact_public_view_hides_server_path(tmp_db, tmp_path):
    uid = tmp_db.ensure_user("u_a")
    f = tmp_path / "a.docx"
    f.write_text("x", encoding="utf-8")
    art = tmp_db.record_artifact(user_id=uid, filename="a.docx", local_path=str(f))
    public = art.public()
    assert "local_path" not in public
    assert str(tmp_path) not in str(public)
    assert public["artifact_id"] == art.id


def test_artifact_versions_increment_and_link(tmp_db, tmp_path):
    uid = tmp_db.ensure_user("u_a")
    f = tmp_path / "plan.docx"
    f.write_text("x", encoding="utf-8")
    v1 = tmp_db.record_artifact(user_id=uid, filename="plan.docx", local_path=str(f))
    v2 = tmp_db.record_artifact(user_id=uid, filename="plan.docx", local_path=str(f))
    assert (v1.version, v2.version) == (1, 2)


def test_artifacts_scoped_by_project(tmp_db, tmp_path):
    uid = tmp_db.ensure_user("u_a")
    p1 = tmp_db.create_project(uid, "茶會")
    p2 = tmp_db.create_project(uid, "評鑑")
    f = tmp_path / "x.docx"
    f.write_text("x", encoding="utf-8")
    tmp_db.record_artifact(user_id=uid, filename="a.docx", local_path=str(f), project_id=p1)
    tmp_db.record_artifact(user_id=uid, filename="b.docx", local_path=str(f), project_id=p2)
    assert [a.filename for a in tmp_db.list_artifacts(uid, project_id=p1)] == ["a.docx"]


def test_working_memory_upsert(tmp_db):
    uid = tmp_db.ensure_user("u_a")
    pid = tmp_db.create_project(uid, "x")
    tmp_db.remember(pid, "社長", "待填")
    tmp_db.remember(pid, "社長", "林小明")
    recalled = tmp_db.recall(pid)
    assert recalled["社長"]["value"] == "林小明"
    assert len(recalled) == 1, "upsert 應該覆蓋而不是新增"


def test_message_ordering_is_stable(tmp_db):
    uid = tmp_db.ensure_user("u_a")
    sid = tmp_db.create_session(uid)
    for i in range(30):
        tmp_db.append_message(sid, {"role": "user", "content": f"m{i}"})
    got = [m["content"] for m in tmp_db.load_messages(sid)]
    assert got == [f"m{i}" for i in range(30)]


def test_tool_calls_round_trip(tmp_db):
    uid = tmp_db.ensure_user("u_a")
    sid = tmp_db.create_session(uid)
    msg = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}],
    }
    tmp_db.append_message(sid, msg)
    loaded = tmp_db.load_messages(sid)[0]
    assert loaded["tool_calls"][0]["function"]["name"] == "f"


def test_unknown_user_is_not_auto_created(tmp_db):
    """偽造 cookie 不該等於自己開帳號。"""
    assert tmp_db.user_exists("u_forged") is False
