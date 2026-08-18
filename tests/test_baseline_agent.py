"""Baseline：保護既有的代理迴圈行為（用假模型，不需 API 金鑰）。"""

from __future__ import annotations

import pytest

from tests.fakes import FakeLLM, say, tool


async def collect(agent_mod, monkeypatch, script, message="幫我做招生管道表", **kw):
    fake = FakeLLM(script=script)
    monkeypatch.setattr(agent_mod, "NvidiaClient", lambda **_: fake)
    events = []
    async for ev in agent_mod.run_turn("t_session", message, destination="local", **kw):
        events.append(ev)
    return fake, events


@pytest.mark.asyncio
async def test_full_loop_search_then_create_then_finish(tmp_output_dir, monkeypatch):
    from app import agent as agent_mod

    agent_mod.reset_session("t_session")
    fake, events = await collect(
        agent_mod,
        monkeypatch,
        [
            tool("search_knowledge", query="招生管道"),
            tool(
                "create_spreadsheet",
                filename="招生管道表",
                sheets=[{"name": "管道", "csv": "管道,負責\n路宣,招生組"}],
            ),
            say("做好了。"),
        ],
    )
    kinds = [e["type"] for e in events]
    assert kinds.count("tool_start") == 2
    assert kinds.count("tool_end") == 2
    assert "done" in kinds
    assert not [e for e in events if e["type"] == "error"]
    artifacts = [e for e in events if e.get("artifact")]
    assert len(artifacts) == 1
    agent_mod.reset_session("t_session")


@pytest.mark.asyncio
async def test_plain_answer_without_tools(monkeypatch):
    from app import agent as agent_mod

    agent_mod.reset_session("t_session")
    _fake, events = await collect(agent_mod, monkeypatch, [say("我們是淡江大學領袖禪學社。")])
    msgs = [e for e in events if e["type"] == "message"]
    assert len(msgs) == 1
    assert "領袖禪學社" in msgs[0]["text"]
    agent_mod.reset_session("t_session")


@pytest.mark.asyncio
async def test_llm_failure_becomes_readable_error(monkeypatch):
    from app import agent as agent_mod

    agent_mod.reset_session("t_session")
    fake = FakeLLM(script=[say("x")], raise_after=0)
    monkeypatch.setattr(agent_mod, "NvidiaClient", lambda **_: fake)
    events = [ev async for ev in agent_mod.run_turn("t_session", "hi", destination="local")]
    errs = [e for e in events if e["type"] == "error"]
    assert errs and "模擬失敗" in errs[0]["text"]
    agent_mod.reset_session("t_session")


@pytest.mark.asyncio
async def test_runaway_tool_loop_is_bounded(tmp_output_dir, monkeypatch):
    """模型一直呼叫工具不收尾時，要停下來而不是無限迴圈。"""
    from app import agent as agent_mod

    agent_mod.reset_session("t_session")
    fake, events = await collect(
        agent_mod, monkeypatch, [tool("search_knowledge", query="招生")]
    )
    assert fake.call_count <= agent_mod.MAX_TOOL_ROUNDS + 1
    assert [e for e in events if e["type"] == "error"], "應該回報卡住"
    agent_mod.reset_session("t_session")


@pytest.mark.asyncio
async def test_system_prompt_forbids_fabricating_current_facts(monkeypatch):
    from app import agent as agent_mod

    agent_mod.reset_session("t_session")
    fake, _ = await collect(agent_mod, monkeypatch, [say("好")])
    prompt = fake.system_prompt()
    for phrase in ["社長", "社費", "待填", "療效"]:
        assert phrase in prompt, f"系統提示應該提到 {phrase}"
    agent_mod.reset_session("t_session")


@pytest.mark.asyncio
async def test_no_chain_of_thought_events(tmp_output_dir, monkeypatch):
    """UI 事件不可以夾帶推理過程。"""
    from app import agent as agent_mod

    agent_mod.reset_session("t_session")
    _fake, events = await collect(
        agent_mod,
        monkeypatch,
        [tool("search_knowledge", query="招生管道"), say("完成")],
    )
    allowed = {
        "status", "message", "tool_start", "tool_end", "error", "done",
        "task_understood", "plan_created", "retrieval_started", "retrieval_result",
        "tool_started", "tool_completed", "verification_started", "verification_result",
        "repair_started", "artifact_ready", "task_completed",
    }
    for ev in events:
        assert ev["type"] in allowed, f"未預期的事件型別 {ev['type']}"
    agent_mod.reset_session("t_session")
