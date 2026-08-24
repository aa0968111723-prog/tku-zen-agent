"""來源證據模型、回答品質閘門與端到端整合（規格的 15 個必測情境）。

最高原則：沒有證據，不要下結論。對象不明，不要猜。
來源錯誤，不要完成。資料混淆，必須阻止輸出。
"""

from __future__ import annotations

import pytest

from app.research import claims as C
from app.research import entities as E
from app.research import verifier as V
from app.services.context import RequestContext
from tests.fakes import FakeLLM, say, tool


@pytest.fixture()
def ctx(tmp_db):
    uid = tmp_db.ensure_user("u_sv", is_local=True)
    return RequestContext(user_id=uid, session_id=tmp_db.create_session(uid))


async def run(orch, monkeypatch, script, message, ctx):
    fake = FakeLLM(script=script)
    monkeypatch.setattr(orch, "NvidiaClient", lambda **_: fake)
    events = [ev async for ev in orch.run_turn(ctx, message, destination="local")]
    return fake, events


def kinds(events):
    return [e["type"] for e in events]


def _ntut_source(**over) -> C.SourceRecord:
    base = dict(
        title="外校公開參考：其他禪學社IG公開資料 › 北科禪心領袖社",
        url="https://www.instagram.com/ntut_leadershipclub/",
        publisher="北科禪心領袖社",
        captured_at="2026-08-24",
        excerpt="公開定位：貼近生活的禪、禪心領袖、特質鍛鍊與領袖實踐。"
                "公開主題：禪定、舒壓、特質培養、大學生活升級、領袖養成。",
        source_type="official_instagram",
        entity_id="ntut-lead",
        school="國立臺北科技大學",
        organization="北科禪心領袖社",
        source_scope="external",
        authority_level="official",
        is_external=True,
    )
    base.update(over)
    return C.SourceRecord(**base).finalize()


def _scope(msg: str) -> tuple[E.ResearchScope, E.EntityResolution]:
    res = E.resolve(msg)
    return E.decide_scope(msg, res), res


# ── 證據模型 ─────────────────────────────────────────────────

def test_external_source_without_url_cannot_be_verified():
    """規格四：沒有 source_url／title／excerpt 不得標 verified。"""
    rec = _ntut_source(url="")
    assert rec.status == C.STATUS_INSUFFICIENT

    claim = C.ClaimRecord(claim="x", status=C.STATUS_VERIFIED).finalize()
    assert claim.status == C.STATUS_INSUFFICIENT


def test_official_fresh_source_is_verified():
    rec = _ntut_source()
    assert rec.status == C.STATUS_VERIFIED


def test_old_captured_at_is_stale():
    """規格情境 13：外校來源日期過舊。"""
    rec = _ntut_source(captured_at="2023-01-01")
    assert rec.status == C.STATUS_STALE


def test_internal_archive_from_past_year_is_marked_stale(index, clean_term):
    """規格情境 7：歷史資料要有清楚標籤，不是本學期事實。"""
    from app.services import current_term as term

    term.update({"academic_year": "115"})
    old = next(
        c for c in index.chunks
        if c.meta and c.meta.source_scope == "internal" and c.meta.academic_year == "111"
    )
    rec = C.source_from_chunk(old, current_year="115")
    assert rec.status == C.STATUS_STALE
    assert rec.is_current is False
    assert rec.school == "淡江大學"


# ── 回答品質閘門 ─────────────────────────────────────────────

def test_claim_supported_by_excerpt_is_verified():
    scope, res = _scope("北科禪心領袖社的 IG 都發什麼")
    review = V.review_answer(
        "【已驗證資料】北科禪心領袖社的公開定位是貼近生活的禪、特質鍛鍊與領袖實踐。",
        scope=scope, resolution=res, sources=[_ntut_source()],
    )
    assert review.verdict in {"allow", "degrade"}
    assert any(c.status == C.STATUS_VERIFIED for c in review.claims)
    assert review.claims[0].source_url.startswith("https://")


def test_unsupported_summary_is_downgraded_not_asserted():
    """規格情境 14：來源內容與 AI 摘要不一致 → 標推測、降級。"""
    scope, res = _scope("北科禪心領袖社的茶會")
    review = V.review_answer(
        "北科禪心領袖社的茶會以生活壓力作為文案切入點，主打冥想手環兌換活動。",
        scope=scope, resolution=res, sources=[_ntut_source()],
    )
    assert review.verdict == "degrade"
    assert review.research_status in {V.RESEARCH_PARTIAL, V.RESEARCH_UNVERIFIED}
    assert any(n == V.PARTIAL_NOTICE for n in review.notices)
    assert all(c.status != C.STATUS_VERIFIED for c in review.claims)


def test_confident_claim_with_no_source_is_blocked():
    """規格情境 10：AI 對沒有來源的內容提出肯定結論 → 阻止輸出。"""
    scope, res = _scope("我指的是政大的正式社團，請只使用可驗證的官方公開來源研究。")
    review = V.review_answer(
        "政大禪學社的茶會以生活壓力作為文案切入點，效果很好。",
        scope=scope, resolution=res, sources=[],
    )
    assert review.verdict == "block"
    assert review.research_status == V.RESEARCH_BLOCKED
    assert any(f.rule == "claim_without_source" for f in review.errors)


def test_source_school_mismatch_is_blocked():
    """規格情境 8：來源（世新）與回答學校（北科）不一致 → 阻止。"""
    scope, res = _scope("北科禪心領袖社的招生貼文怎麼寫")
    shu = _ntut_source(
        entity_id="shu-zen", school="世新大學", organization="世新大學禪學社",
        url="https://www.instagram.com/shuzen_0419/",
        title="外校公開參考 › 世新大學禪學社",
    )
    review = V.review_answer(
        "北科禪心領袖社的招生貼文都以禪境與內在探索為主軸。",
        scope=scope, resolution=res, sources=[shu],
    )
    assert review.verdict == "block"
    assert any(f.rule == "claim_without_source" for f in review.errors)


def test_tku_signature_activity_attributed_to_external_is_contamination():
    """規格情境 9：回答出現「浮游花手作」但來源是淡江 → 資料污染，阻止輸出。"""
    scope, res = _scope("北科禪心領袖社的茶會怎麼做")
    review = V.review_answer(
        "北科禪心領袖社的期初茶會以浮游花手作為主軸。",
        scope=scope, resolution=res, sources=[_ntut_source()],
    )
    assert review.verdict == "block"
    assert review.contamination is not None
    assert any(f.rule == "data_contamination" for f in review.errors)


def test_comparative_sentence_mentioning_tku_is_not_contamination():
    """比較句「淡江的浮游花 vs 北科的做法」是合法的比較分析。"""
    scope, res = _scope("幫我比較北科禪心領袖社跟淡江的茶會")
    review = V.review_answer(
        "【淡江內部資料】淡江的期初茶會以浮游花手作聞名。\n"
        "【外校已驗證資料】北科禪心領袖社的公開定位是貼近生活的禪、特質鍛鍊與領袖實踐。",
        scope=scope, resolution=res, sources=[_ntut_source()],
    )
    assert review.verdict != "block"
    assert review.contamination is None


def test_labeled_speculation_is_allowed():
    scope, res = _scope("北科禪心領袖社的 IG")
    review = V.review_answer(
        "【已驗證資料】北科禪心領袖社的公開定位是貼近生活的禪、特質鍛鍊與領袖實踐。\n"
        "【可能推測】北科禪心領袖社可能也會在期初辦體驗活動。",
        scope=scope, resolution=res, sources=[_ntut_source()],
    )
    # 已標記的推測不觸發 speculation_as_fact
    assert not any(f.rule == "speculation_as_fact" for f in review.findings)


def test_internal_mode_blocks_external_school_claims():
    """規格情境 4：內部模式的回答不得對外校下結論。"""
    scope, res = _scope("只用淡江內部資料，整理期初茶會做法")
    review = V.review_answer(
        "淡江的茶會流程如上。另外政大的茶會都是用浮游花手作。",
        scope=scope, resolution=res, sources=[],
    )
    assert review.verdict == "block"
    assert any(f.rule == "external_claim_in_internal_mode" for f in review.errors)


def test_internal_only_answer_gets_attribution_notice():
    scope, res = _scope("只用淡江內部資料，整理期初茶會做法")
    review = V.review_answer(
        "淡江的期初茶會通常包含報到、體驗禪與手作。",
        scope=scope, resolution=res, sources=[],
    )
    assert review.verdict == "allow"
    assert V.INTERNAL_ATTRIBUTION in review.notices


def test_all_stale_sources_add_warning():
    scope, res = _scope("北科禪心領袖社的 IG")
    review = V.review_answer(
        "【已驗證資料】北科禪心領袖社的公開定位是貼近生活的禪、特質鍛鍊與領袖實踐。",
        scope=scope, resolution=res, sources=[_ntut_source(captured_at="2023-01-01")],
    )
    assert any(f.rule == "stale_sources" for f in review.findings)
    assert review.verdict == "degrade"


# ── 端到端（orchestrator）────────────────────────────────────

@pytest.mark.asyncio
async def test_zhengda_ne_asks_before_answering(tmp_output_dir, monkeypatch, ctx):
    """規格情境 1／九：「政大呢」不能直接回答——先反問，不呼叫模型。"""
    from app import orchestrator as orch

    fake, events = await run(orch, monkeypatch, [say("不該被呼叫")], "政大呢", ctx)
    seq = kinds(events)
    assert "clarification_needed" in seq
    assert "task_completed" not in seq
    assert fake.call_count == 0, "對象未確認前不得呼叫模型"

    card = next(e for e in events if e["type"] == "clarification_needed")
    assert [o["label"] for o in card["options"]]
    assert card["topic_options"]
    status = next(e for e in events if e["type"] == "research_status")
    assert status["status"] == V.RESEARCH_NEEDS_USER


@pytest.mark.asyncio
async def test_zhengda_zen_club_question_asks_too(tmp_output_dir, monkeypatch, ctx):
    """規格情境 2：「政大禪學社的茶會怎麼做」一樣要先確認對象。"""
    from app import orchestrator as orch

    fake, events = await run(orch, monkeypatch, [say("x")], "政大禪學社的茶會怎麼做", ctx)
    assert "clarification_needed" in kinds(events)
    assert fake.call_count == 0


@pytest.mark.asyncio
async def test_clarified_zhengda_gets_honest_no_source_reply(tmp_output_dir, monkeypatch, ctx):
    """規格情境 5：確認對象後仍查無來源 → 誠實回覆，不生成、不顯示完成。"""
    from app import orchestrator as orch

    fake, events = await run(
        orch, monkeypatch, [say("不該被呼叫")],
        "我指的是政大的正式社團，請只使用可驗證的官方公開來源研究，主題是茶會內容。",
        ctx,
    )
    assert fake.call_count == 0, "沒有證據池就不該讓模型生成"
    msg = next(e for e in events if e["type"] == "message")
    assert "沒有足夠公開來源" in msg["text"]
    assert "【研究對象】" in msg["text"] and "國立政治大學" in msg["text"]
    done = next(e for e in events if e["type"] == "task_completed")
    assert done["research_status"] == V.RESEARCH_NO_SOURCE
    assert done["research_status_label"] == "找不到可靠來源"


@pytest.mark.asyncio
async def test_user_provided_ig_does_not_fabricate(tmp_output_dir, monkeypatch, ctx):
    """規格情境 3：使用者指定政大官方 IG——系統沒有該帳號資料，仍不得編造。"""
    from app import orchestrator as orch

    fake, events = await run(
        orch, monkeypatch, [say("x")],
        "政大的官方帳號是 @nccu_zen_official，幫我研究他們的茶會文宣",
        ctx,
    )
    assert fake.call_count == 0
    msg = next(e for e in events if e["type"] == "message")
    assert "沒有足夠公開來源" in msg["text"]


@pytest.mark.asyncio
async def test_ntu_leadership_no_sources_is_honest(tmp_output_dir, monkeypatch, ctx):
    """台大領袖社：registry 知道它存在，但沒有已收錄來源 → 找不到可靠來源。"""
    from app import orchestrator as orch

    fake, events = await run(orch, monkeypatch, [say("x")], "台大領袖社的 IG 怎麼經營", ctx)
    assert fake.call_count == 0
    msg = next(e for e in events if e["type"] == "message")
    assert "台大領袖社" in msg["text"]
    assert "沒有" in msg["text"]


@pytest.mark.asyncio
async def test_verified_external_research_emits_source_cards(tmp_output_dir, monkeypatch, ctx):
    """北科（有已驗證來源）：來源卡帶網址與狀態，claims 可驗證。"""
    from app import orchestrator as orch

    answer = (
        "【研究對象】國立臺北科技大學 北科禪心領袖社\n"
        "【已驗證資料】北科禪心領袖社的公開定位是貼近生活的禪、禪心領袖、特質鍛鍊與領袖實踐。\n"
        "【可能推測】無。\n"
        "【淡江可採用建議】淡江可以把禪定包裝成貼近生活的成長體驗。\n"
        "【尚待確認】無。"
    )
    _fake, events = await run(
        orch, monkeypatch, [say(answer)], "北科禪心領袖社的 IG 都怎麼寫招生貼文？", ctx,
    )
    cards_ev = next(e for e in events if e["type"] == "source_cards")
    assert cards_ev["cards"], "應該要有來源卡"
    card = cards_ev["cards"][0]
    assert card["school"] == "國立臺北科技大學"
    assert card["url"].startswith("https://www.instagram.com/")
    assert card["status"] in {"verified", "stale"}
    assert card["captured_at"]

    review_ev = next(e for e in events if e["type"] == "answer_review")
    assert any(c["status"] == "verified" for c in review_ev["claims"])
    done = next(e for e in events if e["type"] == "task_completed")
    assert done["verdict"] != "block"


@pytest.mark.asyncio
async def test_contaminated_answer_is_blocked_end_to_end(tmp_output_dir, monkeypatch, ctx):
    """規格情境 9／八：浮游花被寫成北科做法 → 顯示歸屬錯誤卡、擋下回答。"""
    from app import orchestrator as orch

    _fake, events = await run(
        orch, monkeypatch,
        [say("北科禪心領袖社的期初茶會以浮游花手作為主軸，現場帶大家做浮游花。")],
        "北科禪心領袖社的茶會怎麼做？", ctx,
    )
    seq = kinds(events)
    assert "contamination_warning" in seq
    warning = next(e for e in events if e["type"] == "contamination_warning")
    assert "混入淡江內部資料" in warning["message"]
    assert [a["label"] for a in warning["actions"]]

    final = [e for e in events if e["type"] == "message"][-1]
    assert V.BLOCK_NOTICE in final["text"]
    assert "浮游花手作為主軸" not in final["text"], "被擋下的內容不得照樣輸出"
    done = next(e for e in events if e["type"] == "task_completed")
    assert done["research_status"] == V.RESEARCH_BLOCKED


@pytest.mark.asyncio
async def test_internal_only_flow_stays_internal(tmp_output_dir, monkeypatch, ctx, clean_term):
    """規格情境 4：只用淡江內部資料——回答附歸屬說明，狀態為內部模式。"""
    from app import orchestrator as orch
    from app.services import current_term as term

    term.update({"academic_year": "115"})
    fake, events = await run(
        orch, monkeypatch, [say("期初茶會的做法整理如上。")],
        "只用淡江內部資料，幫我整理期初茶會的重點做法", ctx,
    )
    prompt = fake.system_prompt()
    assert "研究模式：淡江內部資料" in prompt
    final = [e for e in events if e["type"] == "message"][-1]
    assert V.INTERNAL_ATTRIBUTION in final["text"]
    done = next(e for e in events if e["type"] == "task_completed")
    assert done["research_status"] == V.RESEARCH_INTERNAL


@pytest.mark.asyncio
async def test_external_prompt_carries_required_format(tmp_output_dir, monkeypatch, ctx):
    """規格六：外校研究的輸出格式要求要進 system prompt。"""
    from app import orchestrator as orch

    fake, _events = await run(
        orch, monkeypatch, [say("好")], "北科禪心領袖社的 IG 都發什麼", ctx,
    )
    prompt = fake.system_prompt()
    for label in ("【研究對象】", "【已驗證資料】", "【來源整理】", "【可能推測】",
                  "【淡江可採用建議】", "【尚待確認】"):
        assert label in prompt
    assert "【外校已驗證資料】" in prompt


def test_unverified_external_facts_blocked_in_outward_copy(tmp_path, clean_term):
    """規格情境 15：對外文宣含未驗證外校資訊（校名／日期）→ 產出驗證擋下。"""
    from app import verification
    from app.services import current_term as term

    term.update({"academic_year": "115"})
    path = tmp_path / "post.md"
    path.write_text(
        "# 招生貼文\n\n政大禪學社都這樣辦茶會，我們 2026/9/30 也來一場！",
        encoding="utf-8",
    )
    report = verification.verify(path, rules=["verify_social_copy"])
    assert not report.ok
    messages = " ".join(i.message for i in report.errors)
    assert "政大" in messages or "外校" in messages
