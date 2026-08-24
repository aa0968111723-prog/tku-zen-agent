"""從檔案路徑與內容推出 chunk metadata。

為什麼從路徑推而不是要求 ingest 時標註：雲端那 507 份檔案的資料夾結構
本身就帶了學年度、學期、活動、組別的資訊（`114/1141學期_總召/...`），
比要求幹部逐份標註可靠得多，也不用重跑 ingest。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..research import entities as research_entities

# 114 學年度 / 1141（學年度+學期）
_YEAR = re.compile(r"(?<!\d)(1[0-2]\d)(?!\d)")
_YEAR_SEM = re.compile(r"(?<!\d)(1[0-2]\d)[-_ ]?([12])(?!\d)")
_GREGORIAN = re.compile(r"(?<!\d)(20[0-3]\d)(?!\d)")
_URL = re.compile(r"https?://[^\s)\]}>]+")
_SOURCE_DATE = re.compile(
    r"(?:最後檢索日期|擷取日期|資料日期|更新日期|發布日期|日期)\s*[:：]\s*"
    r"(20\d{2}[-/]\d{1,2}[-/]\d{1,2}|1[0-2]\d\s*學年度)"
)

DOC_TYPE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("細流", ("細流", "流程表", "跑流程", "時間軸")),
    ("企劃書", ("企劃書", "企畫書", "計畫書", "企劃", "企畫")),
    ("績效報告", ("績效", "成果報告", "成果", "評鑑", "社評")),
    ("會議紀錄", ("會議紀錄", "會議記錄", "檢討會", "會議")),
    # 「社課」本身是活動不是文件類型 —— 放進來會讓「社課排程」被判成教材而不是行事曆
    ("社課教材", ("社長時間", "第一堂", "第二堂", "第三堂", "第四堂", "第五堂", "第六堂", "第七堂", "第八堂", "教材", "學習單")),
    ("文宣", ("文宣", "海報", "貼文", "ig", "限動", "宣傳", "傳單")),
    ("名單表", ("名單", "通訊錄", "簽到", "點名", "報名表", "回覆", "responses")),
    ("財務", ("財務", "預算", "核銷", "記帳", "收支", "錢錢")),
    ("行事曆", ("行事曆", "排程", "進度表", "時間表")),
    ("表單", ("表單", "問卷", "回饋單", "調查")),
    ("交接", ("交接", "傳承", "職掌", "手冊")),
    ("章程", ("章程", "辦法", "規章")),
    ("開示", ("開示",)),
)

ACTIVITY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("期初茶會", ("期初茶會", "茶會", "浮花禪光", "浮游", "紓壓禪", "快樂禪")),
    ("期初演講", ("期初演講", "生命靈數", "與自己有約", "數字密碼")),
    ("期中演講", ("期中演講", "聽見彼此的心")),
    ("期末社大", ("期末社大", "社員大會", "感恩星光夜", "送舊")),
    ("挑戰營", ("挑戰營", "登峰傳心", "破曉", "金剛勇士", "禪行破浪", "孝子山", "皇帝殿", "攀越心峰")),
    ("禪訓營", ("禪訓營", "領袖營", "精進營")),
    ("每週社課", ("社課",)),
    ("招生", ("招生", "路宣", "接引", "入社", "班宣")),
    ("社遊", ("社遊", "出遊", "縱走", "一日遊", "冬至")),
)

TEAM_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("招生組", ("招生組",)),
    ("活動組", ("活動組",)),
    ("生活組", ("生活組",)),
    ("場務組", ("場務", "場務組")),
    ("美宣組", ("美宣", "美編")),
    ("網宣組", ("網宣",)),
    ("課程組", ("課程組",)),
    ("財務組", ("財務組", "總務")),
    ("總召", ("總召", "社長專用", "社長")),
)


@dataclass
class ChunkMeta:
    source_type: str = "archive"        # curated | playbook | archive | conversation | external_reference
    academic_year: str | None = None
    semester: str | None = None
    activity: str | None = None
    document_type: str | None = None
    audience: str = "internal"          # internal | external
    team: str | None = None
    contains_sensitive_structure: bool = False
    updated_at: str | None = None
    tags: list[str] = field(default_factory=list)
    source_title: str = ""
    source_url: str = ""
    source_date: str = ""
    summary: str = ""
    credibility: float = 0.0
    verification: str = "needs_verification"  # verified | needs_verification | internal

    @property
    def priority(self) -> int:
        return {
            "current_term": 100,
            "curated": 80,
            "playbook": 70,
            "archive": 50,
            "conversation": 20,
            "external_reference": 30,
        }.get(self.source_type, 10)

    # ── 資料歸屬（政大事故後新增：每一段都要知道自己屬於誰）──
    # source_url 與上方欄位共用：外校段落以 registry 的官方網址為準。
    entity_id: str = ""                 # research.entities 的 entity_id；外校段落對不到實體時為空
    school: str = ""                    # 正式學校名稱
    organization: str = ""              # 正式社團／組織名稱
    source_scope: str = "internal"      # internal | external —— 檢索過濾的硬邊界
    is_external: bool = False
    authority_level: str = "archive"    # official | curated | archive | user | unknown
    published_at: str = ""
    captured_at: str = ""               # 外校資料的檢索日期（判斷過舊用）
    external_source_type: str = ""      # official_instagram | official_website | ...

    def label(self) -> str:
        """給 context builder 顯示的來源標籤。"""
        bits: list[str] = []
        if self.is_external:
            # 外校段落一定要亮出歸屬——沒有這個，段落混進 context 之後
            # 就沒有任何視覺線索提醒「這不是淡江的資料」。
            owner = self.organization or self.school
            bits.append(f"外校參考{'：' + owner if owner else '（多校彙整，不屬於單一學校）'}")
        if self.academic_year:
            bits.append(f"{self.academic_year}學年度" + (f"第{self.semester}學期" if self.semester else ""))
        if self.activity:
            bits.append(self.activity)
        if self.document_type:
            bits.append(self.document_type)
        return " · ".join(bits)

    def to_dict(self) -> dict:
        return {
            "source_type": self.source_type,
            "academic_year": self.academic_year,
            "semester": self.semester,
            "activity": self.activity,
            "document_type": self.document_type,
            "audience": self.audience,
            "team": self.team,
            "contains_sensitive_structure": self.contains_sensitive_structure,
            "updated_at": self.updated_at,
            "entity_id": self.entity_id,
            "school": self.school,
            "organization": self.organization,
            "source_scope": self.source_scope,
            "is_external": self.is_external,
            "authority_level": self.authority_level,
            "published_at": self.published_at,
            "captured_at": self.captured_at,
            "source_title": self.source_title,
            "source_url": self.source_url,
            "source_date": self.source_date,
            "summary": self.summary,
            "credibility": self.credibility,
            "verification": self.verification,
            "priority": self.priority,
        }


def _match_first(text: str, rules: tuple[tuple[str, tuple[str, ...]], ...]) -> str | None:
    low = text.lower()
    best: tuple[int, str] | None = None
    for name, keywords in rules:
        for kw in keywords:
            if kw.lower() in low:
                score = len(kw)
                if best is None or score > best[0]:
                    best = (score, name)
                break
    return best[1] if best else None


# 外校參考檔開頭的「最後檢索日期：2026-08-24」。以檔案為單位快取。
_CAPTURED_AT = re.compile(r"最後檢索日期[:：]\s*(20\d{2}[-/]\d{1,2}[-/]\d{1,2})")
_captured_cache: dict[str, str] = {}


def _file_captured_at(path: Path) -> str:
    key = str(path)
    if key not in _captured_cache:
        try:
            head = path.read_text(encoding="utf-8", errors="replace")[:2000]
        except OSError:
            head = ""
        m = _CAPTURED_AT.search(head)
        _captured_cache[key] = m.group(1).replace("/", "-") if m else ""
    return _captured_cache[key]


_IG_URL = re.compile(r"https?://www\.instagram\.com/[^\s)\]，。]+")
_ANY_URL = re.compile(r"https?://[^\s)\]，。]+")


def _attribute(meta: ChunkMeta, path: Path, label: str, text: str) -> None:
    """資料歸屬。外校段落只認 registry 別名，絕不用「內容相似」歸屬學校。"""
    if meta.source_type == "external_reference":
        meta.source_scope = "external"
        meta.is_external = True
        meta.authority_level = "official"
        meta.captured_at = _file_captured_at(path)
        # 先看段落標題（label 內含 heading 路徑），再看內文；標題最可靠。
        # 內文改用全段掃描（原本只看前 400 字，實體出現在後半的段落會
        # 靜默流失歸屬）；且不論標題或內文，同段點名多個外校、或外校與
        # 淡江同段（比較段落）時，一律視為「多校彙整」——不得整段歸給
        # 其中一校（稽核漏洞 21、27；對抗審查抓到標題點名兩校仍取最長
        # 別名歸給單一校的漏洞）。
        def _sole_external(section_text: str) -> tuple["research_entities.Entity | None", bool]:
            """回傳 (唯一外校實體或 None, 這一層有沒有命中任何實體)。"""
            matched = research_entities.match_entities(section_text)
            externals = [e for e in matched if e.entity_id != research_entities.HOME_ENTITY_ID]
            if not matched:
                return None, False
            if len(externals) == 1 and len(matched) == 1:
                return externals[0], True
            return None, True    # 多校或含淡江的比較段 → 彙整

        entity, decided = _sole_external(label)
        if not decided:
            entity, _decided = _sole_external(text[:6000])
        if entity is not None and entity.entity_id != research_entities.HOME_ENTITY_ID:
            meta.entity_id = entity.entity_id
            meta.school = entity.school
            meta.organization = entity.name
            meta.source_url = entity.instagram_url or entity.website
            meta.external_source_type = (
                "official_website" if (entity.website and not entity.instagram_url) else "official_instagram"
            )
        else:
            # 跨帳號歸納、使用規則這類段落：屬於外部參考，但不屬於任何單一實體，
            # 不能當成任何一所學校的證據。
            meta.entity_id = ""
            meta.school = ""
            meta.organization = ""
            ig = _IG_URL.search(text)
            if ig:
                meta.source_url = ig.group(0)
                meta.external_source_type = "official_instagram"
    else:
        meta.source_scope = "internal"
        meta.is_external = False
        meta.entity_id = research_entities.HOME_ENTITY_ID
        meta.school = research_entities.HOME_SCHOOL
        meta.organization = "淡江大學領袖禪學社"
        meta.authority_level = "curated" if meta.source_type in {"curated", "playbook"} else "archive"


def infer(path: Path, label: str, text: str, source_type: str) -> ChunkMeta:
    """從路徑、來源標籤與內容推 metadata。"""
    hay = f"{path.as_posix()} {label}"
    meta = ChunkMeta(source_type=source_type, source_title=path.stem or label)

    m = _YEAR_SEM.search(hay)
    if m:
        meta.academic_year, meta.semester = m.group(1), m.group(2)
    else:
        y = _YEAR.search(hay)
        if y:
            meta.academic_year = y.group(1)
        else:
            g = _GREGORIAN.search(hay)
            if g:
                # 西元轉民國學年度：2025 暑假的活動屬於 114 學年度
                meta.academic_year = str(int(g.group(1)) - 1911)

    if meta.semester is None:
        if "上學期" in hay or "-1 " in hay:
            meta.semester = "1"
        elif "下學期" in hay or "-2 " in hay:
            meta.semester = "2"

    meta.activity = _match_first(hay, ACTIVITY_RULES)
    meta.document_type = _match_first(hay, DOC_TYPE_RULES)
    meta.team = _match_first(hay, TEAM_RULES)

    head = text[:1500]
    hay_low = hay.lower()   # 檔名裡的 IG 常常是大寫，不轉小寫會漏掉
    if meta.document_type == "文宣" or any(k in hay_low for k in ("ig", "貼文", "海報", "文宣", "限動", "宣傳")):
        meta.audience = "external"
    meta.contains_sensitive_structure = "只保留欄位結構" in head or "名冊類" in head

    url = _URL.search(text)
    if url:
        meta.source_url = url.group(0).rstrip("。，、；;")
    date = _SOURCE_DATE.search(text)
    if date:
        meta.source_date = date.group(1).replace("/", "-").replace(" ", "")
        meta.updated_at = meta.source_date

    # 來源摘要只取原文中的公開定位／主題等短欄位；沒有時退回第一個有內容的行。
    candidates = []
    for line in text.splitlines():
        stripped = line.strip().lstrip(">- *#").strip()
        if not stripped or stripped.startswith("http"):
            continue
        if any(key in stripped for key in ("公開定位", "公開主題", "摘要", "可供研究")):
            candidates.append(stripped)
    if not candidates:
        candidates = [
            line.strip().lstrip(">- *#").strip()
            for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
    meta.summary = (candidates[0] if candidates else "")[:320]

    if source_type in {"curated", "playbook"}:
        meta.credibility, meta.verification = 0.95, "internal"
    elif source_type == "archive":
        meta.credibility, meta.verification = 0.75, "internal"
    elif source_type == "conversation":
        meta.credibility, meta.verification = 0.5, "needs_verification"

    # 實體歸屬（政大事故後新增）。外校段落的 source_url 以 registry 的
    # 官方網址覆寫內文嗅探結果；captured_at 從檔頭「最後檢索日期」帶入。
    _attribute(meta, path, label, text)

    if source_type == "external_reference":
        if not meta.source_date and meta.captured_at:
            meta.source_date = meta.captured_at
        meta.credibility = 0.8 if meta.source_url and meta.source_date else (0.55 if meta.source_url else 0.3)
        meta.verification = "verified" if meta.source_url and meta.source_date else "needs_verification"

    return meta
