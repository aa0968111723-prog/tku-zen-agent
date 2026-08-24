"""SSE 串流的相容性、取消、斷線與解析行為。

`/api/chat` 的 SSE 相容性是硬需求：既有前端只認 `data: {json}\\n\\n`，
所以這裡把「事件格式沒有變」釘死，同時驗證新增的取消路徑。
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, orchestrator

APP_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "app.js"


@pytest.fixture()
def client(tmp_db, tmp_output_dir, monkeypatch):
    monkeypatch.setattr(config, "AUTH_MODE", "local")
    monkeypatch.setattr(config, "APP_ACCESS_TOKEN", "")
    from app.main import app

    with TestClient(app) as c:
        yield c


def _events(body: str) -> list[dict]:
    out = []
    for record in body.split("\n\n"):
        record = record.strip()
        if not record.startswith("data:"):
            continue
        out.append(json.loads(record[5:].strip()))
    return out


# ── 格式相容性 ───────────────────────────────────────────────

def test_sse_wire_format_unchanged(client, monkeypatch):
    """每個事件都是 `data: {json}` + 空行，最後一定有 done。"""

    async def fake_turn(ctx, message, *, destination, model=None, attachments=None, requested=None):
        yield {"type": "message", "text": "測試回覆"}
        yield {"type": "task_completed", "summary": {}, "artifacts": []}

    monkeypatch.setattr(orchestrator, "run_turn", fake_turn)
    sid = client.post("/api/session", json={}).json()["session_id"]
    resp = client.post("/api/chat", json={"session_id": sid, "message": "測試"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    body = resp.text
    assert body.endswith("\n\n"), "串流必須以空行收尾，前端才能切出最後一段"
    for record in body.split("\n\n"):
        if record.strip():
            assert record.startswith("data: "), f"非標準 SSE 記錄：{record[:40]!r}"

    events = _events(body)
    assert events[0]["type"] == "session" and events[0]["session_id"] == sid
    assert events[-1]["type"] == "done"
    assert any(e["type"] == "message" and e["text"] == "測試回覆" for e in events)


def test_sse_error_event_is_generic_chinese(client, monkeypatch):
    async def boom(ctx, message, *, destination, model=None, attachments=None, requested=None):
        raise RuntimeError("NVIDIA api_key leaked in traceback")
        yield  # pragma: no cover

    monkeypatch.setattr(orchestrator, "run_turn", boom)
    sid = client.post("/api/session", json={}).json()["session_id"]
    body = client.post("/api/chat", json={"session_id": sid, "message": "測試"}).text
    events = _events(body)
    err = [e for e in events if e["type"] == "error"]
    assert err and err[0]["text"] == "系統忙碌中，請稍後再試"
    assert "NVIDIA" not in body and "api_key" not in body
    assert events[-1]["type"] == "done"


def test_all_event_labels_are_chinese(client, monkeypatch):
    """使用者看得到的欄位不可出現英文內部工具名。"""

    async def fake_turn(ctx, message, *, destination, model=None, attachments=None, requested=None):
        from app import tools

        yield {"type": "tool_started", "name": "create_social_carousel",
               "label": tools.LABELS["create_social_carousel"], "preview": ""}
        yield {"type": "tool_completed", "name": "create_social_carousel",
               "label": tools.LABELS["create_social_carousel"], "ok": True, "detail": ""}

    monkeypatch.setattr(orchestrator, "run_turn", fake_turn)
    sid = client.post("/api/session", json={}).json()["session_id"]
    events = _events(client.post("/api/chat", json={"session_id": sid, "message": "測試"}).text)
    for ev in events:
        label = ev.get("label")
        if label:
            assert not re.fullmatch(r"[a-z0-9_]+", label), f"label 不可是英文內部名：{label}"
            assert re.search(r"[一-鿿]", label)


# ── 取消 ─────────────────────────────────────────────────────

def test_cancel_endpoint_stops_stream_and_releases_lock(client, monkeypatch):
    import asyncio

    async def slow_turn(ctx, message, *, destination, model=None, attachments=None, requested=None):
        for i in range(100):
            yield {"type": "step", "label": f"步驟 {i}"}
            await asyncio.sleep(0.05)

    monkeypatch.setattr(orchestrator, "run_turn", slow_turn)
    from app.main import TURNS

    sid = client.post("/api/session", json={}).json()["session_id"]
    collected: list[str] = []

    def consume():
        with client.stream("POST", "/api/chat", json={"session_id": sid, "message": "跑"}) as r:
            collected.append(b"".join(r.iter_raw()).decode())

    t = threading.Thread(target=consume)
    t.start()
    time.sleep(0.4)

    resp = client.post("/api/chat/cancel", json={"session_id": sid})
    assert resp.status_code == 200 and resp.json()["cancelled"] is True

    t.join(timeout=15)
    assert not t.is_alive(), "取消後串流必須收尾，不能一直掛著"

    events = _events(collected[0])
    assert any(e["type"] == "cancelled" for e in events)
    assert events[-1]["type"] == "done"
    # session 執行鎖必須釋放：能再次啟動同一個 session 的任務
    assert TURNS.start(sid) is not None
    TURNS.finish(sid)


def test_cancel_unknown_session_is_404(client):
    assert client.post("/api/chat/cancel", json={"session_id": "s_nope"}).status_code == 404


def test_cancel_other_users_session_is_404(tmp_db, tmp_output_dir, monkeypatch):
    """別人的 session 不能被取消 —— 回 404，不洩漏存在與否。"""
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
        assert b.post("/api/chat/cancel", json={"session_id": sid}).status_code == 404


def test_second_turn_on_same_session_is_rejected(client, monkeypatch):
    import asyncio

    async def slow_turn(ctx, message, *, destination, model=None, attachments=None, requested=None):
        for _ in range(60):
            yield {"type": "step", "label": "跑"}
            await asyncio.sleep(0.05)

    monkeypatch.setattr(orchestrator, "run_turn", slow_turn)
    sid = client.post("/api/session", json={}).json()["session_id"]

    def consume():
        with client.stream("POST", "/api/chat", json={"session_id": sid, "message": "a"}) as r:
            b"".join(r.iter_raw())

    t = threading.Thread(target=consume)
    t.start()
    time.sleep(0.3)
    try:
        second = client.post("/api/chat", json={"session_id": sid, "message": "b"})
        assert second.status_code == 409
        assert "正在執行的任務" in second.json()["detail"]
    finally:
        client.post("/api/chat/cancel", json={"session_id": sid})
        t.join(timeout=15)


# ── 前端解析器（對 app.js 的靜態驗收）────────────────────────

def test_frontend_flushes_trailing_buffer():
    js = APP_JS.read_text(encoding="utf-8")
    assert "buffer += decoder.decode();" in js, "串流結束前必須 flush 解碼器"
    assert "if (buffer.trim()) dispatch(buffer);" in js, "最後一段沒有 \\n\\n 也要處理"


def test_frontend_has_abort_and_timeout():
    js = APP_JS.read_text(encoding="utf-8")
    assert "new AbortController()" in js
    assert "STREAM_IDLE_TIMEOUT_MS" in js
    assert "resetStreamWatchdog" in js


def test_frontend_supports_standard_sse_event_field():
    js = APP_JS.read_text(encoding="utf-8")
    assert 'line.startsWith("event:")' in js, "要支援標準 SSE 的 event: 欄位"
    assert 'line.startsWith("data:")' in js


def test_frontend_distinguishes_error_kinds():
    js = APP_JS.read_text(encoding="utf-8")
    for phrase in ["連線中斷", "連線逾時", "伺服器發生錯誤", "沒有權限執行這個操作"]:
        assert phrase in js, f"缺少分開的錯誤提示：{phrase}"


def test_frontend_warns_on_unknown_event_without_breaking():
    js = APP_JS.read_text(encoding="utf-8")
    assert "收到未知的串流事件類型，已略過" in js
    assert "default:" in js


def test_frontend_stop_calls_server_cancel():
    js = APP_JS.read_text(encoding="utf-8")
    assert "停止生成" in js
    assert '"/api/chat/cancel"' in js
    assert "requestStreamCancel" in js
    assert 'controlTask("cancel"' in js


# ── Context 跨事件存續（政大反問輪的假錯誤事件迴歸）─────────────

def test_clarification_stream_has_no_stray_error(client):
    """整條 SSE 串流必須共用同一個 Context。

    每個事件各開新 task 時，use() 的 token reset 會在收尾拋 ValueError，
    讓「政大呢」這種反問輪的結尾多出一個假的「系統忙碌中」錯誤事件；
    RequestContext 與研究範圍也會在後續工具執行時遺失。
    """
    import json as _json

    sid = client.post("/api/session", json={}).json()["session_id"]
    body = client.post("/api/chat", json={"session_id": sid, "message": "政大呢"}).text
    types = [
        _json.loads(line[5:])["type"]
        for line in body.splitlines()
        if line.startswith("data:")
    ]
    assert "clarification_needed" in types
    assert "error" not in types, types
    assert types[-1] == "done"
