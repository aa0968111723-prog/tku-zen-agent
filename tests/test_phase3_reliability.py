"""第三階段：任務狀態、SSE 可靠性、停止與恢復。

稽核對應：
  · 不可靠 #9：「繼續」只翻狀態不執行，系統教的續跑句被自己吞掉
  · 不可靠 #11：模型不產檔也全綠 completed
  · 不可靠 #2：llm 401/404 診斷死碼
  · 不可靠 #6：/api/auth 無獨立暴力節流
  · 不可靠 #7：限流 key 在反向代理後全站共用
  · 不可靠 #30：auth anonymous 桶可 DoS
  · 半成品 #8：單步重試未實作
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.orchestrator.state import OrchestrationState, PlanStep, WorkflowStatus
from app.services.context import RequestContext
from tests.fakes import FakeLLM, say, tool


@pytest.fixture()
def ctx(tmp_db):
    uid = tmp_db.ensure_user("u_p3", is_local=True)
    return RequestContext(user_id=uid, session_id=tmp_db.create_session(uid))


async def run(orch, monkeypatch, script, message, ctx):
    fake = FakeLLM(script=script)
    monkeypatch.setattr(orch, "NvidiaClient", lambda **_: fake)
    events = [ev async for ev in orch.run_turn(ctx, message, destination="local")]
    return fake, events


def kinds(events):
    return [e["type"] for e in events]


# ── 不可靠 #11：不產檔不得 completed ─────────────────────────

@pytest.mark.asyncio
async def test_artifact_expected_but_missing_is_blocked(tmp_output_dir, monkeypatch, ctx):
    from app import orchestrator as orch

    _fake, events = await run(orch, monkeypatch, [say("好的，企劃書的內容如下……")], "幫我做期初茶會企劃書", ctx)
    # 不能一邊存 blocked、一邊送 task_completed 讓前端全綠（grok 審查發現 1）
    assert "task_completed" not in kinds(events)
    failed = [e for e in events if e["type"] == "task_failed"]
    assert failed, kinds(events)
    assert failed[0]["summary"]["workflow_status"] == "blocked"
    assert any("產出" in f["error"] or "檔案" in f["error"] for f in failed[0]["summary"]["failed_steps"])


# ── 不可靠 #9：「繼續」真的續跑 ──────────────────────────────

@pytest.mark.asyncio
async def test_resume_command_actually_continues(tmp_output_dir, monkeypatch, ctx):
    from app import orchestrator as orch

    # 第 1 輪：說好要產檔但沒產 → blocked
    await run(orch, monkeypatch, [say("好")], "幫我做期初茶會企劃書", ctx)

    # 第 2 輪：「繼續」→ 必須真的把任務跑下去（呼叫模型、產出檔案）
    fake, events = await run(
        orch, monkeypatch,
        [
            tool("create_document", filename="期初茶會企劃書",
                 markdown="# 期初茶會企劃書\n\n## 活動目標\n讓新生認識社團。\n\n## 活動流程\n待填\n\n## 活動時間\n待填\n\n## 活動地點\n待填"),
            say("企劃書完成了。"),
        ],
        "繼續", ctx,
    )
    assert "task_resumed" in kinds(events)
    assert fake.call_count > 0, "「繼續」之後必須真的呼叫模型續跑，不能只翻狀態"
    import json as _json
    assert "artifact_ready" in kinds(events), _json.dumps(events[-6:], ensure_ascii=False, default=str)[:1200]


@pytest.mark.asyncio
async def test_retry_command_reruns_failed_steps(tmp_output_dir, monkeypatch, ctx):
    from app import orchestrator as orch

    await run(orch, monkeypatch, [say("好")], "幫我做期初茶會企劃書", ctx)
    fake, events = await run(
        orch, monkeypatch,
        [
            tool("create_document", filename="期初茶會企劃書",
                 markdown="# 期初茶會企劃書\n\n## 活動目標\n讓新生認識社團。\n\n## 活動流程\n待填\n\n## 時間地點\n待填"),
            say("完成。"),
        ],
        "重試", ctx,
    )
    assert "task_retry_ready" in kinds(events)
    assert fake.call_count > 0, "「重試」之後必須真的重跑失敗步驟"


# ── 半成品 #8：單步重試 ──────────────────────────────────────

def test_reset_single_step():
    state = OrchestrationState()
    state.plan_steps = [PlanStep("查資料"), PlanStep("建立文件"), PlanStep("交付")]
    state.fail_step(1, "工具失敗", code="tool_failed")
    state.fail_step(2, "後續也失敗", code="tool_failed")

    assert state.reset_step(state.plan_steps[1].step_id) is True
    assert state.plan_steps[1].status == "pending"
    assert state.plan_steps[2].status == "failed", "單步重試不能動其他失敗步驟"
    assert state.workflow_status == WorkflowStatus.IN_PROGRESS

    assert state.reset_step("step-不存在") is False
    assert state.reset_step(state.plan_steps[0].step_id) is False  # 不是 failed


# ── 不可靠 #6：/api/auth 專屬節流 ────────────────────────────

def test_auth_endpoint_has_dedicated_throttle(tmp_db, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services import ratelimit

    ratelimit.reset()
    with TestClient(app) as client:
        codes = []
        for _ in range(12):
            resp = client.post("/api/auth", json={"token": "wrong-token"})
            codes.append(resp.status_code)
    assert 429 in codes, codes
    assert codes[-1] == 429
    ratelimit.reset()


# ── 不可靠 #7：反向代理後的真實 IP ───────────────────────────

def test_client_ip_uses_xff_only_behind_private_proxy():
    from app.main import client_ip

    def _req(host: str, xff: str = ""):
        headers = {"x-forwarded-for": xff} if xff else {}
        return SimpleNamespace(client=SimpleNamespace(host=host), headers=headers)

    # 代理（私有 IP）後面 → 信 XFF 第一個 hop
    assert client_ip(_req("10.0.0.2", "1.2.3.4, 8.8.8.8, 10.0.0.2")) == "8.8.8.8"  # 最右非私有跳，最左可偽造
    # 公網直連帶假 XFF → 不信
    assert client_ip(_req("8.8.4.4", "1.2.3.4")) == "8.8.4.4"
    # 沒有 XFF → 直連位址
    assert client_ip(_req("127.0.0.1")) == "127.0.0.1"


# ── 不可靠 #30：auth anonymous 桶 ────────────────────────────

def test_lockout_dimensions_skip_shared_anonymous_bucket():
    from app.services.auth import _request_dimensions

    req_no_cookie = SimpleNamespace(
        client=SimpleNamespace(host="1.2.3.4"), cookies={}, headers={},
    )
    dims = _request_dimensions(req_no_cookie, "tku_access")
    assert len(dims) == 1 and dims[0][0] == "ip"

    req_cookie = SimpleNamespace(
        client=SimpleNamespace(host="1.2.3.4"), cookies={"tku_access": "abc"}, headers={},
    )
    dims = _request_dimensions(req_cookie, "tku_access")
    assert len(dims) == 2


# ── SSE 心跳 ─────────────────────────────────────────────────

def test_sse_heartbeat_during_slow_model(tmp_db, monkeypatch):
    import asyncio as aio

    from fastapi.testclient import TestClient

    from app import main as main_mod
    from app.main import app

    monkeypatch.setattr(main_mod, "SSE_HEARTBEAT_SECONDS", 0.05)

    async def slow_turn(ctx, message, *, destination, model=None, attachments=None, requested=None):
        await aio.sleep(0.3)
        yield {"type": "message", "text": "慢工出細活"}

    monkeypatch.setattr(main_mod.orchestrator, "run_turn", slow_turn)
    with TestClient(app) as client:
        sid = client.post("/api/session", json={}).json()["session_id"]
        body = client.post("/api/chat", json={"session_id": sid, "message": "測试"}).text
    assert ": ping" in body, "等模型期間必須送 SSE 心跳"
    assert '"type": "done"' in body


# ── 不可靠 #2：llm 診斷不再是死碼 ────────────────────────────

@pytest.mark.asyncio
async def test_llm_401_gives_actionable_diagnostic(monkeypatch):
    import httpx

    from app import llm as llm_mod

    async def fake_client():
        class _Resp:
            status_code = 401
            request = None

            def json(self):
                return {}

        class _Client:
            async def post(self, *a, **k):
                return _Resp()

        return _Client()

    monkeypatch.setattr(llm_mod, "get_http_client", fake_client)
    client = llm_mod.NvidiaClient(api_key="nvapi-test")
    with pytest.raises(llm_mod.LLMError) as exc:
        await client.chat([{"role": "user", "content": "hi"}])
    assert "401" in str(exc.value) and "金鑰" in str(exc.value)


# ── grok 第三階段審查的補充迴歸 ──────────────────────────────

@pytest.mark.asyncio
async def test_evaluation_missing_one_of_two_kinds_is_failed(tmp_output_dir, monkeypatch, ctx):
    """grok 2：評鑑要文件＋試算表，只出一份文件不能標完成。"""
    from app import orchestrator as orch

    _fake, events = await run(
        orch, monkeypatch,
        [
            tool("create_document", filename="年度績效報告",
                 markdown="# 年度績效報告\n\n## 活動目標\n待填\n\n## 活動流程\n待填"),
            say("報告完成。"),
        ],
        "做一份社團評鑑的年度績效報告", ctx,
    )
    assert "task_completed" not in kinds(events), "缺試算表不得全綠"
    failed = [e for e in events if e["type"] == "task_failed"]
    assert failed and "試算表" in failed[0]["text"]


@pytest.mark.asyncio
async def test_format_substitution_counts_as_complete(tmp_output_dir, monkeypatch, ctx):
    """預期 1 份文件、模型交出 1 份試算表（分工表）是合理格式選擇。"""
    from app import orchestrator as orch

    _fake, events = await run(
        orch, monkeypatch,
        [
            tool("create_spreadsheet", filename="招生管道分工表",
                 sheets=[{"name": "分工", "csv": "管道,負責組別\n路宣,招生組"}]),
            say("分工表完成。"),
        ],
        "幫我做一份招生管道分工表", ctx,
    )
    assert "task_completed" in kinds(events)


def test_lockout_dimension_uses_real_client_ip_behind_proxy():
    """grok 4：反向代理後鎖定維度不能用 socket IP（否則 5 次錯碼鎖全站）。"""
    from app.services.auth import _request_dimensions

    req_a = SimpleNamespace(
        client=SimpleNamespace(host="10.0.0.2"), cookies={},
        headers={"x-forwarded-for": "8.8.8.8, 10.0.0.2"},
    )
    req_b = SimpleNamespace(
        client=SimpleNamespace(host="10.0.0.2"), cookies={},
        headers={"x-forwarded-for": "9.9.9.9, 10.0.0.2"},
    )
    dims_a = _request_dimensions(req_a, "tku_access")
    dims_b = _request_dimensions(req_b, "tku_access")
    assert dims_a != dims_b, "不同客戶端不能共用同一個鎖定桶"
    assert "8.8.8.8" in dims_a[0][1] and "9.9.9.9" in dims_b[0][1]
