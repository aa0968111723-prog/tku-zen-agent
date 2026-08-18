"""從檔案路徑與內容推出 chunk metadata。

為什麼從路徑推而不是要求 ingest 時標註：雲端那 507 份檔案的資料夾結構
本身就帶了學年度、學期、活動、組別的資訊（`114/1141學期_總召/...`），
比要求幹部逐份標註可靠得多，也不用重跑 ingest。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# 114 學年度 / 1141（學年度+學期）
_YEAR = re.compile(r"(?<!\d)(1[0-2]\d)(?!\d)")
_YEAR_SEM = re.compile(r"(?<!\d)(1[0-2]\d)[-_ ]?([12])(?!\d)")
_GREGORIAN = re.compile(r"(?<!\d)(20[0-3]\d)(?!\d)")

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
    source_type: str = "archive"        # curated | playbook | archive | conversation
    academic_year: str | None = None
    semester: str | None = None
    activity: str | None = None
    document_type: str | None = None
    audience: str = "internal"          # internal | external
    team: str | None = None
    contains_sensitive_structure: bool = False
    updated_at: str | None = None
    tags: list[str] = field(default_factory=list)

    def label(self) -> str:
        """給 context builder 顯示的來源標籤。"""
        bits: list[str] = []
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


def infer(path: Path, label: str, text: str, source_type: str) -> ChunkMeta:
    """從路徑、來源標籤與內容推 metadata。"""
    hay = f"{path.as_posix()} {label}"
    meta = ChunkMeta(source_type=source_type)

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

    return meta
