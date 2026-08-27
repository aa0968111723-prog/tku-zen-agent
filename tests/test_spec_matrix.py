"""規格第十五節的 20 個必測情境——端到端行為矩陣。

每個情境都跑真實的 orchestrator 管線（只有模型層是 FakeLLM），
驗證的是「系統會不會做出正確的行為」，不是「函式回傳什麼」。
"""

from __future__ import annotations

import pytest

from app.research import verifier as V
from app.services.context import RequestContext
from tests.fakes import FakeLLM, say, tool


@pytest.fixture()
def ctx(tmp_db):
    uid = tmp_db.ensure_user("u_matrix", is_local=True)
    return RequestContext(user_id=uid, session_id=tmp_db.create_session(uid))


async def run(orch, monkeypatch, script, message, ctx, **kw):
    fake = FakeLLM(script=script)
    monkeypatch.setattr(orch, "NvidiaClient", lambda **_: fake)
    events = [ev async for ev in orch.run_turn(ctx, message, destination="local", **kw)]
    return fake, events


def kinds(events):
    return [e["type"] for e in events]


def _plan_md(title: str) -> str:
    return f"# {title}\n\n## 活動目標\n讓新生認識社團。\n\n## 活動流程\n待填\n\n## 時間地點\n待填"


# ── 1. 政大呢 ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_01_zhengda_ne_asks_first(tmp_output_dir, monkeypatch, ctx):
    from app import orchestrator as orch

    fake, events = await run(orch, monkeypatch, [say("不該被呼叫")], "政大呢", ctx)
    assert fake.call_count == 0, "對象不明不得生成"
    assert "clarification_needed" in kinds(events)
    assert "retrieval_started" not in kinds(events), "反問前不得檢索"


# ── 2. 研究政大官方 IG ───────────────────────────────────────

@pytest.mark.asyncio
async def test_02_research_zhengda_official_ig(tmp_output_dir, monkeypatch, ctx):
    from app import orchestrator as orch

    fake, events = await run(orch, monkeypatch, [say("不該被呼叫")], "研究政大的官方 IG 怎麼經營", ctx)
    assert fake.call_count == 0
    assert "clarification_needed" in kinds(events)


# ── 3. 查詢淡江本學期活動 ────────────────────────────────────

@pytest.mark.asyncio
async def test_03_query_current_term_activity(tmp_output_dir, monkeypatch, ctx, clean_term):
    from app import orchestrator as orch
    from app.services import current_term as term

    term.update({"academic_year": "115", "semester": "上學期"})
    _fake, events = await run(orch, monkeypatch, [say("本學期是 115 學年度上學期。")], "本學期是哪個學年度", ctx)
    assert "clarification_needed" not in kinds(events)
    assert "task_completed" in kinds(events)


# ── 4. 只使用淡江內部資料 ────────────────────────────────────

@pytest.mark.asyncio
async def test_04_internal_only_mode(tmp_output_dir, monkeypatch, ctx):
    from app import orchestrator as orch

    fake, events = await run(
        orch, monkeypatch, [say("期初茶會通常包含報到、體驗與分享。")],
        "只用淡江內部資料，期初茶會通常怎麼安排", ctx,
    )
    assert "研究模式：淡江內部資料" in fake.system_prompt()
    done = next(e for e in events if e["type"] == "task_completed")
    assert done["research_status"] == V.RESEARCH_INTERNAL


# ── 5. 淡江與政大比較 ────────────────────────────────────────

@pytest.mark.asyncio
async def test_05_compare_tku_and_zhengda(tmp_output_dir, monkeypatch, ctx):
    from app import orchestrator as orch

    fake, events = await run(
        orch, monkeypatch, [say("不該被呼叫")], "幫我比較淡江跟政大的茶會做法差異", ctx,
    )
    assert fake.call_count == 0, "政大對象未確認前不得生成比較"
    assert "clarification_needed" in kinds(events)


# ── 6. 搜尋不到可靠來源 ──────────────────────────────────────

@pytest.mark.asyncio
async def test_06_no_reliable_source(tmp_output_dir, monkeypatch, ctx):
    from app import orchestrator as orch

    fake, events = await run(orch, monkeypatch, [say("不該被呼叫")], "研究台大領袖社的招生方式", ctx)
    assert fake.call_count == 0
    done = next(e for e in events if e["type"] == "task_completed")
    assert done["research_status"] == V.RESEARCH_NO_SOURCE
    msg = [e for e in events if e["type"] == "message"][-1]
    assert "沒有足夠公開來源" in msg["text"]


# ── 7. 外校來源與淡江資料混合 ────────────────────────────────

@pytest.mark.asyncio
async def test_07_mixed_sources_are_separated(tmp_output_dir, monkeypatch, ctx):
    from app import orchestrator as orch

    fake, _events = await run(
        orch, monkeypatch, [say("北科的公開定位是特質鍛鍊。")],
        "幫我比較北科禪心領袖社跟淡江的社群經營", ctx,
    )
    prompt = fake.system_prompt()
    assert "【外校已驗證資料】" in prompt and "【淡江內部資料】" in prompt


# ── 8. 歷史資料冒充本學期 ────────────────────────────────────

@pytest.mark.asyncio
async def test_08_history_not_passed_as_current(tmp_output_dir, monkeypatch, ctx, clean_term):
    from app import orchestrator as orch
    from app.services import current_term as term

    term.update({"academic_year": "115"})
    fake, _events = await run(orch, monkeypatch, [say("好")], "期初茶會通常怎麼辦", ctx)
    prompt = fake.system_prompt()
    assert "歷年" in prompt and ("不可以當成今年" in prompt or "不是今年" in prompt or "當年的" in prompt)


# ── 9. 來源與學校不一致 ──────────────────────────────────────

def test_09_source_school_mismatch_blocks():
    from app.research import claims as C
    from app.research import entities as E

    msg = "北科禪心領袖社的招生貼文怎麼寫"
    res = E.resolve(msg)
    scope = E.decide_scope(msg, res)
    shu = C.SourceRecord(
        title="外校公開參考 › 世新大學禪學社",
        url="https://www.instagram.com/shuzen_0419/",
        publisher="世新大學禪學社", captured_at="2026-08-20",
        excerpt="公開定位：貼近生活的禪。", source_type="official_instagram",
        entity_id="shu-zen", school="世新大學", organization="世新大學禪學社",
        source_scope="external", authority_level="official", is_external=True,
    ).finalize()
    review = V.review_answer(
        "北科禪心領袖社的招生貼文都以禪境為主軸。", scope=scope, resolution=res, sources=[shu],
    )
    assert review.verdict == "block"


# ── 10. 使用者中途修改需求 ───────────────────────────────────

@pytest.mark.asyncio
async def test_10_user_changes_target_midway(tmp_output_dir, monkeypatch, ctx):
    from app import orchestrator as orch

    await run(orch, monkeypatch, [say("好")], "研究北醫禪學社的 IG", ctx)
    _fake, events = await run(orch, monkeypatch, [say("好")], "接續剛才，改研究世新禪學社", ctx)
    understood = next(e for e in events if e["type"] == "task_understood")
    assert understood["research_mode"] in {"external", "comparative"}


# ── 11. 使用者停止生成 ───────────────────────────────────────

def test_11_stop_generation_releases_lock(tmp_db, monkeypatch):
    import asyncio as aio

    from fastapi.testclient import TestClient

    from app import main as main_mod
    from app.main import TURNS, app

    async def slow_turn(ctx, message, *, destination, model=None, attachments=None, requested=None):
        yield {"type": "message", "text": "開始"}
        await aio.sleep(5)
        yield {"type": "message", "text": "不該送出"}

    monkeypatch.setattr(main_mod.orchestrator, "run_turn", slow_turn)
    monkeypatch.setattr(main_mod, "SSE_HEARTBEAT_SECONDS", 0.05)
    with TestClient(app) as client:
        sid = client.post("/api/session", json={}).json()["session_id"]

        import threading

        def cancel_soon():
            import time as _t
            _t.sleep(0.5)
            client.post("/api/chat/cancel", json={"session_id": sid})

        threading.Thread(target=cancel_soon, daemon=True).start()
        body = client.post("/api/chat", json={"session_id": sid, "message": "測試"}).text
    assert '"type": "cancelled"' in body
    assert "不該送出" not in body
    assert TURNS.start(sid) is not None, "取消後鎖必須釋放"
    TURNS.finish(sid)


# ── 12. 產出後修改單頁 ───────────────────────────────────────

@pytest.mark.asyncio
async def test_12_edit_previous_artifact(tmp_output_dir, monkeypatch, ctx):
    from app import orchestrator as orch

    await run(
        orch, monkeypatch,
        [tool("create_document", filename="期初茶會企劃書", markdown=_plan_md("期初茶會企劃書")), say("完成")],
        "幫我做期初茶會企劃書", ctx,
    )
    _fake, events = await run(
        orch, monkeypatch,
        [tool("create_document", filename="期初茶會企劃書", markdown=_plan_md("期初茶會企劃書")), say("已更新")],
        "接續剛才，把活動流程再寫細一點", ctx,
    )
    arts = [e for e in events if e["type"] == "artifact_ready"]
    assert arts and arts[0]["version"] >= 2, "改稿要進版本鏈，不是開新檔"


# ── 13. 研究轉網宣 ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_13_research_to_publicity_composite(tmp_output_dir, monkeypatch, ctx):
    from app import orchestrator as orch

    _fake, events = await run(
        orch, monkeypatch,
        [tool("create_social_post", filename="招生貼文", topic="期初茶會", content="歡迎新朋友來喝杯茶。"), say("好了")],
        "研究其他學校的社群做法後，幫我寫一則淡江的招生貼文", ctx,
    )
    understood = next(e for e in events if e["type"] == "task_understood")
    assert understood["task_sequence"], "複合任務要有明確依賴序列"


# ── 14. 網宣轉企劃書 ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_14_publicity_to_plan(tmp_output_dir, monkeypatch, ctx):
    from app import orchestrator as orch

    await run(
        orch, monkeypatch,
        [tool("create_social_post", filename="招生貼文", topic="期初茶會", content="歡迎新朋友。"), say("好")],
        "幫我寫一則期初茶會招生貼文", ctx,
    )
    _fake, events = await run(
        orch, monkeypatch,
        [tool("create_document", filename="期初茶會企劃書", markdown=_plan_md("期初茶會企劃書")), say("完成")],
        "接續剛才，把它擴寫成完整企劃書", ctx,
    )
    assert "artifact_ready" in kinds(events)


# ── 15. 任務重新開啟 ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_15_reopen_task(tmp_output_dir, monkeypatch, ctx):
    from app import orchestrator as orch

    await run(orch, monkeypatch, [say("好")], "幫我做期初茶會企劃書", ctx)
    _fake, events = await run(orch, monkeypatch, [say("已暫停")], "暫停", ctx)
    assert "task_paused" in kinds(events)
    fake, events = await run(
        orch, monkeypatch,
        [tool("create_document", filename="期初茶會企劃書", markdown=_plan_md("期初茶會企劃書")), say("完成")],
        "繼續", ctx,
    )
    assert "task_resumed" in kinds(events)
    assert fake.call_count > 0, "繼續要真的續跑"


# ── 16. 外部工具失敗 ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_16_tool_failure_is_retryable(tmp_output_dir, monkeypatch, ctx):
    from app import orchestrator as orch

    def boom(name, args):
        raise RuntimeError("模擬工具爆炸")

    monkeypatch.setattr(orch.tools, "dispatch", boom)
    _fake, events = await run(
        orch, monkeypatch,
        [tool("create_document", filename="企劃書", markdown="# 企劃書"), say("我沒辦法完成")],
        "幫我做期初茶會企劃書", ctx,
    )
    completed = [e for e in events if e["type"] == "tool_completed"]
    assert completed and not completed[0]["ok"]
    assert completed[0]["alternative"], "失敗要給替代方案"
    assert "task_failed" in kinds(events)


# ── 17. 來源過期 ─────────────────────────────────────────────

def test_17_stale_source_is_flagged():
    from app.research import claims as C

    rec = C.SourceRecord(
        title="外校公開參考 › 北科", url="https://www.instagram.com/ntut_leadershipclub/",
        publisher="北科禪心領袖社", captured_at="2023-01-01", excerpt="公開定位：禪心領袖。",
        source_type="official_instagram", entity_id="ntut-lead",
        source_scope="external", authority_level="official", is_external=True,
    ).finalize()
    assert rec.status == C.STATUS_STALE


# ── 18. 兩個來源互相矛盾 ─────────────────────────────────────

def test_18_conflicting_sources_flagged():
    from app.research import entities as E

    conflicts = [{
        "kind": "same_year_conflict", "field": "president", "year": "115",
        "entity_id": E.HOME_ENTITY_ID,
        "values": [{"value": "林小華"}, {"value": "陳大明"}],
    }]
    review = V.review_answer(
        "本學期社長是林小華。",
        scope=E.ResearchScope(mode=E.ResearchMode.INTERNAL),
        resolution=E.resolve("社長是誰"), sources=[], conflicts=conflicts,
    )
    assert any(f.rule == "conflicted_sources" for f in review.findings)
    assert review.verdict == "degrade"


# ── 19. 缺少時間與地點 ───────────────────────────────────────

@pytest.mark.asyncio
async def test_19_missing_facts_are_listed(tmp_output_dir, monkeypatch, ctx, clean_term):
    from app import orchestrator as orch
    from app.services import current_term as term

    term.update({"academic_year": "115"})
    _fake, events = await run(orch, monkeypatch, [say("好")], "幫我做期初茶會企劃書", ctx)
    plan = next(e for e in events if e["type"] == "plan_created")
    assert isinstance(plan["missing_facts"], list)


# ── 20. 低可信度內容試圖對外使用 ─────────────────────────────

def test_20_low_confidence_blocks_external_use():
    """對外文案不得含外校專名（防冒名／防抄襲）。"""
    from app import verification
    from app.research.entities import external_name_lexicon

    report = verification.Report(filename="招生貼文.md", kind="md")
    verification.verify_social_copy(
        "快來參加淡江期初茶會！我們參考了北科禪心領袖社的做法，和 @ntut_leadershipclub 一樣有趣。",
        external_names=external_name_lexicon(),
        report=report,
    )
    assert not report.ok, "對外文案含外校專名必須擋下"
    assert report.errors
