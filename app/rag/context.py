"""Context Builder。

每一段都要明確標出**來源類型與年份**。這是「歷史資料不冒充今年事實」
的第一道防線 —— 模型看到「歷年範例 · 113學年度」就知道那不是今年的數字。
（第二道是 verification 實際檢查產出。）
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .hybrid import Scored, hybrid_search
from .query import parse

MAX_SNIPPET = 1100
MAX_TOTAL_CHARS = 9000

SOURCE_LABEL = {
    "curated": "社團知識庫",
    "playbook": "任務劇本",
    "archive": "歷年範例",
    "conversation": "歷史對話",
}


@dataclass
class ContextBundle:
    hits: list[Scored] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    curated_count: int = 0
    archive_count: int = 0
    truncated: bool = False

    def source_labels(self) -> list[str]:
        return [s.chunk.source for s in self.hits]

    def display_sources(self) -> list[str]:
        """給 UI 的簡短來源清單。"""
        out: list[str] = []
        for s in self.hits:
            meta = getattr(s.chunk, "meta", None)
            kind = SOURCE_LABEL.get(meta.source_type if meta else "archive", "資料")
            extra = meta.label() if meta else ""
            label = f"{kind}：{extra}" if extra else kind
            name = s.chunk.source.split("／")[-1].split(" › ")[0][:40]
            entry = f"{label}｜{name}" if name else label
            if entry not in out:
                out.append(entry)
        return out[:8]

    def render(self) -> str:
        """組成放進 system prompt 的段落。"""
        if not self.hits:
            return (
                "## 檢索結果\n\n知識庫裡沒有查到相關內容。\n"
                "不要編造社團專屬資訊。如果是通用做法可以用你的常識，但要說清楚"
                "那不是社團既有的規定。"
            )

        lines = [
            "## 檢索結果",
            "",
            "以下是從社團知識庫查到的內容。**每一段都標了來源類型與年份，請照著判斷可信度**：",
            "",
            "- `社團知識庫` / `任務劇本` = 規範，照著做",
            "- `歷年範例` = 往年真實檔案，格式與寫法可以照抄，"
            "**但裡面的日期、人名、金額、人數是當年的，不可以當成今年的事實**",
            "",
        ]

        used = 0
        for s in self.hits:
            chunk = s.chunk
            meta = getattr(chunk, "meta", None)
            kind = SOURCE_LABEL.get(meta.source_type if meta else "archive", "資料")
            detail = meta.label() if meta else ""
            header = f"### 【{kind}】{chunk.source}"
            if detail:
                header += f"（{detail}）"

            text = chunk.text.strip()
            if len(text) > MAX_SNIPPET:
                text = text[:MAX_SNIPPET] + "…（略）"

            block = f"{header}\n\n{text}"
            if used + len(block) > MAX_TOTAL_CHARS:
                self.truncated = True
                lines.append("（其餘結果因長度限制略過）")
                break
            lines.append(block)
            lines.append("")
            used += len(block)

        return "\n".join(lines)


def build_context(
    queries: list[str],
    *,
    task_type: str = "",
    per_query: int = 5,
    total: int = 9,
) -> ContextBundle:
    """跑多個查詢並合併結果。

    多查詢的用意：一句「幫我做期初茶會企劃」同時需要
    〈社課排程與籌備〉劇本 + 歷年的期初茶會企劃書，單一查詢很難兩者都撈到。
    """
    from .. import retrieval
    from ..services import current_term as term_service

    index = retrieval.get_index()
    current_year = term_service.load().get("academic_year")

    bundle = ContextBundle(queries=list(queries))
    seen: set[int] = set()
    pooled: list[Scored] = []

    for i, raw in enumerate(queries or []):
        if not raw.strip():
            continue
        parsed = parse(raw)
        hits = hybrid_search(
            index,
            raw,
            k=per_query,
            parsed=parsed,
            current_year=current_year,
            # 第一個查詢負責保證規範與範例都在，後續查詢純粹補充
            min_curated=2 if i == 0 else 0,
            min_archive=1 if i == 0 else 0,
        )
        for h in hits:
            key = id(h.chunk)
            if key in seen:
                continue
            seen.add(key)
            pooled.append(h)

    pooled.sort(key=lambda s: s.score, reverse=True)
    bundle.hits = pooled[:total]

    from ..retrieval import TIER_CURATED

    bundle.curated_count = sum(1 for s in bundle.hits if s.chunk.tier >= TIER_CURATED)
    bundle.archive_count = len(bundle.hits) - bundle.curated_count
    return bundle
