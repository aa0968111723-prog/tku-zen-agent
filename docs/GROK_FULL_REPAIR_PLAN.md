# Grok 長時間完整修補計畫

本計畫是給長時間 Terminal 代理執行的工程工作說明，目標是把既有
`tku-zen-agent` 的視覺資料庫、Intelligence Library、InsForge 資料層、外部聯網
工具與所有可發現的功能缺陷，整理成可審查、可回滾、可續跑的多階段交付。

## 原則與範圍

- 保留現有 FastAPI、SQLite source-of-truth、SessionStore、VisualAssetStore、
  Intelligence Library、聊天、知識庫、Artifact、社群文案與產檔流程。
- 先審計再修改；所有變更採 additive migration、向後相容與小步驟 commit。
- 不覆蓋、刪除或搬移原始圖片、影片、文件與 Artifact；大量 binary 不進資料庫。
- 本機資料仍可在 InsForge 不可用時正常運作；InsForge 是可替換的視覺資料層與
  同步／搜尋試點，不是一次性後端重寫。
- 所有 AI、外部聯網與遠端同步結果都必須有來源、confidence、status 與可回看的
  evidence；沒有證據不得升級為 verified。
- 禁止 hard reset、force push、提交 secrets、提交 SQLite／素材 binary，以及假造
  PASS、OCR、Vision、embedding 或遠端同步成功。

## 階段 0：可續跑的審計與 checkpoint

建立 `docs/GROK_REPAIR_STATUS.md`、`docs/GROK_REPAIR_STATUS.json`，紀錄目前 phase、
checkpoint、命令、commit、測試、blocker 與下一步。每個小階段完成就更新；程序重啟時
先讀 checkpoint，不從頭掃描或重做已完成工作。

審計 Git、PR、分支、migration、schema、API、UI、測試、環境變數、Dockerfile、
Zeabur 設定、worker、queue、Intelligence Library、檔案儲存、去重、ACL、review queue、
Entity Graph 與現有所有資料來源。確認 PR #20、#21、#22、#23 實際狀態，不假設已合併。

產出 `docs/GROK_CURRENT_ARCHITECTURE_AUDIT.md`，列出已存在、部分存在、只有 UI、
只有 mock、外部阻擋與真正需要修補的功能。

## 階段 1：完整資料與缺陷盤點

實際掃描 repo 內 `data`、`output`、`outputs`、`mobile-shots`、`knowledge`，以及存在時的
工作區同層內部素材樹：

- `淡大劇本`
- `淡大所有照片`
- `05_Photos`
- `06_Video`
- `招生影片`
- `音樂會`
- `挑戰營`
- `場務場刊E310`
- `聖者影片`
- `zen-photo-inbox`

避免掃描 `.git`、`.venv`、`node_modules`、`__pycache__`、SQLite、暫存鎖定檔、憑證與
generated thumbnail/rendition。父層 root 與 canonical root 不得重複計算。

產出真實統計與缺陷清單：圖片、影片、文件、Artifact、知識來源、社團、學校、人物、
活動、日期、duplicate、缺少 source、pending、failed、未註冊檔、unsupported format、
external path、managed binary、每個 root／mime／extension／source 的數量。

特別驗證 `淡大劇本/場景` 的既有分類，如上／下學期社課、期初茶會、演講、場佈、校園、
戶外等。分類只能建立 `probable`／`pending_review` evidence，不得宣稱人物身分或正式
活動事實。

全面搜尋功能缺陷，不限視覺資料庫，包括：

- API 500、錯誤 HTTP status、輸入驗證缺口、錯誤處理遺失上下文。
- reload 後狀態遺失、worker 中斷後假裝完成、queue 卡住、重試重複寫入。
- 搜尋跨 user／跨 project／跨學校洩漏、ACL 順序錯誤、private file 暴露。
- path traversal、symlink、任意檔案讀取、過大檔案、zip bomb、NUL、Unicode 與編碼問題。
- concurrency、SQLite lock、transaction 過長、duplicate lineage、review queue 無限增長。
- 前端靜態假資料、API 欄位不一致、手機 layout、錯誤狀態遺失、鍵盤與貼上上傳失效。
- 聊天、知識庫、Artifact、產檔與既有 RAG 功能的回歸。

## 階段 2：資料庫與 additive migration

先閱讀所有 local／InsForge migration，禁止建立同語意重複表。若缺欄位或 index，建立
下一個版本 migration（例如 `0006_grok_repair_hardening.sql` 或依現有版本命名）。

migration 必須：

1. 可在空 DB 與既有 DB 執行。
2. 可重複執行，使用安全的存在性檢查。
3. 不刪除或覆蓋原始資料。
4. 有 index、foreign key、status constraint 與 owner/project scope。
5. migration 失敗時不靜默忽略。
6. reload 後 schema ledger 與資料一致。

需要核對並延伸：`visual_assets`、`visual_observations`、`visual_date_candidates`、
`visual_people`、`visual_person_references`、`visual_schools`、`visual_clubs`、
`visual_events`、`visual_analysis_jobs`、`visual_imports`、`visual_import_items`、
`sources`、`entities`、`asset_entities`、`events`、`review_queue`、`embeddings`、
`sync_runs`、`data_lineage`、`entity_relationships`、`project_context_nodes`、
`project_asset_links`、`backend_sync_items`、`backend_resource_refs` 與
`backend_user_mappings`。

狀態統一為：`verified`、`probable`、`pending_review`、`conflicted`、`failed`。

## 階段 3：視覺素材匯入與資料整理

完成單檔、批次、資料夾、手機、多媒體匯入、manifest、相對路徑、SHA-256 去重、
source key、idempotency、續傳、部分成功、單檔 retry、重新同步、rollback reference。

支援圖片、影片與文件，但只把 metadata、thumbnail、embedding、lineage 放進資料庫；
external root 保留唯讀原始路徑，managed thumbnail/rendition 留在安全資料夾。內容變更
建立新 asset 與 `supersedes_asset_id`，不覆蓋舊 asset。

每個 import、sync、analysis、retry 與 rollback 都必須有 server truth、owner scope、
manifest、checkpoint、錯誤碼、attempts、時間、部分成功狀態與 append-only audit。

## 階段 4：目錄分類與 Entity Resolution

將既有人工分類轉成可查詢但可審核的 observation。分類證據至少包含 root、relative path、
taxonomy version、source、confidence；不包含不必要的絕對路徑。

正確建立 asset→person／scene／event／club／school、event→date／place、club→school、
document→event／club、person→club／event 關聯。不同學校、同名人物、同名社團與相似
活動必須分開。無 source 的資料不得是 verified。

## 階段 5：圖片／影片／文件分析

先用本機 Pillow、EXIF、OCR parser、感知雜湊、品質與比例分析；再透過既有 Vision／OCR
adapter 執行外部分析。未設定 provider 時，保留可用的本機分析並明確回傳
`BLOCKED_BY_EXTERNAL_DEPENDENCY`，不可填入假文字或假人物。

人物只可顯示未知／可能人物與影像特徵；只有已 verified 的人物 reference 才能比對，
仍須使用者確認才能建立可靠關聯。日期候選並存，衝突設為 `conflicted` 並進 review queue。

影片沒有 decoder 時不可虛構影格、尺寸、人物或場景；若可產生 thumbnail，記錄產生器與
版本，否則保留明確 blocker。

## 階段 6：InsForge 深度整合

InsForge 定位為「視覺資料庫與 AI 後端資料層」，不是取代現有本機後端。先使用 InsForge
MCP 的官方 `fetch-docs` 讀取最新文件；不可在輸出、Git、前端或 status log 顯示 API key。

確認並擴充 server-only：

- `InsForgeDatabaseAdapter`：CRUD、upsert、transaction／RPC、timeout、retry、backoff、
  circuit breaker、錯誤分類與 request correlation id。
- `InsForgeStorageAdapter`：private bucket、安全 object key、checksum、分段／續傳、
  HEAD、presigned URL；絕不把本機絕對路徑當 object key。
- `InsForgeSearchAdapter`：PostgreSQL keyword、pgvector text/image embedding、hybrid
  ranking、ACL-first RPC、current version、confidence 與 evidence。
- `InsForgeFunctionAdapter`：OCR、Vision、embedding、影片處理、非同步 job、callback／
  polling、成本、模型版本、延遲、失敗與 blocker。
- `InsForgeSyncAdapter`：metadata／thumbnail／approved-only／full（明確同意）模式、
  manifest、outbox、checkpoint、retry、dead-letter、remote reference、rollback。

所有前端不得載入 InsForge SDK；前端只呼叫現有 FastAPI API。若 adapter 已存在，使用
既有介面與資料表，不重建第二套。

### InsForge migration 與 mapping

先檢查現有 `migrations/insforge/001_*`、`002_*`、`003_*` 與遠端實際狀態。若需要，建立
下一個 additive migration（例如 `004_insforge_deep_visual_sync.sql`），包含或對應：

1. `visual_assets`
2. `entities`
3. `asset_entities`
4. `events`
5. `sources`
6. `review_queue`
7. `embeddings`
8. `sync_runs`
9. remote reference／version／etag／sync state
10. outbox／checkpoint／dead-letter
11. owner／project scope
12. RLS 與必要 index
13. pgvector extension（若平台文件與權限允許）
14. ACL-first hybrid search RPC

如果本機已有 `visual_asset_backend_refs`、`backend_sync_items`、`sync_runs` 或類似表，
直接擴充 mapping，不建立 duplicate table。

### Source of truth 與同步策略

- 本機 SQLite 是可離線工作的 operation source-of-truth。
- InsForge 保存視覺 metadata、approved evidence、embedding、可控縮圖與搜尋投影。
- private binary 預設不送遠端；只有明確設定與使用者同意才可傳。
- private asset 的本機絕對路徑不得送到 InsForge。
- 同步必須能 metadata-only、thumbnail-only、approved-only、full explicit consent。
- 本地與遠端衝突不直接覆蓋；建立 conflicted sync item 與人工審核。
- 遠端失效時本機功能繼續，狀態是 blocker，不是假成功。
- 遠端恢復後只 retry 未完成 checkpoint，不能整批重傳。

### InsForge 安全

驗證 service key 只在 server secret，owner mapping 明確，RLS 不依賴前端欄位，Storage 不
公開，presigned URL 有期限與權限，search RPC 先 ACL 再 embedding。檢查錯誤 log、例外、
debug mode、health endpoint、metrics 與 audit 是否會洩漏 token、path、prompt 或 private
影像。

## 階段 7：外部聯網與工具能力

審計並修補所有需要外部網路的功能：InsForge、Vision、OCR、embedding provider、文件／
圖片下載、搜尋工具、AI function、callback、Webhook 與 Zeabur outbound network。

每一個外部服務都要有：

1. 明確 provider config 與 server-only secret。
2. connect timeout、read timeout、總期限與取消。
3. retry/backoff 與最大 attempts。
4. rate limit 與 concurrency limit。
5. circuit breaker。
6. 可觀測的 request id、latency、status、cost 與錯誤分類。
7. NUL／Unicode／payload size／content type 驗證。
8. SSRF 防護：禁止任意 user-supplied URL 直接抓取內網、metadata endpoint 或本機檔案。
9. redirect、DNS rebinding、TLS、host allowlist 與下載大小上限。
10. private asset 未同意不得外傳。
11. 外部不可用時 fallback 到本機能力或明確 blocker。
12. 不得把外部結果直接升級為 verified。

確認 AI 可以在現有 agent tool registry 呼叫唯讀的 visual search、context resolver、
資料庫查詢與受控的分析 job；寫入、確認、修正、刪除、上傳與外部傳輸必須有權限與 audit。

## 階段 8：搜尋與 context resolver

搜尋同時支援 keyword、OCR、relative path、directory taxonomy、text embedding、image
embedding、感知雜湊、日期、人物、活動、社團、學校、場景、比例、畫質、商用權限與 privacy。

所有搜尋必須 ACL-first，結果包含縮圖、匹配原因、日期、人物、活動、場景、來源、confidence、
verification status、evidence、duplicate 與加入素材包能力。

Library→Project→Story→Scene→Shot→Output 的解析必須先確認 project ownership，再搜尋。
例如「找適合淡江招生影片第二幕的照片」要能解析淡江、招生影片、第二幕、校園、明亮、16:9、
可使用與可信來源，且不可看到別的 user/project。

## 階段 9：UI、API、回歸修補

確認 UI 所有資料來自 API，不得使用靜態假資料。補齊最近上傳、分析中、待確認、衝突、失敗、
重複、搜尋、篩選、evidence、確認／修正／忽略、批次確認、素材包、下載、同步進度與 blocker。
手機版測試拍照、相簿、多張、拖曳、貼上、滑動與自然語言搜尋。

所有 API 檢查 auth、owner、project、input、page／limit、file size、idempotency、CSRF、
path traversal、private storage、錯誤碼與 audit。不得為了修一條路徑破壞聊天、知識庫、
Artifact、社群文案或產檔。

## 階段 10：測試、部署與 PR

建立真實 SQLite／檔案 fixture 測試，外部服務以 contract test 與可控 fake server 測試；
未設定的真實外部服務只能標為 `BLOCKED_BY_EXTERNAL_DEPENDENCY`，不得冒充 PASS。

至少執行：

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m compileall -q app tests
.venv\Scripts\python.exe -m evals
```

另外執行現有 lint/type check、migration dry-run、InsForge adapter contract、網路失效、
retry、reload、concurrent edit、ACL、SSRF、手機 UI 與 Zeabur health check 測試。

驗證 Zeabur 的 DB、visual asset、export、external root mount 與 secret；容器重啟不得遺失
資料，InsForge 或外部模型中斷不得讓服務啟動失敗。

PR 必須包含：審計、修改計畫、migration、adapter、API、資料統計、測試、blocker、風險、
rollback 與未完成項目。不可自動 merge、不可 force push、不可提交 private binary 或 secret。
