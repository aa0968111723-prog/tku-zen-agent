# tku-zen-agent v2 — Agent Core Upgrade

## 目標

把目前「會查知識庫、會呼叫工具產檔」的代理，升級成可長時間執行社團任務的 Operating Agent。

核心原則：

1. 不破壞現有可用功能：BM25、本機知識庫、NVIDIA Build、xlsx/docx/pptx/gform 產出都要保留。
2. 不靠模型記憶社團事實；今年資料、歷史資料、規範資料要分層。
3. 不把所有能力一次塞成幾十個 tools；用 skill/router 動態縮小工具集合。
4. 代理必須能「規劃 → 查資料 → 執行 → 驗證 → 修正 → 交付」。
5. 不顯示 chain-of-thought；UI 只顯示可理解的執行進度與證據。
6. 優先解決部署安全、session 隔離、RAG 準確度、長任務可靠性，再增加外部工具。

---

## Phase 0 — 安全與 Session 基礎

### 問題

目前 session 只存在 process memory，伺服器重開即消失；session_id 由前端直接提供，部署後缺乏 user/session ownership 與基本隔離。

### 要做

- 新增持久化 Session Store，第一版可用 SQLite。
- 資料模型至少分：
  - users
  - sessions
  - projects
  - messages
  - artifacts
  - working_memory
- session 必須綁定 user/project，不可只靠任意字串 session_id。
- API 層新增最小可用的認證/識別機制；localhost 模式可保留簡化模式。
- artifact 對前端只暴露 artifact_id，不直接暴露伺服器 absolute local_path。
- `/api/download` 改為透過 artifact_id 驗證檔案歸屬後下載。
- `/api/reset` 僅能清除該使用者自己的 session。
- Zeabur / server deployment 不得因多 process 導致 session 消失或彼此不一致。

### 驗收

- server restart 後仍能讀到最近 session/project。
- A 使用者無法讀/下載 B 使用者 artifact。
- 任意猜測 session_id 無法取得別人的對話。
- 本機單人模式仍能一鍵啟動。

---

## Phase 1 — Agent Orchestrator

### 現況

目前流程主要是 LLM → tool → LLM → tool，最多固定輪數。

### 目標流程

`Understand → Plan → Retrieve → Execute → Verify → Repair → Deliver`

### 要做

新增明確的 orchestration state，而不是只靠 messages 推進。

建議狀態：

- `intent`
- `task_type`
- `required_facts`
- `missing_facts`
- `plan_steps`
- `retrieval_queries`
- `selected_skill`
- `artifacts_expected`
- `verification_rules`
- `completion_status`

### 行為要求

- 能合理決定的資訊不要反問。
- 只有缺「會改變結果的必要事實」才詢問使用者。
- 缺今年資料時，優先使用 current term state；沒有才標記待填。
- 工具失敗時先自動修正參數重試，不直接把底層 traceback 丟給使用者。
- 長任務要能知道自己做到哪一步，不因 history trim 遺失。
- 不向 UI 輸出 chain-of-thought。

### UI 事件

新增結構化事件：

- `task_understood`
- `plan_created`
- `retrieval_started`
- `retrieval_result`
- `tool_started`
- `tool_completed`
- `verification_started`
- `verification_result`
- `repair_started`
- `artifact_ready`
- `task_completed`

UI 顯示例：

> 已理解需求 → 找到 3 份歷年茶會企劃 → 套用社團企劃劇本 → 建立文件 → 檢查必要章節 → 完成

---

## Phase 2 — RAG 2.0：Hybrid Retrieval

### 原則

不要刪掉現有 BM25。它對中文專有名詞、活動名稱、歷史文件名稱仍很有價值。

### 新流程

`Query Rewrite → Metadata Filter → BM25 → Local Semantic Search → Rerank → Context Builder`

### 要做

1. 保留現有 BM25/bigram/curated tier/document boost。
2. 新增本機 embedding，可選輕量多語模型，預設不需外部 embedding API。
3. 為知識 chunk 建 metadata：
   - academic_year
   - semester
   - activity
   - document_type
   - audience (internal/external)
   - source_type (curated/playbook/archive/current)
   - department/team
   - contains_sensitive_structure
   - updated_at
4. ingest scripts 補 metadata extraction。
5. query rewrite 將自然語句拆成：主題、文件型別、年份偏好、活動名稱、用途。
6. reranker 需優先：
   - current facts
   - curated knowledge
   - playbooks
   - recent same-type archive examples
7. context builder 必須明確標示 source type 與年份。
8. 不可因歷年文件命中就把舊日期、人名、金額當今年事實。

### 驗收

建立至少 30 組 retrieval regression cases，例如：

- 期初茶會企劃書
- 社課當天流程
- 社評績效報告
- 招生 IG 文案
- 挑戰營細流
- 經費預算

要求：同類歷史範例 + curated/playbook 同時出現在 context；不可讓錯誤歷史文件獨佔 top results。

---

## Phase 3 — Current Semester State

新增獨立的當期真實資料層，不再把會變動的事實混在歷年知識中。

建議檔案：

`data/current_term.yaml`

欄位至少：

- academic_year
- semester
- club_name
- president
- officers
- regular_meeting_time
- regular_meeting_location
- club_fee
- recruitment_period
- signup_url
- primary_drive_folder
- updated_at
- source_note

### 事實優先順序

`Current Term State > Curated Knowledge > Playbook > Historical Archive > General Model Knowledge`

### 要做

- 新增讀取 current term state 的 service/tool。
- 今年資料缺漏時回傳 unknown，不可猜。
- 產出檔案遇到缺漏欄位時自動使用「待填」。
- UI 提供簡單的「本學期設定」頁，不要求使用者編輯 YAML。

---

## Phase 4 — Dynamic Skills / Tool Router

### 目的

未來可擴充 50+ 能力，但每次模型只看到少量相關工具。

### Skill 分組建議

- knowledge
- event_planning
- recruitment
- evaluation
- finance
- documents
- google_workspace
- handover

### Router 行為

使用者輸入先分類 skill，再載入該 skill 需要的 tools/playbooks。

例如「幫我準備期初茶會」只暴露：

- search_knowledge
- search_previous_examples
- create_document
- create_spreadsheet
- create_google_form
- verify_artifact

而不是把所有未來工具一次交給模型。

### 驗收

- 新增 20 個 mock tools 後，現有 5 類任務的 tool selection accuracy 不下降。
- tool schemas 不應在每次 request 都全部送給模型。

---

## Phase 5 — Artifact Verification + Auto Repair

所有產出工具後面都必須經過 verify。

### 文件檢查例

- 是否有必要章節
- 是否有「待填」欄位提示
- 是否誤用歷史日期/姓名/金額
- 是否違反對外語氣規範
- 是否含療效承諾
- 是否錯把宗教招募當主要文宣主軸

### 試算表檢查例

- 必要欄位
- 數值型別
- 公式是否可用
- 日期欄位格式
- 是否有空白必要欄
- 是否有錯誤歷史資料複製

### 流程

`create → inspect → validate → repair → validate → deliver`

最多修正 2 次，仍失敗才交付 warning。

---

## Phase 6 — Working Memory / Project State

不要再只用「最近 40 則對話」保存任務狀態。

新增：

- conversation_history
- working_memory
- project_state
- current_term_state
- artifact_history
- user_preferences

### 規則

- 會改變任務結果的已確認事實寫進 project_state。
- 對話長度超限時，壓縮舊訊息成 summary，但不能丟 project facts。
- 新 artifact 要記錄：類型、版本、來源、產生時間、drive url、本機 artifact id。
- 同一專案後續可「更新上一份企劃」而不是重新猜內容。

---

## Phase 7 — Model Router / Performance

### 要做

- 將 model selection 從純手動改成可自動路由。
- 任務分類/Query Rewrite 用較快模型。
- 複雜規劃/長文用較強模型。
- tool calling 優先選 function calling 最穩定的模型。
- 保留使用者手動 override。
- `httpx.AsyncClient` 改為 reuse connection pool，不要每個 call 都重新建立 client。
- 增加 timeout/retry telemetry。

---

## Phase 8 — Agent Evals

新增 `tests/evals/` 或 `evals/`。

至少建立 50 個真實社團情境。

### 評分維度

- intent accuracy
- skill routing
- tool selection
- retrieval relevance
- grounding
- hallucination
- task completion
- artifact validity
- latency

### 必測案例

1. 「幫我做期初茶會企劃」
2. 「今年社長是誰」但 current state 無資料
3. 「幫我做招生 IG 文案」
4. 「用去年細流幫我做今年版本」
5. 「幫我做社評報告」
6. 「幫我做 18 週社課排程」
7. 「放到雲端」
8. 工具參數故意缺漏
9. 模型吐壞 JSON
10. 歷史文件包含舊日期/姓名/金額

### 關鍵門檻

- current facts hallucination = 0
- 不可因找不到今年資料而偷填歷史資料
- artifact tool execution success rate 要可量測
- regression suite 可在 CI 跑，不依賴付費外部 API 才能完成核心測試

---

## 非本 PR 第一階段範圍

以下先不要一起大改，避免失控：

- Gmail
- Calendar
- 完整 Drive 寫入/同步
- 真正 Google Forms OAuth 寫入
- 多代理 swarm
- 自動發社群貼文
- 大型前端重構

等 Agent Core v2 穩定後再做 Workspace Integration PR。

---

## 預期主要檔案變更

可能包含但不限於：

- `app/agent.py`
- `app/llm.py`
- `app/retrieval.py`
- `app/main.py`
- `app/config.py`
- `app/tools/*`
- `app/services/session_store.py`
- `app/services/project_state.py`
- `app/services/current_term.py`
- `app/orchestrator/*`
- `app/skills/*`
- `app/verification/*`
- `data/current_term.yaml`
- `scripts/ingest_drive.py`
- `tests/*`
- `evals/*`

實作代理應依現有架構調整，不要為了符合這份清單硬建空架構。

---

## 實作順序

請依序完成，不要一次全面重寫：

1. 建 baseline tests/evals，保護現有功能。
2. Session Store + artifact isolation。
3. Current Semester State。
4. Orchestrator state machine。
5. Hybrid Retrieval。
6. Skill Router / dynamic tools。
7. Artifact Verification。
8. Working Memory / project state。
9. Model router + connection pooling。
10. 完整 regression / security / failure tests。

每一階段完成都要跑測試；不可用大量 TODO、placeholder、假實作宣稱完成。

---

## 完成定義（Definition of Done）

PR 實作完成時必須同時符合：

- 現有 5 個工具仍可正常使用。
- 本機 Windows 啟動流程不被破壞。
- Zeabur/server mode 的 session 不再只存在 process memory。
- 今年資料有明確 current state source of truth。
- RAG 能同時給規範與歷史範例，且舊資料不會冒充今年事實。
- 長任務具有可恢復 project state。
- 代理會 verify artifact 並自動修正明顯缺陷。
- 動態 skills 不會讓 tool schema 無限制膨脹。
- 核心 evals 可自動執行。
- README 補上新架構、current term 設定、部署安全注意事項。
- 所有新增設定都要有 `.env.example` / sample config，不得把 secret commit 進 repo。

---

## 給實作代理的工作方式

- 先完整閱讀本 PR、README、`app/agent.py`、`app/llm.py`、`app/retrieval.py`、`app/tools/`、`app/main.py`。
- 先盤點再修改，不要直接重寫整個 repo。
- 優先沿用既有穩定邏輯，特別是 BM25、工具容錯與文件產出。
- 每次改動都要有對應測試。
- 若發現規劃與現況衝突，以「不破壞現有可用功能 + 可測試 + 可部署」為最高原則調整。
- 不要只寫規劃文件；實作任務必須真正修改程式、測試、README。
- 不要偷偷擴張到 Workspace Integration 或前端大重構。
