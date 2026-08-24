"""系統提示組裝。

提示分層，順序就是事實的優先順序：

    當期真實資料 > 已確認的專案事實 > 檢索到的內容 > 社團通則 > 模型常識

放最前面的最權威。這個順序不是修辭 —— 提示裡有明確寫出優先級，
而且 verification 會實際檢查產出有沒有違反。
"""

from __future__ import annotations

from ..research.entities import ResearchMode, ResearchScope, entity_by_id
from ..research.verifier import INTERNAL_ATTRIBUTION
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
    "反過來也一樣：除了標明【外校】的段落，檢索內容**全部是淡江大學領袖禪學社的資料**；"
    "使用者問其他學校時，不可以把這些內容說成該校的做法——查不到該校資料就明說沒有。"
)


INTERNAL_MODE_BLOCK = (
    "## 研究模式：淡江內部資料\n\n"
    "這一輪只能使用：本學期真實資料、淡江社團知識庫與劇本、淡江歷年檔案、"
    "使用者明確提供的內容。\n"
    "**不可以對任何其他學校的社團下事實結論**——沒有查外校來源，就不知道外校的事。\n"
    f"回答的結尾必須附上一行：「{INTERNAL_ATTRIBUTION}」"
)


def _external_target_lines(scope: ResearchScope) -> list[str]:
    lines: list[str] = []
    for eid in scope.target_entities:
        entity = entity_by_id(eid)
        if entity is None:
            continue
        bits = [entity.name]
        if entity.school:
            bits.append(f"（{entity.school}）")
        if entity.instagram_url:
            bits.append(f"官方 IG：{entity.instagram_url}")
        lines.append("- " + " ".join(bits))
    for school in scope.target_schools:
        if not any(school in l for l in lines):
            lines.append(f"- {school}（尚未確認正式社團）")
    if not lines:
        lines.append("- 不限定單一學校的外校公開資料")
    return lines


def external_mode_block(scope: ResearchScope) -> str:
    lines = [
        "## 研究模式：外部研究",
        "",
        "研究對象：",
        *_external_target_lines(scope),
        "",
        "硬規則：",
        "- 外校事實**只能**來自〈外校已驗證資料〉區塊的摘錄，逐項附上學校名稱。",
        "- 淡江的知識庫、劇本、歷年檔案**不是**外校資料，一個字都不可以拿來描述外校。",
        "- 摘錄裡沒有的細節（日期、講師、活動名、報名方式）一律不可以補寫。",
        "- 沒有可驗證來源時，直接輸出：「目前沒有足夠公開來源確認此資訊，因此不提供確定結論。」",
        "",
        "輸出格式（照這個順序分段）：",
        "【研究對象】明確列出學校與正式社團名稱",
        "【已驗證資料】只放有來源支持的事實，每項標學校",
        "【來源整理】每項資料附來源（帳號／網址／檢索日期）",
        "【可能推測】明確標記為 AI 推測的內容",
        "【淡江可採用建議】給淡江的策略——不代表外校事實",
        "【尚待確認】沒有足夠證據的部分",
    ]
    return "\n".join(lines)


def comparative_mode_block(scope: ResearchScope) -> str:
    lines = [
        "## 研究模式：比較分析",
        "",
        "研究對象：",
        *_external_target_lines(scope),
        "",
        "硬規則：兩邊資料必須完全分開。",
        "- 【外校已驗證資料】只能引用外校來源摘錄，逐項標學校名稱。",
        "- 【淡江內部資料】只能引用淡江自己的資料。",
        "- **建議是給淡江的策略，不得寫成外校已經實際使用的做法。**",
        "- 淡江的活動名稱（例如浮游花手作）不可以出現在外校段落裡。",
        "",
        "輸出格式（照這個順序分段）：",
        "【研究對象】",
        "【外校已驗證資料】",
        "【淡江內部資料】",
        "【兩者差異】",
        "【淡江可採用建議】",
        "【尚待確認】",
    ]
    return "\n".join(lines)


def build_system_prompt(
    *,
    state: OrchestrationState,
    routing: Routing,
    context_block: str,
    destination: str,
    project_facts: dict[str, str] | None = None,
) -> str:
    parts = [BASE, "## 資料優先序\n\n" + RAG_PRIORITY]

    # 0. 研究模式（決定資料能不能用在哪一邊）
    scope = ResearchScope.from_dict(state.research_scope) if state.research_scope else None
    if scope is not None:
        if scope.mode == ResearchMode.EXTERNAL:
            parts.append(external_mode_block(scope))
        elif scope.mode == ResearchMode.COMPARATIVE:
            parts.append(comparative_mode_block(scope))
        elif scope.internal_only_requested:
            parts.append(INTERNAL_MODE_BLOCK)

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
        f"- 類型：{routing.skill.label}",
        f"- 計畫：\n{plan}",
    ]
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

    # 4. 檢索到的內容
    if context_block.strip():
        parts.append(context_block)

    # 5. 落點
    parts.append(
        f"## 產出落點\n\n使用者目前的設定是「{DEST_LABEL.get(destination, destination)}」。"
        "除非他在這則訊息裡另外指定，否則呼叫工具時不要填 destination 參數。"
    )

    return "\n\n---\n\n".join(parts)
