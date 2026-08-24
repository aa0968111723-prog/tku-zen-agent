"""Skill 定義與路由。

目的：未來可以有 50+ 個工具，但每次請求只把相關的少數幾個交給模型。
開源模型的工具選擇正確率會隨工具數量明顯下降，而且每個 schema 都要
佔輸入 token —— 全部塞給它既慢又不準。

路由刻意用規則而不是再叫一次模型：
  · 確定性，同樣輸入永遠同樣結果，測得起來
  · 不花額度、不增加延遲
  · 分類錯的成本很低（每個 skill 的工具集都是超集合）

關鍵字分三層，因為它們在句子裡扮演的角色不同：

  deliverables —— **要產出什麼**（預算、報名表、文宣、細流、績效報告）
                  這一層決定路由。「挑戰營的預算」要的是預算表，不是營隊企劃。
  keywords     —— **關於什麼**（茶會、社課、挑戰營、期初）
  weak         —— 泛用名詞（活動、企劃、流程），幾乎每種任務都會出現，
                  當主要訊號會一直誤判
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 每個 skill 都會有的基本工具
BASE_TOOLS = ("search_knowledge", "get_current_term")

W_DELIVERABLE = 2.5     # 平權重，不吃長度加成 —— 「預算」跟「績效報告」一樣關鍵
W_TOPIC_BASE = 1.0
W_TOPIC_LEN = 0.35      # 長的主題詞更有鑑別力（「挑戰營」比「活動」精確）
W_WEAK = 0.4


@dataclass(frozen=True)
class Skill:
    name: str
    label: str
    deliverables: tuple[str, ...]
    keywords: tuple[str, ...]
    tools: tuple[str, ...]
    task_type: str
    weak: tuple[str, ...] = ()
    artifacts_expected: tuple[str, ...] = ()
    required_facts: tuple[str, ...] = ()
    playbook_hints: tuple[str, ...] = ()
    extra_guidance: str = ""

    def tool_names(self) -> tuple[str, ...]:
        seen: list[str] = []
        for t in BASE_TOOLS + self.tools:
            if t not in seen:
                seen.append(t)
        return tuple(seen)

    def all_terms(self) -> tuple[str, ...]:
        return self.deliverables + self.keywords + self.weak


SKILLS: tuple[Skill, ...] = (
    Skill(
        name="activity_management",
        label="活動進度與分工",
        deliverables=("待辦", "逾期", "時程狀態"),
        keywords=(
            "活動進度", "缺什麼", "誰負責什麼", "還剩哪些", "未完成", "截止", "期限",
            "組別",
        ),
        weak=("活動", "工作"),
        tools=(
            "update_activity", "add_activity_task", "update_activity_task", "get_activity_status", "list_activities",
        ),
        task_type="event_planning",
        playbook_hints=("活動籌備與分工",),
        extra_guidance="活動狀態要以活動資料與待辦為準；缺欄位、未指派與逾期工作要分開列出。",
    ),
    Skill(
        name="event_planning",
        label="活動籌備",
        deliverables=("細流", "流程表", "教案", "主持稿", "行前通知", "分工表"),
        keywords=(
            "茶會", "演講", "社課", "挑戰營", "禪訓營", "營隊", "社大", "社遊",
            "期初", "期中", "期末", "破冰", "場佈", "籌備", "行前", "凝聚",
        ),
        weak=("活動", "企劃", "企畫", "流程"),
        tools=(
            "create_activity", "get_activity_status", "create_document", "create_spreadsheet", "create_slides",
        ),
        task_type="event_planning",
        artifacts_expected=("document",),
        required_facts=("academic_year", "semester"),
        playbook_hints=("社課排程與籌備", "營隊籌備"),
        extra_guidance=(
            "先建立或更新活動資料，再用同一筆活動產出；未知日期、地點、負責人必須待填。"
            "活動類產出通常要「企劃書（文件）＋細流（試算表）」成對，不要只給一半。"
        ),
    ),
    Skill(
        name="social_research",
        label="外校社群研究",
        deliverables=("研究", "比較", "分析", "定位", "借鏡", "參考做法"),
        # 注意：這裡**不可以**放我們自己的稱呼（禪學社、領袖社、淡江…）。
        # 曾經因為放了「禪學社」，「請用一句話介紹淡江大學領袖禪學社」
        # 被誤判成外校研究。外校研究另外有 _EXTERNAL_INTENT 硬性閘門把關。
        keywords=(
            "其他學校", "外校", "他校", "別的學校", "跨校", "各校",
            "北科", "北藝", "北醫", "台大", "臺大", "政大", "東吳", "世新", "東華",
            "策略比較", "社群研究", "公開 IG", "公開IG", "公開ig",
        ),
        tools=("search_social_references", "compare_social_strategies", "analyze_social_positioning"),
        task_type="social_research",
        playbook_hints=("外校社群比較", "社群研究"),
        extra_guidance=(
            "外校資料只能當公開參考；研究結果必須完整保留工具回傳的來源清單"
            "（含網址、發布者、檢索日期），不得寫出清單以外的校名、社團、講師或活動；"
            "禁止照抄或當作淡江事實。這些資料來自內部整理的公開參考庫，"
            "不是即時網路搜尋，不可以寫成「剛搜尋到」或「目前現況」。"
        ),
    ),
    Skill(
        name="social_publicity",
        label="社群網宣",
        deliverables=("網宣", "社群文宣", "貼文", "輪播", "限動", "Reels", "社群內容"),
        keywords=(
            "網宣", "IG", "ig", "貼文", "限動", "輪播", "Reels", "社群", "招生文宣", "文宣",
        ),
        tools=(
            "create_social_post", "create_social_carousel", "create_social_story",
            "create_reels_script", "create_social_content_calendar",
        ),
        task_type="social_publicity",
        artifacts_expected=("document",),
        required_facts=("academic_year", "semester"),
        playbook_hints=("文宣與社群貼文", "社群內容規劃"),
        extra_guidance="產出前可參考外校公開資料，但文案必須是淡江原創，不得含外校專名、連結、講師或未確認日期。",
    ),
    Skill(
        name="recruitment",
        label="招生",
        deliverables=("招生", "路宣", "個接", "接引", "文宣", "招新", "班宣", "海報"),
        keywords=("新生", "入社", "ig", "instagram", "臉書", "社群", "宣傳", "擺攤"),
        weak=("fb",),
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
        deliverables=("評鑑", "社評", "績效", "成果報告", "成果", "年度報告", "自我評估"),
        keywords=("課外組", "章程", "會議紀錄", "社員大會", "行事曆"),
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
        deliverables=("預算", "經費", "核銷", "收支", "財務", "記帳", "報帳", "結餘"),
        keywords=("社費", "發票", "採買", "領據"),
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
        deliverables=("交接", "傳承", "幹部手冊", "職掌", "新任"),
        keywords=("幹部訓練", "組輔", "家族長", "新幹部", "幹部"),
        tools=("search_previous_examples", "create_document", "create_spreadsheet"),
        task_type="handover",
        artifacts_expected=("document",),
        playbook_hints=("幹部交接",),
        extra_guidance="這是對內文件，可以完整使用社團語彙，不用像對外文宣那樣淡化禪法脈絡。",
    ),
    Skill(
        name="google_workspace",
        label="表單與問卷",
        deliverables=("google 表單", "google表單", "表單", "問卷", "回饋單", "報名表", "意願調查", "意見調查", "調查表"),
        keywords=("回饋", "統計表"),
        tools=("search_previous_examples", "create_google_form", "create_spreadsheet"),
        task_type="documents",
        artifacts_expected=("form",),
        playbook_hints=("表單設計",),
        extra_guidance="問卷一定要有開放題，而且要問「怎麼知道我們的」——那題直接決定招生管道成效。",
    ),
    Skill(
        name="documents",
        label="一般文件",
        deliverables=(),
        keywords=("簡報", "投影片", "ppt", "邀請函", "公文"),
        weak=("文件", "報告", "word", "excel", "表格", "清單", "排程"),
        tools=(
            "get_activity_status", "create_document", "create_spreadsheet", "create_slides",
        ),
        task_type="documents",
        artifacts_expected=("document",),
    ),
    Skill(
        name="knowledge",
        label="查詢",
        deliverables=(),
        keywords=("十二項特質", "印心", "禪法", "宗旨", "精神", "社長", "社費", "沿革", "創社"),
        weak=("介紹", "說明", "解釋"),
        tools=("search_previous_examples",),
        task_type="knowledge",
        artifacts_expected=(),
    ),
)

SKILL_BY_NAME = {s.name: s for s in SKILLS}
DEFAULT_SKILL = SKILL_BY_NAME["documents"]
KNOWLEDGE_SKILL = SKILL_BY_NAME["knowledge"]

# 明確要求「做出檔案」
_MAKE_VERBS = re.compile(
    r"(幫我(做|寫|生|建|列|排|規劃|整理|產)|做一?[份個張]|產出|產生|生成|建立|寫一?[份篇]|"
    r"排一?[份張]|列一?[份張]|給我一?[份張個]|做成|改成|轉成|轉為|輸出|匯出|來一?[份張]|更新一?[份張]|"
    r"沿用上一份|接續剛才|接著做)"
)

# 問「今年的某個具體事實」—— 這種只要一句話回答，不該產檔
_FACT_LOOKUP = re.compile(
    r"(社長是誰|誰是社長|社費(多少|是多少|幾錢)?|多少錢|禮拜幾|星期幾|幾點|"
    r"在哪(裡|間|邊)?|哪間教室|報名連結|報名網址|怎麼報名|地點在)"
)

# 問「我們是什麼樣的社團」—— 認識性問題，答案在知識庫，不用產檔。
# 「介紹」不限「介紹一下」：「請用一句話介紹淡江大學領袖禪學社」也是認識性問題。
# 會撞到「幫我寫一份社團介紹文宣」嗎？不會 —— is_lookup 先檢查 wants_artifact。
_IDENTITY_QUESTION = re.compile(
    r"(是什麼|什麼樣的|是不是|為什麼|介紹|認識一下|通常怎麼|有哪些活動|在做什麼|做些什麼|怎麼回)"
)

_QUERY_INTENT = re.compile(r"(^查|幫我查|查詢|查一下|查查看|查個|列出|目前有|有哪些|請問)")

# 外校研究的硬性閘門：沒有明確提到「別的學校」就絕不啟動外部研究。
# 光是「研究」「比較」「介紹」出現，或訊息裡有我們自己的名字
# （淡江、禪學社、領袖社），都不算外校意圖。
# 學校名稱的比對交給 research.entities 的 registry（含全名「政治大學」與
# 詞界防護「完成大合照 ≠ 成大」）——清單只維護一份，不在這裡另抄一份簡稱。
_EXTERNAL_GENERIC = re.compile(
    r"(其他學校|其他大學|外校|他校|別的學校|別校|跨校|各校|大專院校|"
    r"公開\s*IG|公開\s*ig|公開帳號)"
)


def has_external_intent(message: str) -> bool:
    """使用者是否明確想研究「別的學校」。這是外校研究的必要條件。"""
    from ..research import entities as research_entities

    return bool(_EXTERNAL_GENERIC.search(message)) or research_entities.mentions_external_school(message)

_ACTIVITY_OPERATION_QUERY = re.compile(
    r"(活動.*(?:缺什麼|進度|待辦|分工|負責|逾期|還剩|未完成)|"
    r"誰負責什麼|哪些工作逾期|還剩哪些事項|新增待辦|更新待辦|建立活動|列出活動)"
)

_CONTINUATION_ONLY = re.compile(r"^\s*(接續(?:剛才|上一個)?|繼續(?:剛才)?|接著做|沿用上一個活動資料)\s*[。！!]*$", re.I)


def _score(message: str, skill: Skill) -> float:
    low = message.lower()
    total = 0.0
    for kw in skill.deliverables:
        if kw.lower() in low:
            total += W_DELIVERABLE
    for kw in skill.keywords:
        if kw.lower() in low:
            total += W_TOPIC_BASE + W_TOPIC_LEN * max(0, len(kw) - 2)
    for kw in skill.weak:
        if kw.lower() in low:
            total += W_WEAK
    return total


def wants_artifact(message: str) -> bool:
    return bool(_MAKE_VERBS.search(message))


def is_lookup(message: str) -> bool:
    """只是要一句話答案，不是要檔案。"""
    if wants_artifact(message):
        return False
    # 明確提到別的學校時不算純查詢 —— 「研究其他學校怎麼招生」要走外校研究
    if has_external_intent(message):
        return False
    return bool(_FACT_LOOKUP.search(message) or _IDENTITY_QUESTION.search(message) or _QUERY_INTENT.search(message))


def is_continuation_only(message: str) -> bool:
    """只說「繼續」時，交給上一個 project 的任務圖處理。"""
    return bool(_CONTINUATION_ONLY.match(message or ""))


@dataclass
class Routing:
    skill: Skill
    score: float
    runner_up: str = ""
    produce_artifact: bool = True
    scores: dict[str, float] = field(default_factory=dict)
    task_sequence: tuple[str, ...] = ()
    display_label: str = ""
    continuation: bool = False
    reuse_previous: bool = False
    preferred_tool: str = ""
    preferred_artifact: str = ""

    def tool_names(self) -> tuple[str, ...]:
        """複合任務暴露依賴節點的工具聯集。"""
        names: list[str] = []
        sequence = self.task_sequence or (self.skill.name,)
        for name in sequence:
            skill = SKILL_BY_NAME.get(name)
            if not skill:
                continue
            candidates = skill.tool_names()
            if self.preferred_tool and name == self.skill.name and self.preferred_tool in candidates:
                scoped = [*BASE_TOOLS]
                if self.continuation:
                    scoped.append("read_artifact")
                scoped.append(self.preferred_tool)
                candidates = tuple(dict.fromkeys(scoped))
            for tool in candidates:
                if tool not in names:
                    names.append(tool)
        return tuple(names)

    @property
    def label(self) -> str:
        return self.display_label or self.skill.label


def route(message: str) -> Routing:
    """把使用者輸入分到一個 skill，決定這一輪要暴露哪些工具。"""
    scores = {s.name: _score(message, s) for s in SKILLS}

    # 外校研究是唯一會去翻外部參考資料的技能，必須有明確的外校意圖
    # 才能參與競爭 —— 不能因為出現「介紹」「比較」就贏過知識庫查詢。
    if not has_external_intent(message):
        scores["social_research"] = 0.0

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    continuation = bool(re.search(r"(接續|繼續|剛才|上一份|上次|沿用|改成|轉成|轉為)", message))
    preferred_tool, preferred_artifact = _preferred_output(message)

    # 複合任務要把依賴關係寫進 routing，而不是讓模型自行猜「研究」何時完成。
    if (
        re.search(r"(研究|比較|分析).*(其他學校|外校|他校)", message, re.S)
        and re.search(r"(後|然後|接著|再).*(輪播|貼文|網宣|Reels|限動)", message, re.I | re.S)
    ):
        return Routing(
            skill=SKILL_BY_NAME["social_publicity"],
            score=scores["social_research"] + scores["social_publicity"],
            runner_up="social_research",
            produce_artifact=True,
            scores=scores,
            task_sequence=("social_research", "social_publicity"),
            display_label="外校研究＋淡江網宣",
            continuation=continuation,
            preferred_tool=preferred_tool,
            preferred_artifact=preferred_artifact,
        )

    # 活動營運查詢直接使用正式活動／待辦資料，不讓「目前」落到一般知識查詢。
    if _ACTIVITY_OPERATION_QUERY.search(message):
        activity_tool = _preferred_activity_tool(message)
        skill = SKILL_BY_NAME["event_planning" if activity_tool == "create_activity" else "activity_management"]
        return Routing(
            skill=skill,
            score=scores[skill.name],
            produce_artifact=False,
            scores=scores,
            continuation=continuation,
            reuse_previous=continuation,
            preferred_tool=activity_tool,
        )

    # 純查詢：路由到 knowledge，連產檔工具都不暴露。
    # 這比「路由到領域 skill 但標記不產檔」更保險 —— 模型看不到 create_*
    # 就不可能手滑生出一個沒人要的檔案。
    if is_lookup(message):
        return Routing(
            skill=KNOWLEDGE_SKILL, score=scores["knowledge"], produce_artifact=False,
            scores=scores, continuation=continuation,
            preferred_tool=preferred_tool,
            preferred_artifact=preferred_artifact,
        )

    best_name, best_score = ranked[0]
    produce = wants_artifact(message)

    if best_score <= 0:
        skill = DEFAULT_SKILL if produce else KNOWLEDGE_SKILL
        return Routing(
            skill=skill, score=0.0, produce_artifact=produce, scores=scores,
            continuation=continuation, reuse_previous=continuation,
            preferred_tool=preferred_tool,
            preferred_artifact=preferred_artifact,
        )

    skill = SKILL_BY_NAME[best_name]

    # 命中查詢技能，但語句明顯要產出東西 → 換成能產檔的技能
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
        produce_artifact=produce or bool(skill.artifacts_expected),
        scores=scores,
        continuation=continuation,
        reuse_previous=continuation,
        preferred_tool=preferred_tool,
        preferred_artifact=preferred_artifact,
    )


def _preferred_output(message: str) -> tuple[str, str]:
    """把明確的轉檔要求縮到單一產出工具，避免模型看到不相關工具。"""
    if re.search(r"(改成|轉成|轉為|做成).*(簡報|投影片|ppt)", message, re.I):
        return "create_slides", "slides"
    if re.search(r"(改成|轉成|轉為|做成).*(Reels|短影片|影片腳本)", message, re.I):
        return "create_reels_script", "document"
    if "輪播" in message:
        return "create_social_carousel", "document"
    if "限動" in message:
        return "create_social_story", "document"
    if re.search(r"(貼文|文案)", message):
        return "create_social_post", "document"
    return "", ""


def _preferred_activity_tool(message: str) -> str:
    if re.search(r"(列出活動|有哪些活動)", message):
        return "list_activities"
    if re.search(r"(新增|建立|加入).*(待辦|工作|分工)", message):
        return "add_activity_task"
    if re.search(r"(更新|修改|完成).*(待辦|工作|分工)", message):
        return "update_activity_task"
    if re.search(r"(建立|新增).*(活動)", message):
        return "create_activity"
    if re.search(r"(更新|修改).*(活動)", message):
        return "update_activity"
    return "get_activity_status"
