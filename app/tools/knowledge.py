"""讓代理查社團知識庫與任務劇本。"""

from __future__ import annotations

from .. import retrieval

MAX_SNIPPET = 1200


def search_knowledge(query: str, top_k: int = 5) -> dict:
    query = (query or "").strip()
    if not query:
        return {"ok": False, "message": "query 是空的。請說明你要查什麼，例如「招生管道」「十二項特質」「社課籌備流程」。"}

    top_k = max(1, min(int(top_k or 5), 10))
    index = retrieval.get_index()
    hits, confidence = index.search_scored(query, k=top_k)

    # 提醒每次都要附上。BM25 一定會回傳「最像」的段落，就算查詢完全不相干；
    # 少了這句，模型很容易把不相關的段落當成社團事實接著往下寫。
    reminder = (
        "\n\n───────────\n"
        "提醒：只有上面出現過的內容才是社團事實。社長姓名、社課時間地點、社費金額、"
        "報名連結、確切日期這類每年變動的資訊，如果上面沒有，就直接說要使用者提供，"
        "或在產出裡填「待填」——不要自己編一個看起來合理的。\n"
        "標示為「歷年檔案」的段落是往年的真實文件，拿來對齊格式與寫法很好用，"
        "但裡面的日期、人名、金額、人數都是當年的，不要當成今年的事實。"
    )

    if not hits or confidence < index.LOW_CONFIDENCE:
        found = ""
        if hits:
            found = (
                "\n\n（勉強找到的段落，相關度很低，多半不是你要的：「"
                + hits[0][1].source
                + "」）"
            )
        return {
            "ok": True,
            "low_confidence": True,
            "hit_count": 0,
            "message": (
                f"知識庫裡查不到「{query}」的相關內容。{found}\n"
                "如果這是社團專屬資訊，請直接告訴使用者知識庫沒有這一項、需要他補充，"
                "不要自行編造。如果只是一般常識或通用做法，可以用你自己的知識回答，"
                "但要說清楚這不是社團既有的規定。"
            ),
        }

    blocks = []
    for _score, chunk in hits:
        text = chunk.text.strip()
        if len(text) > MAX_SNIPPET:
            text = text[:MAX_SNIPPET] + "…（內容過長已截斷）"
        blocks.append(f"【來源：{chunk.source}】\n{text}")

    return {
        "ok": True,
        "low_confidence": False,
        "hit_count": len(hits),
        "message": (
            "以下是知識庫裡最相關的段落，請以這些內容為準：\n\n"
            + "\n\n───────────\n\n".join(blocks)
            + reminder
        ),
    }


SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_knowledge",
        "description": (
            "查詢淡江大學領袖禪學社的知識庫。"
            "**在回答任何社團相關問題、或動手做任何社團產出之前，一定要先用這個工具查過。**"
            "涵蓋兩類內容："
            "(1) 社團知識庫與任務劇本 —— 社團定位、核心精神、青年領袖十二項特質、"
            "社課主題庫、組織運作、年度節奏、招生方法、語氣用詞指南，以及各種任務該怎麼做；"
            "(2) 歷年檔案 —— 108 到 115 學年度的真實企劃書、活動細流、社評績效報告、"
            "社課簡報、會議紀錄、IG 宣傳文案、財務表、挑戰營資料，可以拿來對齊真實格式與寫法。"
            "查歷年範例時直接用活動名稱當關鍵字，例如「浮花禪光 企劃書」「孝子山 挑戰營」"
            "「114 年度績效報告」「教授沒教的大腦休息法」。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "要查的主題關鍵字，用中文。"
                        "查做法用「招生管道」「社課籌備SOP」「對外文宣語氣」；"
                        "查歷年範例用活動或文件名稱，例如「期初茶會企劃書」「挑戰營細流」「年度績效報告」。"
                    ),
                },
                "top_k": {"type": "integer", "description": "要回傳幾段，預設 5，最多 10"},
            },
            "required": ["query"],
        },
    },
}
