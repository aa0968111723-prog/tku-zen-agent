"""Phase 9：規格點名的失敗情境。

  · 工具參數故意缺漏
  · 模型吐壞 JSON
  · 歷史文件含舊日期/姓名/金額
  · 模型把工具呼叫寫在 content 裡
  · 額度用完 / 連線失敗
"""

from __future__ import annotations

import pytest

from app.llm import NvidiaClient, Reply, ToolCall, _coerce_args, _loads_loose, _salvage_inline_tool_call
from app.services import current_term as term
from app.services.context import RequestContext
from tests.fakes import FakeLLM, say, tool


@pytest.fixture()
def ctx(tmp_db):
    uid = tmp_db.ensure_user("u_f", is_local=True)
    return RequestContext(user_id=uid, session_id=tmp_db.create_session(uid))


async def run(orch, monkeypatch, script, message, ctx):
    fake = FakeLLM(script=script)
    monkeypatch.setattr(orch, "NvidiaClient", lambda **_: fake)
    return fake, [ev async for ev in orch.run_turn(ctx, message, destination="local")]


# ── 壞 JSON ──────────────────────────────────────────────────

def test_loads_loose_handles_code_fence():
    assert _loads_loose('```json\n{"a": 1}\n```') == {"a": 1}


def test_loads_loose_handles_trailing_prose():
    assert _loads_loose('{"a": 1} 這是我的回答') == {"a": 1}


def test_loads_loose_handles_leading_prose():
    assert _loads_loose('好的，這是參數：{"filename": "表"}') == {"filename": "表"}


def test_loads_loose_rejects_garbage():
    with pytest.raises(ValueError):
        _loads_loose("完全不是 JSON 的一段話")


def test_coerce_args_accepts_string_json():
    assert _coerce_args('{"x": 1}') == {"x": 1}


def test_coerce_args_accepts_dict():
    assert _coerce_args({"x": 1}) == {"x": 1}


def test_coerce_args_handles_empty():
    assert _coerce_args("") == {}
    assert _coerce_args(None) == {}


def test_malformed_arguments_become_readable_error():
    """模型吐出無法解析的參數時，要給它可讀訊息而不是崩潰。"""
    from app import tools

    r = tools.dispatch("create_document", {"__parse_error__": "{{{"})
    assert not r["ok"]
    assert "JSON" in r["message"]


def test_parse_recovers_from_broken_tool_arguments():
    data = {
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {"id": "c1", "function": {"name": "create_document", "arguments": "{不是 JSON"}}
                    ],
                }
            }
        ]
    }
    reply = NvidiaClient._parse(data, valid_names={"create_document"})
    assert reply.tool_calls[0].arguments.get("__parse_error__")


def test_inline_tool_call_in_content_is_salvaged():
    """有些開源模型不用 tool_calls 欄位，直接把 JSON 寫在 content 裡。"""
    content = '我來幫你做：{"name": "create_document", "arguments": {"filename": "企劃書", "markdown": "# 標題"}}'
    calls = _salvage_inline_tool_call(content, {"create_document"})
    assert calls and calls[0].name == "create_document"
    assert calls[0].arguments["filename"] == "企劃書"


def test_inline_salvage_ignores_unknown_tool_names():
    content = '{"name": "rm_rf_everything", "arguments": {"x": 1}}'
    assert _salvage_inline_tool_call(content, {"create_document"}) == []


@pytest.mark.asyncio
async def test_broken_json_round_trip_recovers(tmp_output_dir, monkeypatch, ctx, clean_term):
    """壞 JSON → 錯誤訊息回給模型 → 模型重試 → 成功。"""
    from app import orchestrator as orch

    term.update({"academic_year": "115"})
    bad = Reply(
        tool_calls=[
            ToolCall(id="c1", name="create_spreadsheet", arguments={"__parse_error__": "{{{壞掉"})
        ]
    )
    good = tool(
        "create_spreadsheet",
        filename="修好的表",
        sheets=[{"name": "A", "csv": "欄1,欄2\n1,2"}],
    )
    _fake, events = await run(orch, monkeypatch, [bad, good, say("好了")], "幫我做一份表", ctx)

    completed = [e for e in events if e["type"] == "tool_completed"]
    assert completed[0]["ok"] is False
    assert completed[-1]["ok"] is True
    assert [e for e in events if e["type"] == "artifact_ready"]
    assert not [e for e in events if e["type"] == "error"]


# ── 缺參數 ───────────────────────────────────────────────────

@pytest.mark.parametrize(
    "name,args,expect",
    [
        ("create_spreadsheet", {"filename": "x"}, "sheets"),
        ("create_document", {"filename": "x"}, "markdown"),
        ("create_slides", {"filename": "x"}, "slides_markdown"),
        ("create_google_form", {"filename": "x"}, "form_title"),
        ("search_knowledge", {}, "query"),
    ],
)
def test_missing_required_argument_names_the_field(name, args, expect):
    from app import tools

    r = tools.dispatch(name, args)
    assert not r["ok"]
    assert expect in r["message"], r["message"]


def test_extra_arguments_are_ignored(tmp_output_dir):
    from app import tools

    r = tools.dispatch(
        "create_spreadsheet",
        {"filename": "x", "sheets": "a,b\n1,2", "模型亂加的參數": "?", "destination": "local"},
    )
    assert r["ok"], r["message"]


# ── 歷史資料冒充今年 ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_stale_year_in_artifact_is_caught_and_repaired(tmp_output_dir, monkeypatch, ctx, clean_term):
    """規格必測：歷史文件包含舊日期/姓名/金額，不可以直接搬成今年的產出。"""
    from app import orchestrator as orch

    term.update({"academic_year": "115", "semester": "上學期"})

    stale = tool(
        "create_document",
        filename="期初茶會企劃書",
        markdown=(
            "# 期初茶會企劃書\n\n## 目標\n\n本學期是 113 學年度，目標六十人。\n\n"
            "## 流程\n\n社長：陳大明\n報到、體驗禪、手作。\n"
        ),
    )
    fixed = tool(
        "create_document",
        filename="期初茶會企劃書",
        markdown=(
            "# 期初茶會企劃書\n\n## 目標\n\n本學期目標六十人出席。\n\n"
            "## 流程\n\n社長：待填\n報到、體驗禪、手作。\n"
        ),
    )
    _fake, events = await run(orch, monkeypatch, [stale, fixed, say("修好了")], "用去年的資料做今年的期初茶會企劃", ctx)

    first = [e for e in events if e["type"] == "verification_result"][0]
    assert first["ok"] is False
    reasons = " ".join(first["errors"])
    assert "113" in reasons or "陳大明" in reasons
    assert [e for e in events if e["type"] == "repair_started"]
    assert [e for e in events if e["type"] == "artifact_ready"][-1]["verified"] is True


def test_unset_president_never_appears_in_artifact(tmp_output_dir, clean_term):
    from app import verification
    from app.tools import document

    term.update({"academic_year": "115"})
    r = document.create_document(
        filename="測試", markdown="# 企劃\n\n社長：張三\n\n這是內容說明段落，長度足夠通過檢查。"
    )
    from pathlib import Path

    report = verification.verify(Path(r["local_path"]), rules=["placeholder_for_unknown"])
    assert not report.ok


# ── 額度 / 連線 ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_quota_exhausted_gives_actionable_message(monkeypatch, ctx):
    from app import orchestrator as orch
    from app.llm import LLMError

    class Broke:
        model = "x"

        async def chat(self, *a, **k):
            raise LLMError("連續呼叫 NVIDIA API 失敗（可能是免費額度用完、達到每分鐘 40 次上限，或網路問題）。")

    monkeypatch.setattr(orch, "NvidiaClient", lambda **_: Broke())
    events = [ev async for ev in orch.run_turn(ctx, "你好", destination="local")]
    err = next(e for e in events if e["type"] == "error")
    assert "額度" in err["text"]


@pytest.mark.asyncio
async def test_unexpected_exception_does_not_leak_traceback(monkeypatch, ctx):
    from app import orchestrator as orch

    class Boom:
        model = "x"

        async def chat(self, *a, **k):
            raise ZeroDivisionError("internal detail")

    monkeypatch.setattr(orch, "NvidiaClient", lambda **_: Boom())
    events = [ev async for ev in orch.run_turn(ctx, "你好", destination="local")]
    err = next(e for e in events if e["type"] == "error")
    assert "Traceback" not in err["text"]
    assert "ZeroDivisionError" in err["text"]


def test_missing_api_key_is_explained():
    import asyncio

    from app.llm import LLMError

    client = NvidiaClient(api_key="")
    with pytest.raises(LLMError) as exc:
        asyncio.run(client.chat([{"role": "user", "content": "hi"}]))
    assert "build.nvidia.com" in str(exc.value)
