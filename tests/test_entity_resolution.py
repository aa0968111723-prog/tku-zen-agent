"""實體解析器、研究範圍與資料歸屬 metadata。

「政大呢」事故的第一線防禦：研究對象是誰、資料屬於誰，
在檢索與生成之前就必須定案。
"""

from __future__ import annotations

from app.research import entities as E


# ── 實體解析 ─────────────────────────────────────────────────

def test_ambiguous_school_needs_clarification():
    """規格情境 11：研究對象名稱不明確（政大沒有已收錄的正式社團）。"""
    res = E.resolve("政大呢")
    assert res.needs_clarification
    assert [u.school for u in res.unresolved] == ["國立政治大學"]

    c = res.clarification()
    assert c is not None
    assert "無法確認" in c.question and "Instagram" in c.question
    labels = [o["label"] for o in c.options]
    assert any("正式社團" in l for l in labels)
    assert any("學生自辦" in l for l in labels)
    assert any("不確定" in l for l in labels)
    assert c.topic_options  # 茶會內容、招生文案……


def test_zhengda_zen_club_is_still_unresolved():
    """「政大禪學社」聽起來具體，但 registry 沒有這個正式社團——仍要反問。"""
    res = E.resolve("政大禪學社的茶會怎麼做")
    assert res.needs_clarification


def test_clarified_reply_is_not_asked_again():
    res = E.resolve("我指的是政大的正式社團，請只使用可驗證的官方公開來源研究。")
    assert res.clarified
    assert not res.needs_clarification     # 不再反問，改走「查無來源」誠實回覆


def test_registered_external_club_resolves():
    res = E.resolve("北科禪心領袖社的 IG 怎麼經營")
    assert [e.entity_id for e in res.external] == ["ntut-lead"]
    assert not res.needs_clarification


def test_similar_school_names_resolve_to_one_entity():
    """規格情境 12：多個相似稱呼（師大／台師大）必須解析成同一個實體。"""
    a = E.resolve("師大領袖社的社課")
    b = E.resolve("台師大領袖社的社課")
    assert a.external and b.external
    assert a.external[0].entity_id == b.external[0].entity_id == "ntnu-lead"


def test_ntu_leadership_is_known_but_has_no_sources():
    res = E.resolve("台大領袖社的 IG 怎麼經營")
    assert [e.entity_id for e in res.no_source_entities] == ["ntu-lead"]
    assert not res.needs_clarification     # 知道是誰，但研究時只能回「查無來源」


def test_user_provided_ig_handle_is_recorded():
    """規格情境 3：使用者指定官方 IG——記下帳號，不再反問。"""
    res = E.resolve("政大的官方帳號是 @nccu_zen_official，幫我研究他們的茶會")
    assert "@nccu_zen_official" in res.user_provided_accounts
    assert not res.needs_clarification


def test_registry_ig_handle_resolves_to_entity():
    res = E.resolve("幫我看看 @ntut_leadershipclub 都發什麼")
    assert [e.entity_id for e in res.external] == ["ntut-lead"]


def test_home_mention_is_not_external():
    res = E.resolve("淡江大學領袖禪學社的期初茶會")
    assert res.home_mentioned
    assert not res.external and not res.unresolved


# ── 研究模式 ─────────────────────────────────────────────────

def test_internal_only_request_forces_internal_mode():
    """規格情境 4：使用者指定只使用淡江內部資料。"""
    msg = "只用淡江內部資料，幫我整理期初茶會的做法"
    scope = E.decide_scope(msg, E.resolve(msg))
    assert scope.mode == E.ResearchMode.INTERNAL
    assert scope.internal_only_requested


def test_external_mention_with_comparison_is_comparative():
    msg = "幫我比較北科禪心領袖社跟淡江的招生文案"
    scope = E.decide_scope(msg, E.resolve(msg))
    assert scope.mode == E.ResearchMode.COMPARATIVE
    assert scope.target_entities == ["ntut-lead"]


def test_external_mention_alone_is_external_mode():
    msg = "北科禪心領袖社的 IG 都發什麼"
    scope = E.decide_scope(msg, E.resolve(msg))
    assert scope.mode == E.ResearchMode.EXTERNAL
    assert not scope.accepts_external_chunk("tmu-zen")      # 別校不是這次的證據
    assert scope.accepts_external_chunk("ntut-lead")
    assert not scope.accepts_external_chunk("")             # 泛用段落也不算對象證據


def test_generic_external_research_accepts_all_external():
    msg = "請研究其他學校禪學社的公開網宣做法，整理給淡江參考"
    scope = E.decide_scope(msg, E.resolve(msg))
    assert scope.mode == E.ResearchMode.COMPARATIVE
    assert scope.generic_external
    assert scope.accepts_external_chunk("tmu-zen")


# ── chunk 歸屬 metadata ──────────────────────────────────────

def test_external_chunks_carry_entity_school_url_captured_at(index):
    ext = [c for c in index.chunks if c.meta and c.meta.source_scope == "external"]
    assert ext, "外校參考檔應該有被建進索引"
    by_entity = {c.meta.entity_id for c in ext if c.meta.entity_id}
    # 參考檔裡的 13 個實體都要對得到
    for eid in ("tmu-zen", "shu-zen", "ndhu-zen", "scu-zen", "fju-zen", "tnua-zen",
                "nsysu-lead", "ntut-lead", "nthu-lead", "ntnu-lead", "ncku-lead",
                "ttu-lead", "wlpef"):
        assert eid in by_entity, f"外校段落沒有對到 {eid}"

    sample = next(c for c in ext if c.meta.entity_id == "ntut-lead")
    assert sample.meta.school == "國立臺北科技大學"
    assert sample.meta.is_external
    assert sample.meta.source_url.startswith("https://www.instagram.com/")
    assert sample.meta.captured_at      # 來自檔頭「最後檢索日期」
    assert sample.meta.authority_level == "official"


def test_internal_chunks_belong_to_tku(index):
    internal = [c for c in index.chunks if c.meta and c.meta.source_scope == "internal"]
    assert internal
    for c in internal[:50]:
        assert c.meta.entity_id == E.HOME_ENTITY_ID
        assert c.meta.school == "淡江大學"
        assert not c.meta.is_external


def test_no_internal_chunk_is_attributed_to_external_school(index):
    """規格三：不得只靠內容相似就把淡江文件歸屬給外校。

    「政大」實際出現在兩份淡江社課文件裡（講師學歷）——它們必須仍屬於淡江。
    """
    mentions = [
        c for c in index.chunks
        if "政大" in c.text and c.meta and c.meta.source_scope == "internal"
    ]
    assert mentions, "語料裡應該有提到政大的淡江內部文件（事故的誘因）"
    for c in mentions:
        assert c.meta.entity_id == E.HOME_ENTITY_ID
        assert c.meta.school == "淡江大學"


# ── scope 檢索過濾 ───────────────────────────────────────────

def test_external_scope_never_returns_tku_chunks_as_evidence():
    """規格情境 8 的資料層防線：研究政大時，淡江資料不得作為政大證據。"""
    from app import retrieval

    msg = "我指的是政大的正式社團，請只使用可驗證的官方公開來源研究。政大 茶會 文宣"
    scope = E.decide_scope(msg, E.resolve(msg))
    assert scope.mode == E.ResearchMode.EXTERNAL
    bundle = retrieval.build_context(["政大 茶會 文宣", "期初茶會 文宣"], scope=scope)
    assert bundle.external_evidence_for_targets() == []
    assert bundle.hits == [], "外部研究模式下，淡江內部段落連 context 都不能進"


def test_targeted_external_scope_only_returns_target_school():
    from app import retrieval

    msg = "北科禪心領袖社的招生貼文"
    scope = E.decide_scope(msg, E.resolve(msg))
    bundle = retrieval.build_context(["北科禪心領袖社 招生 貼文"], scope=scope)
    evidence = bundle.external_evidence_for_targets()
    assert evidence
    for s in evidence:
        assert s.chunk.meta.entity_id == "ntut-lead"


def test_comparative_scope_keeps_pools_separated():
    """規格情境 6：外校來源與淡江資料必須分池、分段呈現。"""
    from app import retrieval

    msg = "幫我比較北科禪心領袖社跟淡江的招生文案"
    scope = E.decide_scope(msg, E.resolve(msg))
    bundle = retrieval.build_context(["北科禪心領袖社 招生", "招生 文宣 貼文"], scope=scope)
    assert bundle.external_evidence_for_targets()
    assert bundle.internal_hits()
    rendered = bundle.render()
    assert "【外校已驗證資料】" in rendered
    assert "【淡江內部資料】" in rendered
    # 外校區塊要帶學校與來源網址
    assert "國立臺北科技大學" in rendered
    assert "instagram.com" in rendered


# ── social 工具的實體過濾 ────────────────────────────────────

def test_social_references_filter_by_target_school(index):
    from app.tools import social

    result = social.search_social_references("北科禪心領袖社 招生")
    assert result["ok"]
    assert result["references"]
    for ref in result["references"]:
        assert ref["entity_id"] == "ntut-lead"
        assert ref["school"] == "國立臺北科技大學"
        assert ref["source_url"].startswith("https://")
        assert ref["captured_at"]


def test_social_references_report_no_source_for_unknown_school(index):
    """規格情境 5：外部研究但搜尋不到來源——不得拿別校資料湊數。"""
    from app.tools import social

    result = social.search_social_references("政大 禪學社 茶會")
    assert result["ok"]
    assert result["references"] == []
    assert result.get("no_verified_source") is True
    assert "沒有" in result["message"] and "official" not in result["message"].lower()
    assert "不提供確定結論" in result["message"]
