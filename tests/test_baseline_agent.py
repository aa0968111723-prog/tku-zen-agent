"""Baseline：保護代理迴圈的核心行為（用假模型，不需 API 金鑰）。

v2 之後入口從 app.agent 換成 app.orchestrator，但這裡的斷言完全沒變 ——
它們檢查的是行為，不是模組名稱。
"""

from __future__ import annotations

import pytest

from app.services.context import RequestContext
from tests.fakes import FakeLLM, say, tool

# orchestrator 允許送出的事件型別。多出來的一律視為錯誤：
# 這條測試同時擋住「不小心把推理過程送到前端」。
ALLOWED_EVENTS = {
    "task_understood", "plan_created", "retrieval_started", "retrieval_result",
    "tool_started", "tool_completed", "verification_started", "verification_result",
    "repair_started", "artifact_ready", "task_completed",
    "message", "error", "done", "session", "status",
    # 研究驗證引擎的結構化事件（進度與依據，不是推理過程）
    "clarification_needed", "source_cards", "answer_review",
    "contamination_warning", "research_status",
    "visual_analysis_started", "visual_analysis_completed",
}


async def collect(orch, monkeypatch, script, message, ctx, **kw):
    fake = FakeLLM(script=script)
    monkeypatch.setattr(orch, "NvidiaClient", lambda **_: fake)
    events = []
    async for ev in orch.run_turn(ctx, message, destination="local", **kw):
        events.append(ev)
    return fake, events


@pytest.fixture()
def ctx(tmp_db):
    user_id = tmp_db.ensure_user("u_test", is_local=True)
    session_id = tmp_db.create_session(user_id)
    return RequestContext(user_id=user_id, session_id=session_id)


@pytest.mark.asyncio
async def test_full_loop_search_then_create_then_finish(tmp_output_dir, monkeypatch, ctx):
    from app import orchestrator as orch

    fake, events = await collect(
        orch,
        monkeypatch,
        [
            tool("search_knowledge", query="招生管道"),
            tool(
                "create_spreadsheet",
                filename="招生管道表",
                sheets=[{"name": "管道", "csv": "管道,負責組別\n路宣,招生組\n個接,全體"}],
            ),
            say("做好了。"),
        ],
        "幫我做一份招生管道分工表",
        ctx,
    )
    kinds = [e["type"] for e in events]
    assert kinds.count("tool_started") == 2
    assert kinds.count("tool_completed") == 2
    assert "artifact_ready" in kinds
    assert "task_completed" in kinds
    assert not [e for e in events if e["type"] == "error"]


@pytest.mark.asyncio
async def test_plain_answer_without_tools(monkeypatch, ctx):
    from app import orchestrator as orch

    _fake, events = await collect(
        orch, monkeypatch, [say("我們是淡江大學領袖禪學社。")], "你們是什麼社團？", ctx
    )
    msgs = [e for e in events if e["type"] == "message"]
    assert msgs and "領袖禪學社" in msgs[-1]["text"]


@pytest.mark.asyncio
async def test_image_attachment_is_summarized_by_fal_only_for_current_model_request(monkeypatch, ctx, tmp_db):
    from app import orchestrator as orch

    async def describe_images(attachments):
        assert attachments[0]["data_url"] == "data:image/png;base64,AA=="
        return "海報上有新生茶會與報名資訊。"

    monkeypatch.setattr(orch.fal_service, "describe_images", describe_images)

    fake, events = await collect(
        orch,
        monkeypatch,
        [say("已讀取圖片內容。")],
        "請依圖片寫文案",
        ctx,
        attachments=[{"name": "海報.png", "media_type": "image/png", "data_url": "data:image/png;base64,AA=="}],
    )
    user = next(message for message in fake.calls[0]["messages"] if message["role"] == "user")
    assert isinstance(user["content"], str)
    assert "【圖片可見資訊" in user["content"]
    assert "新生茶會" in user["content"]
    assert "data:image" not in user["content"]
    assert [event["type"] for event in events if event["type"].startswith("visual_")] == [
        "visual_analysis_started", "visual_analysis_completed",
    ]
    stored = tmp_db.load_messages(ctx.session_id)
    history = "\n".join(message["content"] for message in stored)
    assert "data:image" not in history
    assert "圖片可見資訊" not in history


@pytest.mark.asyncio
async def test_llm_failure_becomes_readable_error(monkeypatch, ctx):
    from app import orchestrator as orch

    fake = FakeLLM(script=[say("x")], raise_after=0)
    monkeypatch.setattr(orch, "NvidiaClient", lambda **_: fake)
    events = [ev async for ev in orch.run_turn(ctx, "你好", destination="local")]
    errs = [e for e in events if e["type"] == "error"]
    # 新契約：技術細節只進伺服器 log，前端一律統一文案
    assert errs and errs[0]["text"] == "系統忙碌中，請稍後再試"
    assert "模擬失敗" not in errs[0]["text"]


@pytest.mark.asyncio
async def test_runaway_tool_loop_is_bounded(tmp_output_dir, monkeypatch, ctx):
    from app import orchestrator as orch

    fake, events = await collect(
        orch, monkeypatch, [tool("search_knowledge", query="招生")], "幫我查招生", ctx
    )
    assert fake.call_count <= orch.MAX_TOOL_ROUNDS + 1
    assert [e for e in events if e["type"] == "error"], "卡住時要回報"


@pytest.mark.asyncio
async def test_system_prompt_forbids_fabricating_current_facts(monkeypatch, ctx):
    from app import orchestrator as orch

    fake, _ = await collect(orch, monkeypatch, [say("好")], "你好", ctx)
    prompt = fake.system_prompt()
    for phrase in ["社長", "社費", "待填", "療效", "優先順序"]:
        assert phrase in prompt, f"系統提示應該提到 {phrase}"


@pytest.mark.asyncio
async def test_only_structured_events_no_chain_of_thought(tmp_output_dir, monkeypatch, ctx):
    from app import orchestrator as orch

    _fake, events = await collect(
        orch,
        monkeypatch,
        [tool("search_knowledge", query="招生管道"), say("完成")],
        "幫我做招生企劃",
        ctx,
    )
    for ev in events:
        assert ev["type"] in ALLOWED_EVENTS, f"未預期的事件型別 {ev['type']}"


@pytest.mark.asyncio
async def test_tool_failure_is_recoverable(tmp_output_dir, monkeypatch, ctx):
    """工具參數錯誤時，模型要拿到可讀訊息並能重試，而不是整個對話中斷。"""
    from app import orchestrator as orch

    fake, events = await collect(
        orch,
        monkeypatch,
        [
            tool("create_spreadsheet", filename="缺參數"),          # 故意漏 sheets
            tool(
                "create_spreadsheet",
                filename="補好了",
                sheets=[{"name": "A", "csv": "欄1,欄2\n值1,值2"}],
            ),
            say("修好了。"),
        ],
        "幫我做一份表",
        ctx,
    )
    completed = [e for e in events if e["type"] == "tool_completed"]
    assert completed[0]["ok"] is False
    assert completed[1]["ok"] is True
    assert not [e for e in events if e["type"] == "error"]
