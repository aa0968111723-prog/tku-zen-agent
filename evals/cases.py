"""50 個真實社團情境。

每個案例都定義：使用者說什麼、期望路由到哪個 skill、該不該產檔、
檢索結果裡該出現什麼、以及有沒有特殊的 grounding 要求。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Case:
    id: str
    message: str
    skill: str                      # 期望的 skill
    artifacts: tuple[str, ...] = () # 期望產出類型
    expect_context: tuple[str, ...] = ()   # context 裡該出現的關鍵字
    must_not_fabricate: tuple[str, ...] = ()  # 這些今年的事實沒設定時不可以編
    tools: tuple[str, ...] = ()     # 期望能用到的工具
    note: str = ""
    term_fields: dict[str, str] = field(default_factory=dict)  # 這個案例要先設定的當期資料
    tags: tuple[str, ...] = ()
    # SSE 事件流斷言（runner 驗證，硬門檻）。元素形如 "clarification_needed"
    # （只比事件 type）或 "research_status=no_reliable_source"
    # （type＋識別欄位值，識別欄位見 runner._EVENT_VALUE_FIELD）。
    expected_events: tuple[str, ...] = ()   # 事件流必須出現
    forbidden_events: tuple[str, ...] = ()  # 事件流不得出現
    # 這個案例預期的外校研究對象（學校簡稱或全名）。預設由 message 點名的
    # 學校推得；回答提到範圍外的外校 → 學校歸屬幻覺（hallucination=0）。
    expected_schools: tuple[str, ...] = ()


CASES: list[Case] = [
    # ── 活動營運資料 ──────────────────────────────────────
    Case("act-01", "建立活動：期初茶會，日期地點先待填", "event_planning", (), ("茶會",),
         tools=("create_activity",), tags=("深度優化", "活動管理")),
    Case("act-02", "這場活動目前缺什麼", "activity_management", (), (),
         tools=("get_activity_status",), tags=("深度優化", "活動管理")),
    Case("act-03", "這場活動誰負責什麼", "activity_management", (), (),
         tools=("get_activity_status",), tags=("深度優化", "活動管理")),
    Case("act-04", "這場活動哪些工作逾期", "activity_management", (), (),
         tools=("get_activity_status",), tags=("深度優化", "活動管理")),
    Case("act-05", "列出活動", "activity_management", (), (),
         tools=("list_activities",), tags=("深度優化", "活動管理")),
    # ── 複合任務與延續 ────────────────────────────────────
    Case(
        "comp-01",
        "研究其他學校招生方式後，幫我產生淡江招生輪播",
        "social_publicity",
        ("document",),
        ("招生",),
        tools=("search_social_references", "create_social_carousel"),
        tags=("深度優化", "複合任務"),
    ),
    # ── 活動籌備 ──────────────────────────────────────────
    Case("plan-01", "幫我做期初茶會企劃", "event_planning", ("document",), ("茶會",),
         tools=("create_document",), tags=("必測",)),
    Case("plan-02", "幫我做 18 週社課排程", "event_planning", ("document",), ("社課",),
         tools=("create_spreadsheet",), tags=("必測",)),
    Case("plan-03", "期初演講的企劃書要怎麼寫", "event_planning", ("document",), ("演講",)),
    Case("plan-04", "幫我排期末社大的流程", "event_planning", ("document",), ("社大", "期末")),
    Case("plan-05", "做一份挑戰營的活動細流", "event_planning", ("document",), ("挑戰營",)),
    Case("plan-06", "挑戰營要準備什麼器材", "event_planning", ("document",), ("營",)),
    Case("plan-07", "幫我寫社課教案，主題是情緒管理", "event_planning", ("document",), ("社課",)),
    Case("plan-08", "期初茶會的主持稿", "event_planning", ("document",), ("茶會",)),
    Case("plan-09", "幫我規劃一場戶外社遊", "event_planning", ("document",), ("社遊", "戶外", "活動")),
    Case("plan-10", "禪訓營的行前通知", "event_planning", ("document",), ("禪訓營", "營")),
    Case("plan-11", "社課當天工作人員怎麼分工", "event_planning", ("document",), ("社課",)),
    Case("plan-12", "幫我做一份社課的凝聚分享引導問題", "event_planning", ("document",), ("凝聚",)),

    # ── 招生 ──────────────────────────────────────────────
    Case("rec-01", "幫我做這學期的招生企劃", "recruitment", ("document",), ("招生",),
         tools=("create_document",), tags=("必測",)),
    Case("rec-02", "幫我寫招生 IG 文案", "recruitment", ("document",), ("招生", "文宣"),
         tools=("create_document",), tags=("必測", "對外")),
    Case("rec-03", "路宣排班表", "recruitment", ("document",), ("路宣", "招生")),
    Case("rec-04", "個接要怎麼跟新生開口", "recruitment", ("document",), ("個接", "招生")),
    Case("rec-05", "幫我寫班宣要用的訊息", "recruitment", ("document",), ("招生", "宣")),
    Case("rec-06", "做一份招生名單追蹤表", "recruitment", ("document",), ("招生",)),
    Case("rec-07", "期初茶會的招生文宣要寫什麼", "recruitment", ("document",), ("茶會", "文宣")),
    Case("rec-08", "幫我想三個招生活動的點子", "recruitment", ("document",), ("招生",)),

    # ── 評鑑 ──────────────────────────────────────────────
    Case("eval-01", "幫我做社評報告", "evaluation", ("document", "spreadsheet"), ("評鑑",),
         tools=("create_document",), tags=("必測",)),
    Case("eval-02", "年度績效報告要交哪些附件", "evaluation", ("document", "spreadsheet"), ("評鑑",)),
    Case("eval-03", "幫我整理這學年的活動成果", "evaluation", ("document", "spreadsheet"), ("成果", "評鑑")),
    Case("eval-04", "社團行事曆要怎麼寫給課外組", "evaluation", ("document", "spreadsheet"), ("行事曆", "評鑑")),
    Case("eval-05", "自我評估那一段要寫什麼", "evaluation", ("document", "spreadsheet"), ("評鑑",)),

    # ── 經費 ──────────────────────────────────────────────
    Case("fin-01", "幫我做學年度經費預算表", "finance", ("spreadsheet",), ("預算",),
         tools=("create_spreadsheet",), tags=("必測",)),
    Case("fin-02", "核銷要注意什麼", "finance", ("spreadsheet",), ("核銷",)),
    Case("fin-03", "做一份社費收取紀錄表", "finance", ("spreadsheet",), ("社費", "經費", "財務")),
    Case("fin-04", "挑戰營的預算要怎麼抓", "finance", ("spreadsheet",), ("預算",)),
    Case("fin-05", "幫我做收支明細表", "finance", ("spreadsheet",), ("收支", "財務", "經費")),

    # ── 交接 ──────────────────────────────────────────────
    Case("hand-01", "幫我做幹部交接手冊", "handover", ("document",), ("交接",),
         tools=("create_document",)),
    Case("hand-02", "新任社長要接手哪些事", "handover", ("document",), ("交接", "職")),
    Case("hand-03", "幹部訓練要怎麼設計", "handover", ("document",), ("幹部",)),
    Case("hand-04", "交接要交哪些東西", "handover", ("document",), ("交接",)),

    # ── 表單 ──────────────────────────────────────────────
    Case("form-01", "做一份新生報名表", "google_workspace", ("form",), ("報名", "表單"),
         tools=("create_google_form",), tags=("必測",)),
    Case("form-02", "社課回饋問卷", "google_workspace", ("form",), ("問卷", "回饋")),
    Case("form-03", "幹部意願調查表單", "google_workspace", ("form",), ("表單", "問卷", "幹部")),
    Case("form-04", "幫我做挑戰營報名表", "google_workspace", ("form",), ("報名", "營")),
    Case("form-05", "做一份活動出席統計表", "google_workspace", ("form",), ("統計", "表")),

    # ── 查詢（不該產檔）────────────────────────────────────
    Case("know-01", "我們社團是什麼樣的社團", "knowledge", (), ("社團",)),
    Case("know-02", "青年領袖十二項特質是什麼", "knowledge", (), ("特質",)),
    Case("know-03", "印心禪法是什麼", "knowledge", (), ("印心", "禪")),
    Case("know-04", "新生問我們是不是宗教社團，怎麼回", "knowledge", (), ("宗教", "社團")),
    Case("know-05", "社課通常怎麼進行", "knowledge", (), ("社課",)),
    Case("know-06", "我們社團有哪些活動", "knowledge", (), ("活動",)),

    # ── 當期事實（沒設定時絕不可編）─────────────────────────
    Case("cur-01", "今年社長是誰", "knowledge", (), (),
         must_not_fabricate=("president",), tags=("必測", "grounding")),
    Case("cur-02", "這學期社課是禮拜幾", "knowledge", (), (),
         must_not_fabricate=("regular_meeting_time",), tags=("grounding",)),
    Case("cur-03", "社費多少錢", "knowledge", (), (),
         must_not_fabricate=("club_fee",), tags=("grounding",)),
    Case("cur-04", "報名連結是什麼", "knowledge", (), (),
         must_not_fabricate=("signup_url",), tags=("grounding",)),
    Case("cur-05", "這學期社課在哪間教室", "knowledge", (), (),
         must_not_fabricate=("regular_meeting_location",), tags=("grounding",)),

    # ── 沿用歷年 ──────────────────────────────────────────
    Case("hist-01", "用去年的細流幫我做今年版本", "event_planning", ("document",), ("細流", "流程"),
         must_not_fabricate=("academic_year",), tags=("必測", "grounding")),
    Case("hist-02", "參考 113 學年度的績效報告做一份新的", "evaluation",
         ("document", "spreadsheet"), ("評鑑",), tags=("grounding",)),
    Case("hist-03", "把上次的招生企劃改成這學期的", "recruitment", ("document",), ("招生",),
         tags=("grounding",)),

    # ── 產出落點 ──────────────────────────────────────────
    Case("dest-01", "幫我做社課排程，放到雲端", "event_planning", ("document",), ("社課",),
         tags=("必測",)),

    # ── 研究驗證（政大事故回歸案例）────────────────────────
    # 這批案例除了維度評分外，還宣告事件流斷言（expected_events／forbidden_events）：
    # 反問閘門、研究狀態這些防護若被移除，事件流會缺少對應事件，案例直接判失敗。
    Case("res-01", "幫我比較北科禪心領袖社跟淡江的招生文案", "social_research", (), ("北科",),
         note="點名外校＋比較 → 外校證據池必須含北科段落", tags=("研究驗證",),
         expected_events=("retrieval_started", "source_cards", "research_status"),
         forbidden_events=("clarification_needed", "artifact_ready")),
    Case("res-02", "政大呢", "social_research", (), (),
         note="研究對象不明——必須反問，不得生成外校事實", tags=("研究驗證",),
         # 反問閘門：必須發出反問卡與 needs_clarification 狀態；
         # 閘門明言「不做任何檢索與生成」→ 檢索與產檔事件都不得出現。
         expected_events=("clarification_needed", "research_status=needs_clarification"),
         forbidden_events=("artifact_ready", "retrieval_started")),
    Case("res-03", "幫我做期初茶會企劃書，只用淡江內部資料就好", "event_planning", ("document",), ("茶會",),
         note="明確要求內部模式", tags=("研究驗證",),
         expected_events=("artifact_ready", "research_status=internal"),
         forbidden_events=("clarification_needed",)),
    Case("res-04", "幫我研究台大領袖社的招生方式", "social_research", (), (),
         note="registry 有此社團但無已驗證來源——必須誠實回報查無來源，不得生成外校事實",
         tags=("研究驗證",),
         expected_events=("research_status=no_reliable_source",),
         forbidden_events=("artifact_ready", "clarification_needed")),
]

BY_ID = {c.id: c for c in CASES}
MUST_TEST = [c for c in CASES if "必測" in c.tags]

assert len(CASES) >= 50, f"規格要求至少 50 個情境，目前只有 {len(CASES)}"
