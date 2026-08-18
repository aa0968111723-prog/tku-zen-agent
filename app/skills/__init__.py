"""Skill 定義與路由。

目的：未來可以有 50+ 個工具，但每次請求只把相關的少數幾個交給模型。
開源模型的工具選擇正確率會隨工具數量明顯下降，而且每個 schema 都要
佔輸入 token —— 全部塞給它既慢又不準。

路由刻意用規則而不是再叫一次模型：
  · 確定性，同樣輸入永遠同樣結果，測得起來
  · 不花額度、不增加延遲
  · 分類錯的成本很低（工具集是超集合，缺的話 fallback 會補）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 每個 skill 都會有的基本工具
BASE_TOOLS = ("search_knowledge", "get_current_term")


@dataclass(frozen=True)
class Skill:
    name: str
    label: str
    keywords: tuple[str, ...]
    tools: tuple[str, ...]
    task_type: str
    artifacts_expected: tuple[str, ...] = ()
    required_facts: tuple[str, ...] = ()
    playbook_hints: tuple[str, ...] = ()
    weight: float = 1.0
    extra_guidance: str = ""

    def tool_names(self) -> tuple[str, ...]:
        seen: list[str] = []
        for t in BASE_TOOLS + self.tools:
            if t not in seen:
                seen.append(t)
        return tuple(seen)


SKILLS: tuple[Skill, ...] = (
    Skill(
        name="event_planning",
        label="活動籌備",
        keywords=(
            "茶會", "演講", "社課", "活動", "企劃", "企畫", "細流", "流程", "籌備",
            "營隊", "挑戰營", "禪訓營", "社大", "社遊", "破冰", "行前", "場佈",
            "期初", "期中", "期末", "教案", "主持稿",
        ),
        tools=("search_previous_examples", "create_document", "create_spreadsheet", "create_slides"),
        task_type="event_planning",
        artifacts_expected=("document",),
        required_facts=("academic_year", "semester"),
        playbook_hints=("社課排程與籌備", "營隊籌備"),
        extra_guidance="活動類產出通常要「企劃書（文件）＋細流（試算表）」成對，不要只給一半。",
    ),
    Skill(
        name="recruitment",
        label="招生",
        keywords=(
            "招生", "新生", "路宣", "個接", "接引", "入社", "文宣", "海報", "貼文",
            "ig", "instagram", "fb", "臉書", "限動", "社群", "宣傳", "報名表",
            "招新", "擺攤", "班宣",
        ),
        tools=(
            "search_previous_examples", "create_document", "create_spreadsheet", "create_google_form",
        ),
        task_type="recruitment",
        artifacts_expected=("document",),
        required_facts=("academic_year", "semester", "recruitment_period"),
        playbook_hints=("招生企劃", "文宣與社群貼文"),
        extra_guidance=(
            "對外文案一定要先查〈文宣與社群貼文〉劇本的語氣規範。"
            "不可以寫療效宣稱，不可以寫成宗教招募。"
        ),
    ),
    Skill(
        name="evaluation",
        label="社團評鑑",
        keywords=(
            "評鑑", "社評", "績效", "成果報告", "課外組", "年度報告", "自我評估",
            "章程", "會議紀錄", "社員大會", "行事曆",
        ),
        tools=("search_previous_examples", "create_document", "create_spreadsheet"),
        task_type="evaluation",
        artifacts_expected=("document", "spreadsheet"),
        required_facts=("academic_year",),
        playbook_hints=("社團評鑑",),
        extra_guidance="這是交給學校的正式文件，人數、日期、姓名一律不可編造，沒有就填「待填」。",
    ),
    Skill(
        name="finance",
        label="經費",
        keywords=("預算", "經費", "核銷", "收支", "財務", "社費", "記帳", "發票", "報帳", "結餘", "採買"),
        tools=("search_previous_examples", "create_spreadsheet", "create_document"),
        task_type="finance",
        artifacts_expected=("spreadsheet",),
        required_facts=("academic_year", "semester"),
        playbook_hints=("經費預算",),
        extra_guidance="金額欄位要用公式（=SUM 之類），不要手算後填死數字。",
    ),
    Skill(
        name="handover",
        label="幹部交接",
        keywords=("交接", "傳承", "幹部手冊", "職掌", "幹部訓練", "組輔", "家族長", "新幹部"),
        tools=("search_previous_examples", "create_document", "create_spreadsheet"),
        task_type="handover",
        artifacts_expected=("document",),
        playbook_hints=("幹部交接",),
        extra_guidance="這是對內文件，可以完整使用社團語彙，不用像對外文宣那樣淡化禪法脈絡。",
    ),
    Skill(
        name="google_workspace",
        label="表單與試算表",
        keywords=(
            "google 表單", "google表單", "表單", "問卷", "回饋單", "回饋", "意願調查",
            "意見調查", "統計表", "報名表單", "調查表",
        ),
        tools=("search_previous_examples", "create_google_form", "create_spreadsheet"),
        task_type="documents",
        artifacts_expected=("form",),
        playbook_hints=("表單設計",),
        # 「問卷」「表單」講的是**要產出什麼**，比「社課」「茶會」這種
        # 只說明主題的活動名詞更能決定路由。權重反映這個差別 ——
        # 否則「社課回饋問卷」會被歸成活動籌備，拿不到表單工具。
        weight=1.25,
        extra_guidance="問卷一定要有開放題，而且要問「怎麼知道我們的」——那題直接決定招生管道成效。",
    ),
    Skill(
        name="documents",
        label="一般文件",
        keywords=("文件", "報告", "簡報", "投影片", "ppt", "word", "excel", "表格", "清單", "排程", "公文", "邀請函"),
        tools=("search_previous_examples", "create_document", "create_spreadsheet", "create_slides"),
        task_type="documents",
        artifacts_expected=("document",),
        weight=0.8,   # 通用技能，關鍵字比較泛，權重調低避免搶走專門技能
    ),
    Skill(
        name="knowledge",
        label="查詢",
        keywords=(
            "是什麼", "為什麼", "怎麼", "介紹", "說明", "解釋", "誰", "哪裡", "什麼時候",
            "社長", "社費", "地點", "時間", "十二項特質", "印心", "禪法", "宗旨", "精神",
        ),
        tools=("search_previous_examples",),
        task_type="knowledge",
        artifacts_expected=(),
        weight=0.7,
    ),
)

SKILL_BY_NAME = {s.name: s for s in SKILLS}
DEFAULT_SKILL = SKILL_BY_NAME["documents"]

# 明確要求「做出檔案」的訊號 —— 有這些就不會被歸成純查詢
_MAKE_VERBS = re.compile(
    r"(幫我(做|寫|生|建|列|排|規劃|整理)|做一?[份個張]|產出|產生|生成|建立|寫一?[份篇]|"
    r"排一?[份張]|列一?[份張]|給我一?[份張個]|做成|輸出|匯出|來一?[份張])"
)


def _score(message: str, skill: Skill) -> float:
    low = message.lower()
    hits = 0.0
    for kw in skill.keywords:
        if kw.lower() in low:
            # 長關鍵字更有鑑別力（「挑戰營」比「活動」更能定位）
            hits += 1.0 + 0.35 * max(0, len(kw) - 2)
    return hits * skill.weight


def wants_artifact(message: str) -> bool:
    return bool(_MAKE_VERBS.search(message))


@dataclass
class Routing:
    skill: Skill
    score: float
    runner_up: str = ""
    produce_artifact: bool = True
    scores: dict[str, float] = field(default_factory=dict)


def route(message: str) -> Routing:
    """把使用者輸入分到一個 skill，決定這一輪要暴露哪些工具。"""
    scores = {s.name: _score(message, s) for s in SKILLS}
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_name, best_score = ranked[0]

    produce = wants_artifact(message)

    if best_score <= 0:
        # 完全沒命中：想產檔就給通用文件技能，否則當查詢
        skill = DEFAULT_SKILL if produce else SKILL_BY_NAME["knowledge"]
        return Routing(skill=skill, score=0.0, produce_artifact=produce, scores=scores)

    skill = SKILL_BY_NAME[best_name]

    # 命中的是查詢技能，但語句明顯是要產出東西 → 換成能產檔的技能
    if skill.name == "knowledge" and produce:
        for name, sc in ranked[1:]:
            if sc > 0 and SKILL_BY_NAME[name].artifacts_expected:
                skill = SKILL_BY_NAME[name]
                break
        else:
            skill = DEFAULT_SKILL

    return Routing(
        skill=skill,
        score=best_score,
        runner_up=ranked[1][0] if len(ranked) > 1 else "",
        produce_artifact=produce or bool(skill.artifacts_expected and skill.name != "knowledge"),
        scores=scores,
    )
