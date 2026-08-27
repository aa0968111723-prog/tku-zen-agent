"""Phase 5：Skill 路由與動態工具集。

規格驗收：
  · 新增 20 個 mock tools 後，現有 5 類任務的 tool selection accuracy 不下降
  · tool schemas 不應在每次 request 都全部送給模型
"""

from __future__ import annotations

import pytest

from app import skills, tools

# (訊息, 期望 skill)
ROUTING_CASES = [
    ("幫我做期初茶會的企劃書", "event_planning"),
    ("排一份這學期的社課排程", "event_planning"),
    ("挑戰營的細流要怎麼做", "event_planning"),
    ("幫我寫期末社大的流程", "event_planning"),
    ("這學期招生要怎麼規劃", "recruitment"),
    ("幫我寫三則招生 IG 貼文", "social_publicity"),  # v3 起 IG 貼文歸網宣工作台
    ("路宣排班表", "recruitment"),
    ("做一份社團評鑑的年度績效報告", "evaluation"),
    ("課外組要的成果報告", "evaluation"),
    ("幫我做學年度經費預算表", "finance"),
    ("核銷要準備什麼", "finance"),
    ("幫我做幹部交接手冊", "handover"),
    ("新幹部要知道什麼", "handover"),
    ("做一份社課回饋問卷", "google_workspace"),
    ("新生報名表單", "google_workspace"),
    ("我們社團是什麼樣的社團", "knowledge"),
    ("青年領袖十二項特質是什麼", "knowledge"),
]


@pytest.mark.parametrize("message,expected", ROUTING_CASES)
def test_routing(message, expected):
    assert skills.route(message).skill.name == expected, f"{message} → {skills.route(message).skill.name}"


def test_artifact_intent_detection():
    assert skills.wants_artifact("幫我做一份表") is True
    assert skills.wants_artifact("產出一份企劃書") is True
    assert skills.wants_artifact("社長是誰") is False


def test_question_about_president_is_knowledge_not_document():
    r = skills.route("今年社長是誰")
    assert r.skill.name == "knowledge"
    assert r.produce_artifact is False


def test_make_verb_upgrades_knowledge_to_producing_skill():
    """「幫我做一份社團簡介」雖然命中查詢關鍵字，但明顯要產檔。"""
    r = skills.route("幫我做一份社團簡介文件")
    assert r.skill.artifacts_expected, f"路由到 {r.skill.name}，不會產檔"


def test_unknown_input_falls_back_safely():
    r = skills.route("zzzz")
    assert r.skill.name in {"documents", "knowledge"}


# ── 動態工具集 ───────────────────────────────────────────────

def test_每個_skill_都包含基本工具():
    for s in skills.SKILLS:
        names = s.tool_names()
        assert "search_knowledge" in names
        assert "get_current_term" in names


def test_schemas_for_returns_subset():
    subset = tools.schemas_for(skills.SKILL_BY_NAME["finance"].tool_names())
    names = {s["function"]["name"] for s in subset}
    assert "create_spreadsheet" in names
    assert "create_google_form" not in names, "財務任務不需要表單工具"
    assert len(subset) < len(tools.SCHEMAS)


def test_schemas_for_ignores_unknown_names():
    subset = tools.schemas_for(["search_knowledge", "尚未實作的工具"])
    assert {s["function"]["name"] for s in subset} == {"search_knowledge"}


def test_schemas_for_empty_returns_empty():
    assert tools.schemas_for([]) == []


# ── 20 個 mock tools 不能拖垮路由 ─────────────────────────────

@pytest.fixture()
def twenty_mock_tools():
    """模擬未來擴充到 27 個工具的情況。"""
    created = []
    for i in range(20):
        name = f"mock_tool_{i:02d}"

        def fn(_i=i, **kwargs):
            return {"ok": True, "message": f"mock {_i}"}

        tools.register(
            name,
            fn,
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": f"模擬工具 {i}：處理社團的某種雜項工作，可以產生報表與文件。",
                    "parameters": {"type": "object", "properties": {"x": {"type": "string"}}},
                },
            },
            label=f"模擬工具{i}",
        )
        created.append(name)
    yield created
    for name in created:
        tools.unregister(name)


@pytest.mark.parametrize("message,expected", ROUTING_CASES)
def test_routing_unaffected_by_extra_tools(twenty_mock_tools, message, expected):
    assert skills.route(message).skill.name == expected


def test_exposed_tool_count_stays_small_with_activity_tools(twenty_mock_tools):
    # The public-source/Perplexity tools are additive to the original 26-tool
    # registry. Keep the count explicit so an accidental registry explosion is
    # still caught while acknowledging the six approved research tools.
    assert len(tools.all_names()) == 52, "應包含 32 個正式工具與 20 個 mock 工具"
    for s in skills.SKILLS:
        exposed = tools.schemas_for(s.tool_names())
        assert len(exposed) <= 8, f"{s.name} 暴露了 {len(exposed)} 個工具，太多"


def test_mock_tools_are_never_exposed_to_model(twenty_mock_tools):
    for s in skills.SKILLS:
        names = {x["function"]["name"] for x in tools.schemas_for(s.tool_names())}
        assert not any(n.startswith("mock_tool_") for n in names)


@pytest.mark.asyncio
async def test_orchestrator_only_sends_skill_tools(tmp_db, tmp_output_dir, monkeypatch, twenty_mock_tools):
    """端到端：確認送給模型的 tools 是篩過的子集。"""
    from app import orchestrator as orch
    from app.services.context import RequestContext
    from tests.fakes import FakeLLM, say

    fake = FakeLLM(script=[say("好")])
    monkeypatch.setattr(orch, "NvidiaClient", lambda **_: fake)
    uid = tmp_db.ensure_user("u_t", is_local=True)
    ctx = RequestContext(user_id=uid, session_id=tmp_db.create_session(uid))

    async for _ in orch.run_turn(ctx, "幫我做學年度經費預算表", destination="local"):
        pass

    offered = set(fake.tools_offered(0))
    assert "create_spreadsheet" in offered
    assert "create_google_form" not in offered
    assert not any(n.startswith("mock_tool_") for n in offered)
    assert len(offered) <= 7, f"送了 {len(offered)} 個工具給模型"
