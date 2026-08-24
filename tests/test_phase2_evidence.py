"""第二階段：證據模型、可信度閘門、來源卡。

稽核對應：
  · 漏洞 24：污染詞庫（17 招牌詞）外的淡江內容被寫成外校做法只 degrade
    → 內部摘錄重疊兜底，error/block
  · 漏洞 18：comparative 模式把外校值列成衝突並指示「以淡江為準」
    → conflicts 加 entity 維度
  · 半成品 19：STATUS_CONFLICTED 全 codebase 零賦值 → claim 層真實賦值
  · 半成品 20：partial claim 漏日期欄位
  · 半成品 21：classify_source 永不回 wrong_entity → 支援研究對象比對
  · 對抗審查：_DISCLAIMER 不能攔「知識庫沒有政大的資料」的誠實拒答
"""

from __future__ import annotations

from app.rag.conflicts import detect_conflicts
from app.research import claims as C
from app.research import entities as E
from app.research import verifier as V


def _ntut_scope() -> tuple[E.ResearchScope, E.EntityResolution]:
    msg = "研究北科禪心領袖社的 IG"
    res = E.resolve(msg)
    return E.decide_scope(msg, res), res


def _ntut_source(**over) -> C.SourceRecord:
    base = dict(
        title="外校公開參考 › 北科禪心領袖社",
        url="https://www.instagram.com/ntut_leadershipclub/",
        publisher="北科禪心領袖社",
        captured_at="2026-08-20",
        excerpt="公開定位：貼近生活的禪、禪心領袖、特質鍛鍊與領袖實踐。",
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


def _home_source(excerpt: str) -> C.SourceRecord:
    return C.SourceRecord(
        title="114學年度 期初茶會企劃書",
        publisher="淡江大學領袖禪學社",
        excerpt=excerpt,
        source_type="internal_archive",
        entity_id=E.HOME_ENTITY_ID,
        school=E.HOME_SCHOOL,
        organization="淡江大學領袖禪學社",
        source_scope="internal",
        authority_level="archive",
        is_external=False,
    ).finalize()


# ── 污染兜底：詞庫外的淡江內容被寫成外校做法 ─────────────────

def test_internal_overlap_contamination_blocks():
    """淡江內部摘錄的內容（非 17 招牌詞）被寫成北科做法 → block。"""
    scope, res = _ntut_scope()
    internal = _home_source(
        "茶會當天安排香氛蠟燭手作體驗，由生活組準備材料包，參加者完成後可帶回宿舍。"
    )
    answer = "北科禪心領袖社的茶會安排香氛蠟燭手作體驗，由生活組準備材料包，參加者完成後可帶回宿舍。"
    review = V.review_answer(answer, scope=scope, resolution=res, sources=[_ntut_source(), internal])
    assert review.verdict == "block"
    assert any(f.rule == "data_contamination" for f in review.findings)
    assert any(c.status == C.STATUS_WRONG_ENTITY for c in review.claims)


def test_comparison_sentence_naming_home_is_not_contamination():
    """「北科…而淡江…」的比較句合法，不算污染。"""
    scope, res = _ntut_scope()
    internal = _home_source("茶會當天安排香氛蠟燭手作體驗，由生活組準備材料包。")
    answer = "北科禪心領袖社主打特質鍛鍊與領袖實踐，而淡江的茶會安排香氛蠟燭手作體驗，由生活組準備材料包。"
    review = V.review_answer(answer, scope=scope, resolution=res, sources=[_ntut_source(), internal])
    assert not any(f.rule == "data_contamination" for f in review.findings)


# ── _DISCLAIMER：誠實拒答不攔 ────────────────────────────────

def test_honest_no_data_with_school_name_not_blocked():
    msg = "政大的社課時間"
    res = E.resolve(msg)
    scope = E.ResearchScope(mode=E.ResearchMode.INTERNAL)
    review = V.review_answer(
        "知識庫沒有政大的資料，無法回答政大的社課時間。",
        scope=scope, resolution=res, sources=[],
    )
    assert review.verdict != "block"


# ── conflicts：entity 維度 ───────────────────────────────────

class _FakeMeta:
    def __init__(self, year, entity_id, school, is_external, source_type="archive", priority=50):
        self.academic_year = year
        self.entity_id = entity_id
        self.school = school
        self.is_external = is_external
        self.source_type = source_type
        self.priority = priority


class _FakeChunk:
    def __init__(self, text, meta, source="測試檔"):
        self.text = text
        self.meta = meta
        self.source = source


class _FakeScored:
    def __init__(self, chunk):
        self.chunk = chunk
        self.score = 1.0


def _scored(text, year="114", entity_id=E.HOME_ENTITY_ID, school=E.HOME_SCHOOL, is_external=False):
    return _FakeScored(_FakeChunk(text, _FakeMeta(year, entity_id, school, is_external)))


def test_cross_entity_values_are_not_conflicts():
    """淡江社長與北科社長同年不同名 → 不是衝突（不同實體本來就不同）。"""
    hits = [
        _scored("社長：林小華"),
        _scored("社長：湯某某", entity_id="ntut-lead", school="國立臺北科技大學", is_external=True),
    ]
    conflicts = detect_conflicts(hits)
    assert not [c for c in conflicts if c["kind"] == "same_year_conflict"]


def test_same_entity_same_year_conflict_detected():
    hits = [_scored("社長：林小華"), _scored("社長：陳大明")]
    found = [c for c in detect_conflicts(hits) if c["kind"] == "same_year_conflict"]
    assert found and found[0]["entity_id"] == E.HOME_ENTITY_ID


def test_external_conflict_resolution_never_says_use_tku():
    hits = [
        _scored("社長：湯某某", entity_id="ntut-lead", school="國立臺北科技大學", is_external=True),
        _scored("社長：黃某某", entity_id="ntut-lead", school="國立臺北科技大學", is_external=True),
    ]
    found = [c for c in detect_conflicts(hits) if c["kind"] == "same_year_conflict"]
    assert found
    assert "以淡江" not in found[0]["resolution"].replace("不要以淡江", "")
    assert "來源衝突" in found[0]["resolution"]


def test_external_values_not_stale_reference_against_current_term():
    """外校的社長跟淡江 current_term 不同不是「過期參照」。"""
    hits = [_scored("社長：湯某某", entity_id="ntut-lead", school="國立臺北科技大學", is_external=True)]
    conflicts = detect_conflicts(hits, current_facts={"president": "林小華"})
    assert not [c for c in conflicts if c["kind"] == "stale_reference"]


# ── STATUS_CONFLICTED 賦值 ───────────────────────────────────

def test_claim_using_conflicted_value_is_marked():
    scope, res = _ntut_scope()
    conflicts = [{
        "kind": "same_year_conflict", "field": "president", "year": "114",
        "entity_id": "ntut-lead",
        "values": [{"value": "湯某某"}, {"value": "黃某某"}],
    }]
    src = _ntut_source(excerpt="社長：湯某某，主打特質鍛鍊。")
    review = V.review_answer(
        "北科禪心領袖社的社長：湯某某，主打特質鍛鍊。",
        scope=scope, resolution=res, sources=[src], conflicts=conflicts,
    )
    assert any(c.status == C.STATUS_CONFLICTED for c in review.claims)
    assert any(f.rule == "conflicted_sources" for f in review.findings)
    assert review.verdict == "degrade"


def test_internal_answer_with_conflicted_value_warns():
    res = E.resolve("社長是誰")
    scope = E.ResearchScope(mode=E.ResearchMode.INTERNAL)
    conflicts = [{
        "kind": "same_year_conflict", "field": "president", "year": "114",
        "entity_id": E.HOME_ENTITY_ID,
        "values": [{"value": "林小華"}, {"value": "陳大明"}],
    }]
    review = V.review_answer(
        "本學期社長是林小華。", scope=scope, resolution=res, sources=[], conflicts=conflicts,
    )
    assert any(f.rule == "conflicted_sources" for f in review.findings)
    assert review.verdict == "degrade"


# ── classify_source：wrong_entity 與保守分級 ─────────────────

def test_source_outside_research_target_is_wrong_entity():
    rec = _ntut_source()
    assert rec.status == C.STATUS_VERIFIED
    rec2 = _ntut_source().finalize(target_entities=["shu-zen"])
    assert rec2.status == C.STATUS_WRONG_ENTITY


def test_entityless_external_source_with_targets_is_wrong_entity():
    rec = _ntut_source(entity_id="").finalize(target_entities=["ntut-lead"])
    assert rec.status == C.STATUS_WRONG_ENTITY


# ── partial claim 帶日期 ─────────────────────────────────────

def test_partial_claim_keeps_dates():
    scope, res = _ntut_scope()
    src = _ntut_source(
        excerpt="公開主題包含禪定與舒壓，貼文以特質培養為主軸，經營節奏穩定。",
        captured_at="2026-08-20", published_at="2026-08-01",
    )
    # 句子只有部分內容對得上摘錄 → partial
    review = V.review_answer(
        "北科禪心領袖社的貼文以特質培養為主軸，並固定於每週五晚間開課供新生參加。",
        scope=scope, resolution=res, sources=[src],
    )
    partial = [c for c in review.claims if c.status == C.STATUS_PARTIAL]
    if partial:
        assert partial[0].captured_at == "2026-08-20"


# ── grok 第二階段審查的五個反例 ──────────────────────────────

def test_disclaimer_clause_does_not_shield_following_claim():
    """grok 1：「沒有北科的資料提到茶會，北科的茶會安排香氛蠟燭…」
    前半免責不能掩護後半的編造。"""
    scope, res = _ntut_scope()
    internal = _home_source("茶會當天安排香氛蠟燭手作體驗，由生活組準備材料包，參加者完成後可帶回宿舍。")
    answer = (
        "目前沒有北科的資料提到茶會，北科禪心領袖社的茶會安排香氛蠟燭手作體驗，"
        "由生活組準備材料包，參加者完成後可帶回宿舍。"
    )
    review = V.review_answer(answer, scope=scope, resolution=res, sources=[_ntut_source(), internal])
    assert review.verdict == "block"


def test_internal_mode_disclaimer_then_fabrication_blocked():
    """grok 1（內部模式）：「知識庫沒有政大的資料，不過政大禪學社的茶會通常會…」"""
    res = E.resolve("政大的茶會")
    scope = E.ResearchScope(mode=E.ResearchMode.INTERNAL)
    review = V.review_answer(
        "知識庫沒有政大的資料，不過政大禪學社的茶會通常會安排體驗禪。",
        scope=scope, resolution=res, sources=[],
    )
    assert review.verdict == "block"


def test_mixed_partial_sentence_still_caught_as_contamination():
    """grok 2：外校支持度 0.3 的混合句不能靠 partial 分支矇混。"""
    scope, res = _ntut_scope()
    internal = _home_source("茶會當天安排香氛蠟燭手作體驗，由生活組準備材料包，參加者完成後可帶回宿舍。")
    answer = (
        "北科禪心領袖社主打貼近生活的禪與特質鍛鍊與領袖實踐，茶會安排香氛蠟燭手作體驗，"
        "由生活組準備材料包，參加者完成後可帶回宿舍。"
    )
    review = V.review_answer(answer, scope=scope, resolution=res, sources=[_ntut_source(), internal])
    assert any(f.rule == "data_contamination" for f in review.findings)
    assert review.verdict == "block"


def test_generic_club_words_not_flagged_as_contamination():
    """grok 3：「北科每週舉辦社課，並透過茶會讓新生認識社團」是通用敘述，不是抄淡江。"""
    scope, res = _ntut_scope()
    internal = _home_source("本社每週舉辦社課，並在學期初舉辦茶會，讓新生認識社團與社員。")
    review = V.review_answer(
        "北科禪心領袖社每週舉辦社課，並透過茶會讓新生認識社團。",
        scope=scope, resolution=res, sources=[_ntut_source(), internal],
    )
    assert not any(f.rule == "data_contamination" for f in review.findings)
    assert review.verdict != "block"


def test_internal_conflict_degrade_shows_partial_status():
    """grok 4：內部衝突 degrade → research_status 改 partially_verified（黃燈），
    不能停在 internal 讓前端誤判成紅色驗證失敗。"""
    res = E.resolve("社長是誰")
    scope = E.ResearchScope(mode=E.ResearchMode.INTERNAL)
    conflicts = [{
        "kind": "same_year_conflict", "field": "president", "year": "114",
        "entity_id": E.HOME_ENTITY_ID,
        "values": [{"value": "林小華"}, {"value": "陳大明"}],
    }]
    review = V.review_answer(
        "本學期社長是林小華。", scope=scope, resolution=res, sources=[], conflicts=conflicts,
    )
    assert review.verdict == "degrade"
    assert review.research_status == V.RESEARCH_PARTIAL


def test_home_conflict_does_not_mark_external_claim():
    """grok 5：淡江社長的衝突值不能把北科 claim 標成 conflicted。"""
    scope, res = _ntut_scope()
    conflicts = [{
        "kind": "same_year_conflict", "field": "president", "year": "114",
        "entity_id": E.HOME_ENTITY_ID,
        "values": [{"value": "林小華"}, {"value": "陳大明"}],
    }]
    src = _ntut_source(excerpt="公開定位：特質鍛鍊與領袖實踐，社長不是林小華。")
    review = V.review_answer(
        "北科禪心領袖社主打特質鍛鍊與領袖實踐，社長不是林小華。",
        scope=scope, resolution=res, sources=[src], conflicts=conflicts,
    )
    assert not any(c.status == C.STATUS_CONFLICTED for c in review.claims)


def test_swallowed_alias_not_blocked_in_internal_mode():
    """內部模式的詞界防護：「完成大合照」不得觸發外校結論封鎖。"""
    res = E.resolve("挑戰營流程")
    scope = E.ResearchScope(mode=E.ResearchMode.INTERNAL)
    review = V.review_answer(
        "挑戰營最後在山頂完成大合照，為活動畫下句點。",
        scope=scope, resolution=res, sources=[],
    )
    assert review.verdict == "allow"
