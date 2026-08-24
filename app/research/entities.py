"""實體解析器與 entity registry。

「政大呢」事故的第一層根因就在這裡缺席：系統過去沒有任何地方記錄
「研究對象是哪一所學校的哪一個正式社團」，於是 BM25 撈到什麼就答什麼，
淡江的浮游花手作被冒充成政大的茶會做法。

原則：
  · registry 只收「有已驗證公開來源」的實體（來源＝knowledge/社群 參考檔）。
  · 學校講得出來、但 registry 沒有對應正式社團 → 一律 needs_clarification，
    反問使用者，不得先生成外校事實。
  · 解析是規則式的：確定性、不花額度、測得起來。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

HOME_ENTITY_ID = "tku-lead-zen"
HOME_SCHOOL = "淡江大學"


@dataclass(frozen=True)
class Entity:
    entity_id: str
    name: str                      # 正式社團／組織名稱
    school: str                    # 正式學校名稱（基金會類為空字串）
    kind: str                      # home_club | zen_club | leadership_club | foundation
    aliases: tuple[str, ...]       # 訊息裡可能出現的稱呼（含簡稱）
    instagram: str = ""            # 官方帳號 handle
    instagram_url: str = ""
    website: str = ""
    has_sources: bool = True       # registry 是否有可引用的公開來源資料


# ── Registry ─────────────────────────────────────────────────
# 外校條目與 knowledge/社群/其他禪學社IG公開資料.md 一一對應；
# 新增外校資料檔時要同步加條目，否則會被視為「無已驗證社團」而反問。

HOME = Entity(
    entity_id=HOME_ENTITY_ID,
    name="淡江大學領袖禪學社",
    school=HOME_SCHOOL,
    kind="home_club",
    aliases=("淡江大學領袖禪學社", "領袖禪學社", "淡大領", "淡江禪學社", "本社"),
)

EXTERNAL_ENTITIES: tuple[Entity, ...] = (
    Entity("tmu-zen", "北醫禪學社", "臺北醫學大學", "zen_club",
           ("北醫禪學社", "臺北醫學大學禪學社", "台北醫學大學禪學社"),
           instagram="tmu_zenclub", instagram_url="https://www.instagram.com/tmu_zenclub/"),
    Entity("shu-zen", "世新大學禪學社", "世新大學", "zen_club",
           ("世新大學禪學社", "世新禪學社"),
           instagram="shuzen_0419", instagram_url="https://www.instagram.com/shuzen_0419/"),
    Entity("ndhu-zen", "東華禪學社", "國立東華大學", "zen_club",
           ("東華禪學社", "東華大學禪學社"),
           instagram="ndhu.heartchen", instagram_url="https://www.instagram.com/ndhu.heartchen/"),
    Entity("scu-zen", "東吳大學禪學社", "東吳大學", "zen_club",
           ("東吳大學禪學社", "東吳禪學社"),
           instagram="scu.zen.club", instagram_url="https://www.instagram.com/scu.zen.club/"),
    Entity("fju-zen", "輔仁大學禪學社", "輔仁大學", "zen_club",
           ("輔仁大學禪學社", "輔大禪學社", "輔仁禪學社"),
           instagram="fju.zen", instagram_url="https://www.instagram.com/fju.zen/"),
    Entity("tnua-zen", "北藝禪學社", "國立臺北藝術大學", "zen_club",
           ("北藝禪學社", "臺北藝術大學禪學社", "台北藝術大學禪學社"),
           instagram="tnua_zenclub", instagram_url="https://www.instagram.com/tnua_zenclub/"),
    Entity("nsysu-lead", "中山領袖社", "國立中山大學", "leadership_club",
           ("中山領袖社", "中山大學領袖社"),
           instagram="nsysu_leadershipclub", instagram_url="https://www.instagram.com/nsysu_leadershipclub/"),
    Entity("ntut-lead", "北科禪心領袖社", "國立臺北科技大學", "leadership_club",
           ("北科禪心領袖社", "北科領袖社", "禪心領袖社", "北科禪心"),
           instagram="ntut_leadershipclub", instagram_url="https://www.instagram.com/ntut_leadershipclub/"),
    Entity("nthu-lead", "國立清華大學領袖社", "國立清華大學", "leadership_club",
           ("清華領袖社", "清大領袖社", "清華大學領袖社"),
           instagram="nthu_leadership", instagram_url="https://www.instagram.com/nthu_leadership/"),
    Entity("ntnu-lead", "國立臺灣師範大學領袖社", "國立臺灣師範大學", "leadership_club",
           ("師大領袖社", "臺師大領袖社", "台師大領袖社"),
           instagram="ntnu_leader", instagram_url="https://www.instagram.com/ntnu_leader/"),
    Entity("ncku-lead", "國立成功大學領袖社", "國立成功大學", "leadership_club",
           ("成大領袖社", "成功大學領袖社"),
           instagram="ncku_leadership_club", instagram_url="https://www.instagram.com/ncku_leadership_club/"),
    Entity("ttu-lead", "大同大學領袖社", "大同大學", "leadership_club",
           ("大同領袖社", "大同大學領袖社"),
           instagram="ttuleader", instagram_url="https://www.instagram.com/ttuleader/"),
    Entity("wlpef", "世界領袖教育和平基金會", "", "foundation",
           ("世界領袖教育和平基金會", "領袖教育和平基金會", "和平基金會"),
           instagram="wlpef.official", instagram_url="https://www.instagram.com/wlpef.official/",
           website="https://www.wlef.org/"),
    # 規格點名要能區分、但目前**沒有任何已驗證公開來源**的實體。
    # has_sources=False：解析得到，但研究時只能回「找不到可靠來源」，不得生成事實。
    Entity("ntu-lead", "台大領袖社", "國立臺灣大學", "leadership_club",
           ("台大領袖社", "臺大領袖社", "台灣大學領袖社"), has_sources=False),
)

ALL_ENTITIES: tuple[Entity, ...] = (HOME,) + EXTERNAL_ENTITIES
_BY_ID = {e.entity_id: e for e in ALL_ENTITIES}


def entity_by_id(entity_id: str | None) -> Entity | None:
    return _BY_ID.get(entity_id or "")


# 學校簡稱 → 正式名稱。涵蓋 registry 學校與「常被問到但沒有已收錄社團」的學校。
SCHOOL_ALIASES: dict[str, str] = {
    "淡江大學": HOME_SCHOOL, "淡江": HOME_SCHOOL, "淡大": HOME_SCHOOL,
    "國立政治大學": "國立政治大學", "政治大學": "國立政治大學", "政大": "國立政治大學",
    "國立臺灣大學": "國立臺灣大學", "臺灣大學": "國立臺灣大學", "台灣大學": "國立臺灣大學",
    "台大": "國立臺灣大學", "臺大": "國立臺灣大學",
    "臺北醫學大學": "臺北醫學大學", "台北醫學大學": "臺北醫學大學", "北醫": "臺北醫學大學",
    "世新大學": "世新大學", "世新": "世新大學",
    "國立東華大學": "國立東華大學", "東華大學": "國立東華大學", "東華": "國立東華大學",
    "東吳大學": "東吳大學", "東吳": "東吳大學",
    "輔仁大學": "輔仁大學", "輔仁": "輔仁大學", "輔大": "輔仁大學",
    "國立臺北藝術大學": "國立臺北藝術大學", "臺北藝術大學": "國立臺北藝術大學",
    "台北藝術大學": "國立臺北藝術大學", "北藝": "國立臺北藝術大學",
    "國立中山大學": "國立中山大學", "中山大學": "國立中山大學",
    "國立臺北科技大學": "國立臺北科技大學", "臺北科技大學": "國立臺北科技大學",
    "台北科技大學": "國立臺北科技大學", "北科": "國立臺北科技大學",
    "國立清華大學": "國立清華大學", "清華大學": "國立清華大學", "清大": "國立清華大學",
    "國立臺灣師範大學": "國立臺灣師範大學", "臺灣師範大學": "國立臺灣師範大學",
    "台師大": "國立臺灣師範大學", "臺師大": "國立臺灣師範大學", "師大": "國立臺灣師範大學",
    "國立成功大學": "國立成功大學", "成功大學": "國立成功大學", "成大": "國立成功大學",
    "大同大學": "大同大學",
}

_SCHOOL_BY_ENTITY = {e.school: e for e in EXTERNAL_ENTITIES if e.school}

# 訊息裡的 IG 帳號或網址
_IG_HANDLE = re.compile(r"@([A-Za-z0-9._]{2,40})")
_URL = re.compile(r"https?://[^\s，。、）)]+")

# ── 別名詞界防護 ─────────────────────────────────────────────
# 中文沒有空白詞界，兩字簡稱很容易被前一個字「吞掉」變成別的詞：
# 「完成大合照」≠ 成大、「老師大概」≠ 師大、「行政大樓」≠ 政大。
# 對每個容易誤判的簡稱列出「前一個字是這些就不算」的黑名單。
# 寧可列少不列多——漏擋只是多一次反問，錯擋會讓真正的外校查詢靜默變內部模式。
_FALSE_PREV: dict[str, str] = {
    "成大": "完達變組造促養構贊集落改組",
    "師大": "老講律醫禪導工程牧藥廚設計技分",
    "政大": "行財家郵憲民內施",
    "台大": "平舞陽燈檯",
    "臺大": "平舞陽燈檯",
    "清大": "澄釐",
    "東吳": "",
    "北科": "",
}


def alias_mentioned(text: str, alias: str) -> bool:
    """別名是否真的以「獨立稱呼」出現在文字裡（至少一次未被前字吞掉）。"""
    if not alias or alias not in text:
        return False
    if len(alias) > 2:
        return True
    bad_prev = _FALSE_PREV.get(alias, "")
    if not bad_prev:
        return True
    start = 0
    while True:
        i = text.find(alias, start)
        if i == -1:
            return False
        prev = text[i - 1] if i > 0 else ""
        # 句首（prev 為空）一定算獨立稱呼；注意 "" in "…" 恆為 True，不能直接用 in
        if not prev or prev not in bad_prev:
            return True
        start = i + 1


# 反問後使用者的選項回覆（前端反問卡送出的句型，也接受手打）。
# 注意：這些字樣**只有在上一輪真的發過反問**時才算「澄清回覆」——
# 否則「我不確定政大的社課時間」這種首句就會誤觸而跳過反問（稽核漏洞 D）。
_CLARIFIED_MARKERS = ("正式社團", "學生自辦", "自辦活動", "不確定", "請協助辨識", "幫我辨識")


class ResearchMode(str, Enum):
    INTERNAL = "internal"          # 只用淡江內部資料
    EXTERNAL = "external"          # 研究外校公開資料
    COMPARATIVE = "comparative"    # 外校 vs 淡江 比較分析


@dataclass
class UnresolvedMention:
    """講得出學校，但無法對到一個「有已驗證來源」的正式社團。"""

    mention: str                   # 使用者原話裡的稱呼
    school: str                    # 正式學校名稱（解析得到時）
    reason: str                    # no_registered_club | no_sources | unknown_account


@dataclass
class ClarificationRequest:
    """反問卡的內容。沒完成實體確認，不得生成外校事實。"""

    school: str
    question: str
    options: list[dict[str, str]] = field(default_factory=list)   # {label, send_text}
    topic_question: str = ""
    topic_options: list[str] = field(default_factory=list)

    def to_event(self) -> dict:
        return {
            "type": "clarification_needed",
            "school": self.school,
            "question": self.question,
            "options": self.options,
            "topic_question": self.topic_question,
            "topic_options": self.topic_options,
        }


@dataclass
class EntityResolution:
    home_mentioned: bool = False
    external: list[Entity] = field(default_factory=list)          # 已解析、有來源
    no_source_entities: list[Entity] = field(default_factory=list)  # 已解析、無來源
    unresolved: list[UnresolvedMention] = field(default_factory=list)
    user_provided_accounts: list[str] = field(default_factory=list)  # 使用者貼的 IG/網址
    clarified: bool = False        # 這句話本身就是反問的回覆

    @property
    def needs_clarification(self) -> bool:
        return bool(self.unresolved) and not self.clarified and not self.user_provided_accounts

    def external_targets(self) -> list[Entity]:
        return self.external + self.no_source_entities

    def target_schools(self) -> list[str]:
        schools = [e.school for e in self.external_targets() if e.school]
        schools += [u.school for u in self.unresolved if u.school]
        out: list[str] = []
        for s in schools:
            if s not in out:
                out.append(s)
        return out

    def clarification(self) -> ClarificationRequest | None:
        if not self.unresolved:
            return None
        u = self.unresolved[0]
        short = _short_school(u.school)
        return ClarificationRequest(
            school=u.school,
            question=f"我目前無法確認你指的是{short}哪個社團或帳號，"
                     "請提供正式名稱、Instagram、Facebook 或網址。",
            options=[
                {"label": f"{short}正式社團",
                 "send_text": f"我指的是{short}的正式社團，請只使用可驗證的官方公開來源研究。"},
                {"label": f"{short}某個 Instagram 帳號",
                 "send_text": f"我指的是{short}的某個 Instagram 帳號，帳號是：@"},
                {"label": f"{short}學生自辦活動",
                 "send_text": f"我指的是{short}的學生自辦活動，請只使用可驗證的公開來源研究。"},
                {"label": "我不確定，請協助辨識",
                 "send_text": f"我不確定是{short}的哪個社團，請協助辨識，先告訴我有哪些查得到的公開帳號。"},
            ],
            topic_question="你想查哪一類？",
            topic_options=["茶會內容", "招生文案", "活動流程", "Instagram 經營", "社團定位", "其他"],
        )


def _short_school(school: str) -> str:
    for alias, full in SCHOOL_ALIASES.items():
        if full == school and len(alias) <= 3:
            return alias
    return school or "該校"


def clarification_for_school(school: str) -> ClarificationRequest:
    """依學校名直接組反問卡。

    給「跨輪繼承」用：上一輪反問了政大、這一輪使用者只說「那他們的茶會呢」
    ——這句話本身解析不出任何 unresolved，反問卡要從繼承的 scope 生出來。
    """
    res = EntityResolution()
    res.unresolved.append(
        UnresolvedMention(mention=_short_school(school), school=school, reason="no_registered_club")
    )
    clarification = res.clarification()
    assert clarification is not None
    return clarification


def apply_requested(
    resolution: EntityResolution,
    *,
    school: str = "",
    entity_id: str = "",
) -> EntityResolution:
    """把前端結構化欄位（研究對象下拉、entity_id）併入解析結果。

    結構化欄位比從句子裡猜可靠，所以直接補進 resolution；
    但仍走同一套 registry 規則——沒有已驗證來源的學校一樣要反問。
    """
    if entity_id:
        entity = entity_by_id(entity_id)
        if entity is not None and entity.entity_id != HOME_ENTITY_ID:
            if entity.has_sources and entity not in resolution.external:
                resolution.external.append(entity)
            elif not entity.has_sources and entity not in resolution.no_source_entities:
                resolution.no_source_entities.append(entity)
    if school:
        full = SCHOOL_ALIASES.get(school.strip(), school.strip())
        if full and full != HOME_SCHOOL:
            already = {e.school for e in resolution.external_targets()}
            already |= {u.school for u in resolution.unresolved}
            if full not in already:
                entity = _SCHOOL_BY_ENTITY.get(full)
                if entity is not None:
                    resolution.external.append(entity)
                else:
                    no_source = next(
                        (e for e in EXTERNAL_ENTITIES if e.school == full and not e.has_sources), None,
                    )
                    if no_source is not None:
                        resolution.no_source_entities.append(no_source)
                    else:
                        resolution.unresolved.append(
                            UnresolvedMention(mention=school.strip(), school=full, reason="no_registered_club")
                        )
    return resolution


def _find_aliases(message: str) -> list[tuple[str, Entity]]:
    hits: list[tuple[str, Entity]] = []
    for entity in ALL_ENTITIES:
        for alias in sorted(entity.aliases, key=len, reverse=True):
            if alias_mentioned(message, alias):
                hits.append((alias, entity))
                break
    return hits


def match_entity(text: str) -> Entity | None:
    """在一段文字裡找最可能的單一實體（給 chunk metadata 歸屬用）。

    只認 registry 別名，不做內容相似度猜測——「內容像政大」不代表屬於政大。
    多個實體同時出現時取最長別名命中（最明確的那個）。
    """
    best: tuple[int, Entity] | None = None
    for entity in ALL_ENTITIES:
        for alias in (entity.name, *entity.aliases):
            if alias_mentioned(text, alias) and (best is None or len(alias) > best[0]):
                best = (len(alias), entity)
    return best[1] if best else None


def match_entities(text: str) -> list[Entity]:
    """一段文字裡出現的**所有**實體（去重）。

    給 chunk 歸屬用：同一段落點名多個外校時，不能整段歸給其中一校
    ——那是「多校彙整」，不屬於任何單一實體的證據。
    """
    found: list[Entity] = []
    for entity in ALL_ENTITIES:
        for alias in (entity.name, *entity.aliases):
            if alias_mentioned(text, alias):
                if entity not in found:
                    found.append(entity)
                break
    return found


def mentions_external_school(text: str) -> bool:
    """文字是否明確提到非淡江的學校、社團或組織（詞界防護後）。

    skill 路由的外校意圖閘門與 working memory 的事實隔離都用這個，
    確保「學校清單」只有一份（本 registry），不會兩邊漂移。
    """
    if not text:
        return False
    for alias, full in SCHOOL_ALIASES.items():
        if full != HOME_SCHOOL and alias_mentioned(text, alias):
            return True
    for entity in EXTERNAL_ENTITIES:
        for alias in (entity.name, *entity.aliases):
            if alias_mentioned(text, alias):
                return True
    return False


def resolve(message: str, *, awaiting_clarification: bool = False) -> EntityResolution:
    """解析一句話裡的研究對象。規則式、確定性。

    ``awaiting_clarification``：上一輪是否真的發出過反問。
    只有在等待反問回覆時，「正式社團」「不確定」這些字樣才算澄清回覆；
    平常出現（「我不確定政大的社課時間」）不能拿來跳過反問。
    """
    res = EntityResolution()
    text = message.strip()
    if not text:
        return res

    res.clarified = awaiting_clarification and any(m in text for m in _CLARIFIED_MARKERS)

    matched_schools: set[str] = set()

    # 1. 社團／組織全名與別名（最精確）
    for _alias, entity in _find_aliases(text):
        if entity.entity_id == HOME_ENTITY_ID:
            res.home_mentioned = True
        elif entity.has_sources:
            if entity not in res.external:
                res.external.append(entity)
        else:
            if entity not in res.no_source_entities:
                res.no_source_entities.append(entity)
        if entity.school:
            matched_schools.add(entity.school)

    # 2. IG 帳號（registry 有就解析成該實體；沒有就記為使用者提供的帳號）
    for handle in _IG_HANDLE.findall(text):
        entity = next((e for e in EXTERNAL_ENTITIES if e.instagram.lower() == handle.lower()), None)
        if entity is not None:
            if entity not in res.external and entity.has_sources:
                res.external.append(entity)
            matched_schools.add(entity.school)
        else:
            res.user_provided_accounts.append("@" + handle)
    for url in _URL.findall(text):
        entity = next(
            (e for e in EXTERNAL_ENTITIES if e.instagram_url and e.instagram_url.rstrip("/") in url.rstrip("/")),
            None,
        )
        if entity is not None:
            if entity not in res.external and entity.has_sources:
                res.external.append(entity)
            matched_schools.add(entity.school)
        else:
            res.user_provided_accounts.append(url)

    # 3. 學校名稱。對到學校但沒對到社團 → 看 registry 有沒有該校唯一條目；
    #    沒有（政大就是這種）→ unresolved，必須反問。
    for alias in sorted(SCHOOL_ALIASES, key=len, reverse=True):
        if not alias_mentioned(text, alias):
            continue
        school = SCHOOL_ALIASES[alias]
        if school in matched_schools:
            continue
        matched_schools.add(school)
        if school == HOME_SCHOOL:
            res.home_mentioned = True
            continue
        entity = _SCHOOL_BY_ENTITY.get(school)
        if entity is not None:
            if entity.has_sources and entity not in res.external:
                res.external.append(entity)
            elif not entity.has_sources and entity not in res.no_source_entities:
                res.no_source_entities.append(entity)
        else:
            no_source = next((e for e in EXTERNAL_ENTITIES if e.school == school and not e.has_sources), None)
            if no_source is not None:
                if no_source not in res.no_source_entities:
                    res.no_source_entities.append(no_source)
            else:
                res.unresolved.append(UnresolvedMention(mention=alias, school=school, reason="no_registered_club"))

    return res


# ── 研究模式與範圍 ────────────────────────────────────────────

_INTERNAL_ONLY = re.compile(r"(只用|僅用|只使用|僅使用|只看|只查).{0,6}(淡江|淡大|本社|內部)|內部資料(就好|即可)")
_COMPARE_HINTS = re.compile(r"(比較|對比|差異|參考|借鏡|學習|淡江可以|我們可以|怎麼改良)")
_EXTERNAL_HINTS = re.compile(r"(其他學校|外校|別的學校|他校|各校|公開資料|官方IG|官方 IG|官方帳號)")


@dataclass
class ResearchScope:
    """檢索與驗證共用的資料範圍。每一次檢索都必須帶著它。"""

    mode: ResearchMode = ResearchMode.INTERNAL
    target_entities: list[str] = field(default_factory=list)   # 外校研究對象 entity_id
    target_schools: list[str] = field(default_factory=list)
    internal_only_requested: bool = False
    generic_external: bool = False    # 沒點名學校的「研究其他學校」

    @property
    def allow_internal_evidence(self) -> bool:
        return self.mode != ResearchMode.EXTERNAL

    @property
    def allow_external_evidence(self) -> bool:
        return self.mode != ResearchMode.INTERNAL

    def accepts_external_chunk(self, entity_id: str | None) -> bool:
        """這段外校資料能不能當這次研究的證據。"""
        if not self.allow_external_evidence:
            return False
        if self.generic_external and not self.target_entities:
            return True
        return bool(entity_id) and entity_id in self.target_entities

    def to_dict(self) -> dict:
        return {
            "mode": self.mode.value,
            "target_entities": list(self.target_entities),
            "target_schools": list(self.target_schools),
            "internal_only_requested": self.internal_only_requested,
            "generic_external": self.generic_external,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ResearchScope":
        try:
            mode = ResearchMode(data.get("mode", "internal"))
        except ValueError:
            mode = ResearchMode.INTERNAL
        return cls(
            mode=mode,
            target_entities=list(data.get("target_entities") or []),
            target_schools=list(data.get("target_schools") or []),
            internal_only_requested=bool(data.get("internal_only_requested")),
            generic_external=bool(data.get("generic_external")),
        )


def decide_scope(message: str, resolution: EntityResolution, task_type: str = "") -> ResearchScope:
    """決定這一輪的研究模式。

    優先序：使用者明講「只用內部」 > 點名外校 > 泛稱其他學校 > 內部。
    點名外校時，只要訊息帶有比較語氣或同時提到淡江，就是比較分析模式。
    """
    scope = ResearchScope()

    if _INTERNAL_ONLY.search(message):
        scope.mode = ResearchMode.INTERNAL
        scope.internal_only_requested = True
        return scope

    targets = resolution.external_targets()
    mentions_external = bool(targets or resolution.unresolved or resolution.user_provided_accounts)
    # 泛用外部研究必須有明確的外部訊號（「其他學校」「外校」…）。
    # 只靠 task_type 判斷會把「分析我們社團的定位」這類內部自我分析
    # 誤判成外部研究，反而把淡江自己的資料整批排除（Codex review 抓到的 bug）。
    generic = bool(_EXTERNAL_HINTS.search(message))

    if not mentions_external and not generic:
        return scope   # internal

    scope.target_entities = [e.entity_id for e in targets]
    scope.target_schools = resolution.target_schools()
    scope.generic_external = generic and not scope.target_entities and not resolution.unresolved

    if resolution.home_mentioned or _COMPARE_HINTS.search(message):
        scope.mode = ResearchMode.COMPARATIVE
    else:
        scope.mode = ResearchMode.EXTERNAL
    return scope


def external_name_lexicon() -> set[str]:
    """所有外校可識別名稱（學校、社團、帳號）。給對外文宣防抄襲／防冒名檢查用。"""
    names: set[str] = set()
    for e in EXTERNAL_ENTITIES:
        names.add(e.name)
        names.update(a for a in e.aliases if len(a) >= 3)
        if e.school:
            names.add(e.school)
        if e.instagram:
            names.add("@" + e.instagram)
            names.add(e.instagram)
        if e.instagram_url:
            names.add(e.instagram_url)
        if e.website:
            names.add(e.website)
    for alias, full in SCHOOL_ALIASES.items():
        if full != HOME_SCHOOL:
            names.add(full)
            names.add(alias)
    return names
