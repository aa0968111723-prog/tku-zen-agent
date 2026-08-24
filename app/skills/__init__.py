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
        name="event_planning",
        label="活動籌備",
        deliverables=("細流", "流程表", "教案", "主持稿", "行前通知", "分工表"),
        keywords=(
            "茶會", "演講", "社課", "挑戰營", "禪訓營", "營隊", "社大", "社遊",
            "期初", "期中", "期末", "破冰", "場佈", "籌備", "行前", "凝聚",
        ),
        weak=("活動", "企劃", "企畫", "流程"),
        tools=("search_previous_examples", "create_document", "create_spreadsheet", "create_slides"),
        task_type="event_planning",
        artifacts_expected=("document",),
        required_facts=("academic_year", "semester"),
        playbook_hints=("社課排程與籌備", "營隊籌備"),
        extra_guidance="活動類產出通常要「企劃書（文件）＋細流（試算表）」成對，不要只給一半。",
    ),
    Skill(
        name="social_research",
        label="外校社群研究",
        deliverables=("研究", "比較", "分析", "定位"),
        keywords=(
            "其他學校", "外校", "北科", "北藝", "禪心社", "領袖社", "禪學社", "策略比較", "社群研究",
        ),
        tools=("search_social_references", "compare_social_strategies", "analyze_social_positioning"),
        task_type="social_research",
        playbook_hints=("外校社群比較", "社群研究"),
        extra_guidance="外校資料只能當公開參考；研究結果必須附學校名與來源檔名，禁止照抄或當作淡江事實。",
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
        tools=("search_previous_examples", "create_document", "create_spreadsheet", "create_slides"),
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

# 問「我們是什麼樣的社團」—— 認識性問題，答案在知識庫，不用產檔
_IDENTITY_QUESTION = re.compile(
    r"(是什麼|什麼樣的|是不是|為什麼|介紹一下|通常怎麼|有哪些活動|在做什麼|做些什麼|怎麼回)"
)

_QUERY_INTENT = re.compile(r"(查詢|查一下|查查看|列出|目前有|有哪些|請問)")

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
                candidates = tuple(t for t in candidates if t in BASE_TOOLS or t == self.preferred_tool)
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
