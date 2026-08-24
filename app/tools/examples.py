"""專門找歷年真實檔案的工具。

跟 search_knowledge 分開，是為了讓模型能明確表達「我要看以前怎麼做的」。
回傳的每一段都標了年份與文件類型，而且開頭就寫死警告：這是往年資料。
"""

from __future__ import annotations

from .. import retrieval
from ..rag import parse
from ..rag.hybrid import hybrid_search
from ..services import current_term as term_service
from .knowledge import _attribution_notes, internal_data_guard

MAX_SNIPPET = 1400


def search_previous_examples(query: str, document_type: str = "", top_k: int = 4) -> dict:
    query = (query or "").strip()
    if not query:
        return {"ok": False, "message": "query 是空的。請說明你要找什麼歷年檔案。"}

    blocked = internal_data_guard("歷年檔案")
    if blocked is not None:
        return blocked

    top_k = max(1, min(int(top_k or 4), 8))
    combined = f"{query} {document_type}".strip()
    parsed = parse(combined)
    if document_type and not parsed.document_type:
        parsed.document_type = document_type.strip()

    index = retrieval.get_index()
    current_year = term_service.load().get("academic_year")

    hits = hybrid_search(
        index,
        combined,
        k=top_k * 3,
        parsed=parsed,
        current_year=current_year,
        min_curated=0,
        min_archive=0,
    )
    archive = [h for h in hits if h.chunk.tier < retrieval.TIER_CURATED][:top_k]

    if not archive:
        return {
            "ok": True,
            "hit_count": 0,
            "message": (
                f"歷年檔案裡找不到「{query}」的相關內容。\n"
                "可以改用 search_knowledge 查任務劇本的通用做法，"
                "或直接依劇本規範自己設計，不要編造一份不存在的歷年檔案。"
            ),
        }

    blocks = []
    for h in archive:
        meta = getattr(h.chunk, "meta", None)
        label = meta.label() if meta else ""
        text = h.chunk.text.strip()
        if len(text) > MAX_SNIPPET:
            text = text[:MAX_SNIPPET] + "…（略）"
        header = f"【歷年檔案｜淡江內部資料】{h.chunk.source}"
        if label:
            header += f"（{label}）"
        blocks.append(f"{header}\n{text}")

    scope_header, mismatch = _attribution_notes(query)
    return {
        "ok": True,
        "hit_count": len(archive),
        "message": (
            scope_header
            + mismatch
            + "以下是淡江大學領袖禪學社往年的真實檔案，**格式、章節、寫法可以照抄，"
            "但裡面的日期、人名、金額、人數都是當年的，不可以當成今年的事實，"
            "也不得寫成其他學校的資料**：\n\n"
            + "\n\n───────────\n\n".join(blocks)
            + "\n\n───────────\n"
            "今年的資料一律以 get_current_term 為準；那裡沒有的就填「待填」。"
        ),
    }


SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_previous_examples",
        "description": (
            "找社團 108–115 學年度的真實歷年檔案：企劃書、活動細流、社評績效報告、"
            "社課簡報、會議紀錄、IG 宣傳文案、財務表、挑戰營資料。"
            "要對齊社團一直以來的格式與寫法時用這個。"
            "查到的是**往年**資料，只能當範例，不能當成今年的事實。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "活動或文件名稱，例如「期初茶會 企劃書」「挑戰營 細流」「年度績效報告」",
                },
                "document_type": {
                    "type": "string",
                    "description": "想找的文件類型，例如 企劃書、細流、績效報告、文宣、財務、會議紀錄",
                },
                "top_k": {"type": "integer", "description": "要幾份，預設 4，最多 8"},
            },
            "required": ["query"],
        },
    },
}
