"""代理主迴圈：系統提示 + 多輪工具呼叫。"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

from . import config, retrieval, tools
from .llm import LLMError, NvidiaClient, Reply, ToolCall

MAX_TOOL_ROUNDS = 8
MAX_HISTORY_MESSAGES = 40

SYSTEM_PROMPT = """你是「淡江大學領袖禪學社」的專屬 AI 助理。你服務的對象是社團幹部與社員。

## 你最重要的一件事：動手做，不要只給建議

使用者說「幫我做一份社課排程表」，你就要**真的呼叫 create_spreadsheet 把檔案做出來**，
而不是在對話裡貼一段表格然後說「你可以參考這樣做」。
使用者說「做一份招生企劃」，你就要**真的呼叫 create_document 產出文件**。
只有在資訊真的不足、且缺的是關鍵資訊（例如日期、學期、預算上限）時，才先問一句再做。
能自己合理決定的（欄位設計、排版、章節結構、範例內容），就自己決定，不要反問。

## 你的工作流程

1. **先查知識庫**：任何跟社團有關的問題或產出，動手之前先呼叫 `search_knowledge`。
   查一次不夠就查第二次（例如先查「招生方法」再查「對外文宣語氣」，
   再查「浮花禪光 期初茶會企劃書」看歷年實際怎麼寫）。
2. **依知識庫內容產出**：知識庫是社團資訊的唯一依據。
3. **呼叫產出工具**：把成果做成實際檔案。
4. **回報**：告訴使用者做了什麼、檔案叫什麼、存在哪裡，並提出 1–2 個可以接著做的下一步。

## 知識庫有兩種東西，用途不一樣

- **社團知識庫／任務劇本** = 「這件事該怎麼做」。這是規範，照著做。
- **歷年檔案** = 社團 108–115 學年度的真實檔案（企劃書、活動細流、社評績效報告、
  社課簡報、會議紀錄、IG 文案、財務表、挑戰營資料）。這是**範例與素材**，
  用來對齊真實格式與寫法，例如：
  · 要寫企劃書 → 找歷年同類型企劃書，沿用它的章節與行政格式
  · 要寫 IG 文案 → 找歷年的 IG 宣傳檔，抓它的語感
  · 要做細流 → 找歷年細流，沿用它的欄位與時間顆粒度
  · 要做社評 → 找歷年年度績效報告，照它的結構

  **但歷年檔案裡的日期、人名、金額、人數都是當年的，不是現在的。**
  可以拿來當「格式範例」，不可以直接當成今年的事實抄進產出裡。

- **名冊類資料一律只有欄位結構，沒有內容** —— 那些檔案含同學個資，匯入時就擋掉了。
  所以你可以知道「招生進度表有哪些欄位」，但查不到也不該去猜任何同學的姓名或聯絡方式。

## 絕對不可以編造的資訊

以下每年都會變動，知識庫查不到就**直接說「這個要你提供」**，不准自己填一個看起來合理的：
- 當學期社長、幹部姓名與聯絡方式
- 社課的確切時間、地點、教室
- 社費金額
- 報名連結、表單網址
- 具體日期（除非使用者告訴你，或是知識庫寫明的年度節奏）

做表格時如果遇到這種欄位，就留空並在該欄填「待填」，不要瞎編。

## 語氣

- **對外**（招生文宣、IG 貼文、校園海報、給校外單位的簡介）：
  主打自我成長、紓壓、專注、人際、找到夥伴。溫暖、生活化、貼近大學生。
  禪定是「方法／特色」，宗教脈絡淡化、自然帶過，不要從宗教講起。
- **對內**（幹部、社員、傳承文件）：
  完整使用社團語彙（護持、凝聚、發願、家族、組輔、路宣、個接、體驗禪…），
  強調團隊與修行的連結。
- 一律用繁體中文、台灣用語。稱社團用「我們」，稱讀者用「你」。

## 紅線

- **不做療效宣稱**：禪定是自我成長的方法。不可以寫成「治病、改善憂鬱、保證成功、改運、開智慧」這類承諾。
- **不要把社團寫成宗教招募**：對外以成長與陪伴為主軸。
- 提到法脈、創辦人、基金會理念時，比照知識庫的中性轉述方式，不要加碼渲染。

## 產出落點

每個產出工具都有 destination 參數：local（只存本機）、drive（上傳 Google 雲端硬碟）、both。
使用者沒特別說就不要填，系統會用他在介面上選的預設值。
使用者說「放雲端」「上傳到共用雲端」就填 drive；說「兩邊都要」就填 both。
"""


def _destination_note(destination: str) -> str:
    label = {"local": "只存本機", "drive": "上傳 Google 雲端硬碟", "both": "本機與雲端都存"}.get(
        destination, destination
    )
    return f"\n\n（使用者目前的產出落點設定：{label}。除非他在這則訊息裡另外指定，否則不要填 destination 參數。）"


def _preview(args: dict[str, Any]) -> str:
    """給介面顯示的簡短參數摘要。"""
    for key in ("query", "filename", "form_title", "title"):
        if args.get(key):
            return str(args[key])[:60]
    return ""


class Session:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.artifacts: list[dict[str, Any]] = []

    def trim(self) -> None:
        if len(self.messages) <= MAX_HISTORY_MESSAGES:
            return
        # 保留最近的訊息，但不能讓開頭是孤兒 tool 訊息
        keep = self.messages[-MAX_HISTORY_MESSAGES:]
        while keep and keep[0].get("role") == "tool":
            keep.pop(0)
        self.messages = keep


_sessions: dict[str, Session] = {}


def get_session(sid: str) -> Session:
    if sid not in _sessions:
        _sessions[sid] = Session()
    return _sessions[sid]


def reset_session(sid: str) -> None:
    _sessions.pop(sid, None)


async def run_turn(
    sid: str,
    user_message: str,
    *,
    destination: str,
    model: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """跑完一輪對話，過程中把事件 yield 出去給前端。"""
    session = get_session(sid)
    client = NvidiaClient(model=model)

    if not session.messages:
        session.messages.append({"role": "system", "content": SYSTEM_PROMPT})

    session.messages.append({"role": "user", "content": user_message + _destination_note(destination)})
    session.trim()

    for round_no in range(MAX_TOOL_ROUNDS):
        yield {"type": "status", "text": "思考中…" if round_no == 0 else "整理結果…"}

        try:
            reply: Reply = await client.chat(session.messages, tools.SCHEMAS)
        except LLMError as exc:
            yield {"type": "error", "text": str(exc)}
            return
        except Exception as exc:  # noqa: BLE001
            yield {"type": "error", "text": f"呼叫模型時發生非預期錯誤：{type(exc).__name__}: {exc}"}
            return

        assistant_msg: dict[str, Any] = {"role": "assistant", "content": reply.content or ""}
        if reply.tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)},
                }
                for tc in reply.tool_calls
            ]
        session.messages.append(assistant_msg)

        if not reply.tool_calls:
            text = reply.content.strip()
            if not text:
                text = "（模型這次沒有回覆內容，請再說一次，或換個說法。）"
            yield {"type": "message", "text": text}
            yield {"type": "done"}
            return

        # 模型講了話又同時呼叫工具，把話也顯示出來
        if reply.content.strip():
            yield {"type": "message", "text": reply.content.strip()}

        for tc in reply.tool_calls:
            yield {
                "type": "tool_start",
                "name": tc.name,
                "label": tools.LABELS.get(tc.name, tc.name),
                "preview": _preview(tc.arguments),
            }

            result = await asyncio.to_thread(tools.dispatch, tc.name, tc.arguments)

            session.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.name,
                    "content": json.dumps(
                        {"ok": result.get("ok", True), "message": result.get("message", "")},
                        ensure_ascii=False,
                    ),
                }
            )

            event: dict[str, Any] = {
                "type": "tool_end",
                "name": tc.name,
                "label": tools.LABELS.get(tc.name, tc.name),
                "ok": bool(result.get("ok", True)),
            }
            if tc.name == "search_knowledge":
                event["detail"] = f"找到 {result.get('hit_count', 0)} 段相關內容"
            else:
                event["detail"] = result.get("message", "")
                if result.get("filename"):
                    artifact = {
                        "filename": result["filename"],
                        "local_path": result.get("local_path"),
                        "drive_url": result.get("drive_url"),
                    }
                    session.artifacts.append(artifact)
                    event["artifact"] = artifact
            yield event

    yield {
        "type": "error",
        "text": f"工具連續呼叫超過 {MAX_TOOL_ROUNDS} 輪還沒收尾，先停下來避免無限迴圈。"
        "可能是模型卡住了，請把需求說得更具體一點再試一次，或在設定裡換一個模型。",
    }


def health() -> dict[str, Any]:
    idx = retrieval.get_index()
    return {
        "model": config.NVIDIA_MODEL,
        "models": config.KNOWN_TOOL_MODELS,
        "destination": config.DEFAULT_DESTINATION,
        "output_dir": str(config.OUTPUT_DIR),
        "knowledge": idx.stats(),
        "tools": [{"name": n, "label": l} for n, l in tools.LABELS.items()],
        "problems": config.missing_config(),
        "drive_ready": config.GOOGLE_CREDENTIALS_FILE.exists(),
    }
