"""讓代理查社團知識庫與任務劇本。

政大事故後：這個工具**只有淡江大學領袖禪學社的資料**。
外部研究模式下呼叫它，會拿到明確拒絕而不是淡江資料——
淡江的浮游花手作被冒充成政大茶會做法，入口之一就是這裡沒把過關。
"""

from __future__ import annotations

from ..research import entities as research_entities
from ..services import context as ctx_mod
from .. import retrieval

MAX_SNIPPET = 1200


def _current_scope() -> "research_entities.ResearchScope | None":
    raw = ctx_mod.research_scope()
    if not raw:
        return None
    return research_entities.ResearchScope.from_dict(raw)


def _scope_target_label(scope: "research_entities.ResearchScope") -> str:
    names: list[str] = []
    for eid in scope.target_entities:
        entity = research_entities.entity_by_id(eid)
        if entity is not None:
            names.append(entity.name)
    names += [s for s in scope.target_schools if s not in names]
    return "、".join(names) or "研究對象"


def internal_data_guard(tool_label: str) -> dict | None:
    """外部研究模式下，內部檢索工具一律拒絕（不能拿淡江資料當外校證據）。"""
    scope = _current_scope()
    if scope is not None and scope.mode == research_entities.ResearchMode.EXTERNAL:
        target = _scope_target_label(scope)
        return {
            "ok": False,
            "code": "scope_blocked",
            "message": (
                f"目前是外校研究模式（研究對象：{target}）。"
                f"{tool_label}只有淡江大學領袖禪學社的內部資料，"
                f"不能作為{target}的證據，也不得把其中內容寫成{target}的做法。"
                "請改用外校研究工具查公開參考資料；查不到就誠實說找不到可靠來源。"
            ),
        }
    return None


def _attribution_notes(query: str) -> tuple[str, str]:
    """回傳（開頭歸屬聲明, 查詢學校錯配警告）。"""
    scope = _current_scope()
    header = ""
    if scope is not None and scope.mode == research_entities.ResearchMode.COMPARATIVE:
        target = _scope_target_label(scope)
        header = (
            "【資料歸屬】以下段落全部來自淡江大學領袖禪學社的內部資料，"
            f"只能放進【淡江內部資料】區塊，不得寫成{target}的做法或外校事實。\n\n"
        )
    mismatch = ""
    if research_entities.mentions_external_school(query):
        mismatch = (
            "【注意】這個查詢提到了其他學校，但知識庫**沒有**任何其他學校的資料；"
            "以下段落全部屬於淡江大學領袖禪學社，不可用來回答其他學校的情況。\n\n"
        )
    return header, mismatch


def search_knowledge(query: str, top_k: int = 5) -> dict:
    query = (query or "").strip()
    if not query:
        return {"ok": False, "message": "query 是空的。請說明你要查什麼，例如「招生管道」「十二項特質」「社課籌備流程」。"}

    blocked = internal_data_guard("知識庫")
    if blocked is not None:
        return blocked

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
        blocks.append(f"【來源：{chunk.source}｜淡江內部資料】\n{text}")

    header, mismatch = _attribution_notes(query)
    return {
        "ok": True,
        "low_confidence": False,
        "hit_count": len(hits),
        "message": (
            header
            + mismatch
            + "以下是知識庫裡最相關的段落，請以這些內容為準（全部屬於淡江大學領袖禪學社，"
            "不得寫成其他學校的資料）：\n\n"
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
