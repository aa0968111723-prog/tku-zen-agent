"""可選工具：只列出八類公開入口，不抓社群、不改 SSOT。"""

from __future__ import annotations

from app.research.public_map import entities_matching
from app.research.public_map_hooks import PUBLIC_LABEL, list_map_brief


def list_public_research_map(query: str = "") -> dict:
    query = (query or "").strip()
    rows = list_map_brief()
    if query:
        matched_ids = {e.id for e in entities_matching(query)}
        rows = [row for row in rows if row["id"] in matched_ids] or rows
    return {
        "ok": True,
        "attribution": PUBLIC_LABEL,
        "hit_count": len(rows),
        "message": (
            "以下是已核對的公開研究入口，不是社團規定，也不是今年活動時間表。"
            "需要最新網頁內容時請走 allowlist 或 search_perplexity_web，並保留原始 HTTPS 網址。"
        ),
        "categories": rows,
    }


SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_public_research_map",
        "description": (
            "列出八類公開研究來源入口：淡江大學、淡大美食地圖、淡水地區、"
            "世界領袖教育和平基金會、旗下或相關大學社團、社青團、悟覺妙天禪師開示、禪天下。"
            "結果只是公開入口導覽，不是社團規定。不要用它來回答今年社課時間或社員個資。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "可選。例如「社青團」「美食地圖」「禪天下」。空白則回全部八類。",
                }
            },
            "required": [],
        },
    },
}
