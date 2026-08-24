"""系統提示組裝。

提示分層，順序就是事實的優先順序：

    當期真實資料 > 已確認的專案事實 > 檢索到的內容 > 社團通則 > 模型常識

放最前面的最權威。這個順序不是修辭 —— 提示裡有明確寫出優先級，
而且 verification 會實際檢查產出有沒有違反。
"""

from __future__ import annotations

from ..services import current_term as term_service
from ..skills import Routing
from .state import OrchestrationState

BASE = """你是「淡江大學領袖禪學社」的專屬 AI 助理，服務對象是社團幹部與社員。

## 最重要的一件事：動手做，不要只給建議

使用者說「幫我做一份社課排程表」，你就要**真的呼叫工具把檔案做出來**，
而不是在對話裡貼一段表格說「你可以參考這樣做」。
能自己合理決定的（欄位設計、排版、章節結構、範例內容）就自己決定，不要反問。
只有缺「會改變結果的必要事實」時才問一句。

## 事實的優先順序（由高到低，不可顛倒）

1. **本學期真實資料** —— 今年的事實只認這個來源
2. **已確認的專案事實** —— 這個專案裡使用者親口告訴你的
3. **檢索到的內容** —— 社團知識庫、任務劇本、歷年檔案
4. 你自己的常識 —— 只能用在通用做法，不能用來補社團專屬資訊

**歷年檔案是範例，不是今年的事實。**
它的格式、章節、寫法都可以照抄；但裡面的日期、人名、金額、人數都是當年的，
不可以搬到今年的產出裡。

## 絕對不可以編造

社長姓名、幹部名單、社課時間地點、社費金額、報名連結、確切日期 ——
「本學期真實資料」裡沒有就是沒有。直接說還沒設定、請使用者補，
做檔案時該欄位填「待填」。編一個看起來合理的值是最嚴重的錯誤。

## 語氣

- **對外**（招生文宣、IG 貼文、海報、給校外單位）：主打自我成長、紓壓、專注、
  人際、找到夥伴。溫暖、生活化、貼近大學生。禪定是「方法」，宗教脈絡淡化。
- **對內**（幹部、社員、傳承文件）：完整使用社團語彙（護持、凝聚、發願、家族、
  組輔、路宣、個接、體驗禪）。
- 一律繁體中文、台灣用語。稱社團用「我們」，稱讀者用「你」。

## 紅線

- **不做療效宣稱**：不可以寫「治療、改善憂鬱、保證成功、改運、開智慧」這類承諾。
- **不要把社團寫成宗教招募**：對外以成長與陪伴為主軸。
- 提到法脈、創辦人、基金會時，比照知識庫的中性轉述，不要加碼渲染。

## 回覆方式

不要輸出你的推理過程或思考步驟。直接給結果：做了什麼、檔案叫什麼、
還缺什麼要使用者補、建議的下一步（1–2 個）。
"""

DEST_LABEL = {"local": "只存本機", "drive": "上傳 Google 雲端硬碟", "both": "本機與雲端都存"}


RAG_PRIORITY = (
    "本學期真實資料 > 規範／劇本 > 淡江歷年 > 外校公開參考 > 模型常識。"
    "外校公開參考只能用於比較分析，禁止照抄，也不是淡江資料。"
)


def build_system_prompt(
    *,
    state: OrchestrationState,
    routing: Routing,
    context_block: str,
    destination: str,
    project_facts: dict[str, str] | None = None,
    previous_artifacts: list[dict] | None = None,
    research_sources: list[dict] | None = None,
) -> str:
    parts = [BASE, "## 資料優先序\n\n" + RAG_PRIORITY]

    # 1. 當期真實資料（最高優先）
    parts.append(term_service.load().prompt_block())

    # 2. 專案裡已確認的事實
    if project_facts:
        lines = ["## 這個專案已確認的事實", "", "（使用者在這個專案裡親口說過的，可以直接採用）", ""]
        lines += [f"- **{k}**：{v}" for k, v in project_facts.items()]
        parts.append("\n".join(lines))

    # 3. 這一輪的任務與計畫
    plan = "\n".join(f"{i}. {s.description}" for i, s in enumerate(state.plan_steps, 1))
    task_block = [
        "## 這一輪的任務",
        "",
        f"- 類型：{routing.label}",
        f"- 計畫：\n{plan}",
    ]
    if routing.task_sequence:
        task_block.append("- 任務依賴：" + " → ".join(routing.task_sequence))
        task_block.append("- 先完成研究來源，再把已驗證洞察改寫成淡江自己的產出；不可把外校資料當淡江事實。")
    if state.artifacts_expected:
        from .planner import ARTIFACT_LABEL

        kinds = "、".join(ARTIFACT_LABEL.get(a, a) for a in state.artifacts_expected)
        task_block.append(f"- 預期產出：{kinds}（一定要真的呼叫工具做出來）")
    if routing.skill.extra_guidance:
        task_block.append(f"- 注意：{routing.skill.extra_guidance}")
    if state.missing_facts:
        labels = "、".join(
            term_service.FIELD_BY_KEY[k].label
            for k in state.missing_facts
            if k in term_service.FIELD_BY_KEY
        )
        if labels:
            task_block.append(
                f"- **這些今年的資料還沒設定：{labels}。**"
                "產出裡對應欄位一律填「待填」，並在回覆最後提醒使用者去補。"
            )
    parts.append("\n".join(task_block))

    if previous_artifacts:
        lines = ["## 這個專案最近的產出", "", "以下是可沿用或轉換的最新版本；修改時保留版本關聯，不要要求使用者重新描述全部內容。"]
        lines.extend(
            f"- {a.get('filename', '未命名')}（版本 {a.get('version', 1)}，artifact_id={a.get('artifact_id', '')}）"
            for a in previous_artifacts[:8]
        )
        parts.append("\n".join(lines))

    if research_sources:
        lines = ["## 這個專案已保存的研究來源", "", "研究結論只能引用下列來源；verification=needs_verification 的來源必須標成待驗證。"]
        for source in research_sources[:12]:
            lines.append(
                f"- {source.get('title') or source.get('source_file') or '未命名來源'}｜"
                f"{source.get('url') or '網址待補'}｜可信度 {source.get('credibility', 0)}｜"
                f"{source.get('verification', 'needs_verification')}"
            )
        parts.append("\n".join(lines))

    # 4. 檢索到的內容
    if context_block.strip():
        parts.append(context_block)

    # 5. 落點
    parts.append(
        f"## 產出落點\n\n使用者目前的設定是「{DEST_LABEL.get(destination, destination)}」。"
        "除非他在這則訊息裡另外指定，否則呼叫工具時不要填 destination 參數。"
    )

    return "\n\n---\n\n".join(parts)
