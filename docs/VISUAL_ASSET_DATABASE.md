# 視覺資料庫 Phase 1：圖片／影片／文件素材庫

本功能是既有 FastAPI、SQLite、cookie 授權與 fal.ai 圖片理解的增量擴充。聊天、
知識庫、活動管理與產檔工具沒有被替換；新頁面位於 `/visual-assets`。

## 資料與信任模型

原圖寫入 `VISUAL_ASSET_DIR/<user_id>/<asset_id>/original.<ext>` 後不再覆寫。
縮圖與 1:1、4:5、9:16、16:9 是可重建的非破壞式衍生檔。系統沒有刪除原圖的
API，PATCH 也拒絕 storage path、雜湊、尺寸、上傳者等不可變欄位。

AI 結果不直接當成事實。每個 observation 都有：

- `source`：EXIF、file metadata、OCR、fal vision 或 user confirmation
- `confidence`：0–1
- `status`：`verified`、`probable`、`pending_review`、`conflicted`、`failed`
- `evidence`：可回看圖片的文字依據或使用者修正原因

EXIF、檔案日期、OCR、活動日期與使用者輸入分別存在
`visual_date_candidates`。值不同時 API 回傳 `date_status=conflicted`，不選一個
覆蓋其他候選。

學校是社團與人物的上層實體。社團以 `(school_id, name)` 唯一；人物姓名不做
唯一限制，因此同名者會得到不同 ID。把已知人物或活動連到不同學校的圖片時，
API 會回 `school_conflict`，不建立關聯。

人物流程特別保守：

1. 一般視覺分析只計數、描述匿名人物，不允許模型回傳姓名。
2. 只有 `visual_people.status=verified` 且有人工確認參考圖的人物能進入比對。
3. 比對結果永遠以 `probable`／「可能是」保存。
4. 使用者按確認後才成為可靠關聯；單次錯誤不會改模型權重。

## SQLite 資料表

| 資料表 | 用途 |
|---|---|
| `visual_schools` | 學校與別名，隔離跨校資料 |
| `visual_clubs` | 社團、Logo 資產、社群、網站、別名與來源 |
| `visual_people` | 人物、暱稱、學校、社團、角色、狀態與信心 |
| `visual_person_references` | 已人工確認的人物參考照片 |
| `visual_scenes` | 場景分類與自訂場景 |
| `visual_events` | 活動名稱、類型、社團、學校、日期、地點、文案與證據 |
| `visual_assets` | 原圖／縮圖路徑、格式、尺寸、EXIF、雜湊、embedding、權限、品質與分析 |
| `visual_observations` | 圖片與人物／場景／活動／社團／Logo 的有證據關聯 |
| `visual_date_candidates` | 並列的日期來源與衝突 |
| `visual_analysis_jobs` | 分析階段、進度與安全錯誤碼 |
| `visual_learning_events` | 搜尋、推薦、選用、排除、下載、工具成敗與用途 |
| `visual_corrections` | 使用者修正、原值、新值、原因與管理審核 |
| `visual_collections` | 素材包、影片分鏡、社群貼文與海報素材集合 |
| `visual_collection_items` | 集合內圖片順序、說明與建議比例 |
| `visual_schema_migrations` | 已套用 migration 版本；部署升級不重建資料庫 |
| `visual_imports` | 使用者、idempotency key、manifest、總進度與最後同步時間 |
| `visual_import_items` | 相對路徑、單檔狀態、雜湊、錯誤碼、嘗試次數與資產版本 |

Migration 依序為 `0001_initial_visual_schema` 與
`0002_phase1_media_import`。第二段會保留既有資料並把舊審核字彙轉成統一狀態；
服務重啟時未完成的 running job 會標示 `interrupted_process`，不會在 reload 後假裝完成。

## 匯入與重新同步

直接上傳及資料夾匯入都支援 client idempotency key。資料夾 API 接受 manifest 的
`client_key`、`relative_path`、`last_modified`；伺服器持久化每個項目的成功、失敗、
錯誤碼與 attempts。同一 key／同一雜湊會安全略過，失敗項目可單獨重送。內容變更時
必須明確指定 `resync=true`，系統建立新 asset 並填入 `supersedes_asset_id`，不覆寫
舊檔。直接上傳的 binary 只寫入 `VISUAL_ASSET_DIR`；本機外部資料樹則保留原始
`storage_path`、只在 `VISUAL_ASSET_DIR` 寫入縮圖與衍生圖，SQLite 僅保存路徑、雜湊
與結構化資料，因此不會為 29GB 級資料再複製一份。

圖片（JPEG/PNG/WebP/GIF/AVIF/JFIF）由 Pillow 實際解碼；PDF、DOCX、PPTX 與文字文件使用本機 parser 擷取文字並
建立縮圖。CR2/ARW/PSD/SVG 等 RAW／設計檔、MP4/MOV/WebM/M4V/AVI/MKV/MTS 影片、
MP3/WAV/M4A/FLAC/OGG/AAC/WMA 音訊，以及 ZIP/WFP/WFPBUNDLE/BDM/CPI/MPL 剪輯／封裝檔
也會被目錄化；它們保留外部原始路徑並使用安全占位縮圖，不把大型 binary 複製進 SQLite。
目前部署未附影片影格或 RAW 解碼器，因此只會明確回報
`BLOCKED_BY_EXTERNAL_DEPENDENCY`／generic inspection，不假造尺寸、OCR 或標籤。

## 圖片分析

上傳時一定先完成離線分析：Pillow 解碼驗證、EXIF 日期、SHA-256、感知雜湊、
色彩向量、縮圖、模糊／清晰度、亮度、解析度與海報／影片／IG 首圖適用性。
完全相同的 SHA-256 會標示 `duplicate_of`，但新上傳原圖仍保留，不會偷偷刪除。

設定 `FAL_KEY` 後，背景流程再呼叫現有 fal.ai Vision：

- 繁體中文 OCR、海報文字、活動、日期、時間、地點、人名文字、社團與 Logo 文字
- 場景、活動類型、物件、Logo、舞台、佈置、文件／海報
- 已確認人物參考圖的受控候選比對

未設定視覺服務時本機品質與去重仍可用，狀態顯示 `needs_vision_config`，不會假裝
OCR 已完成。

## 搜尋與輸出

`GET /api/visual-assets/search` 同時使用關鍵字、AI/OCR 標籤、實體關聯、本機
語意向量、畫質與使用者選用／排除回饋排序。關鍵字與向量排名沿用既有
Intelligence Library Hybrid RAG 的 Reciprocal Rank Fusion。自然語言中明確出現的既知場景會
成為有證據的條件，沒有場景 observation 的圖片不會冒充命中。另支援學校、
社團、人物、場景、活動、日期、比例、畫質、商用權限、隱私與重複圖篩選。

`POST /api/visual-assets/search-by-image` 使用感知雜湊與色彩向量找相似構圖；
查詢圖片只在請求期間解碼，不會自動加入資料庫。

單圖端點可下載原圖、縮圖及 1:1、4:5、9:16、16:9 衍生圖。素材包匯出為 ZIP，
內含選定圖片、`manifest.json` 與 UTF-8 BOM `manifest.csv`。加入分鏡、貼文、
海報或素材包會寫 learning event，供排序與策略洞察使用。

## API

所有端點都套用既有 cookie 授權、全站限流、CSRF Origin 檢查、安全標頭與
append-only audit；列表端點有 page/limit 並受 `VISUAL_SEARCH_LIMIT` 限制。

| Method | Path | 說明 |
|---|---|---|
| POST | `/api/visual-assets/upload` | 多檔上傳、本機分析、排程 AI 分析 |
| POST | `/api/visual-assets/import` | manifest 資料夾匯入、續傳、部分失敗與重新同步 |
| POST | `/api/visual-assets/analyze` | 重試／明確執行 AI 分析 |
| POST | `/api/visual-assets/{id}/analyze` | 單一素材分析；外部不可用回明確 blocker |
| POST | `/api/visual-assets/{id}/retry` | 重試失敗分析並累計 attempts |
| POST | `/api/visual-assets/{id}/confirm` | 一鍵確認素材；日期衝突時拒絕並要求修正 |
| GET | `/api/visual-assets/review-queue` | 目前使用者的待審核／衝突／失敗佇列 |
| GET | `/api/visual-imports/{id}` | 伺服器端匯入進度與逐檔結果 |
| GET | `/api/visual-assets/dashboard` | 首頁統計與最近上傳 |
| GET | `/api/visual-assets/search` | 自然語言與結構化搜尋 |
| POST | `/api/visual-assets/search-by-image` | 以圖搜圖 |
| GET | `/api/visual-assets/{id}` | 圖片、證據、日期候選與分析工作 |
| PATCH | `/api/visual-assets/{id}` | 僅修改權限、來源、學校／社團與審核狀態 |
| GET | `/api/visual-assets/{id}/file` | 原圖、縮圖與比例衍生圖 |
| POST | `/api/visual-assets/{id}/usage` | 選用、排除、分鏡、貼文、下載紀錄 |
| POST | `/api/visual-assets/export` | 素材包 ZIP＋JSON＋CSV |
| GET | `/api/visual-collections` | 目前使用者的素材包／分鏡／貼文／海報集合 |
| GET | `/api/visual-collections/{id}` | 集合與依序排列的圖片 |
| POST | `/api/visual-collections/{id}/assets` | 將圖片加入既有集合 |
| POST | `/api/entities/confirm` | 確認、修正或忽略 observation |
| GET | `/api/people` | 人物列表 |
| GET | `/api/clubs` | 社團列表 |
| GET | `/api/events` | 活動列表 |
| GET | `/api/scenes` | 場景列表 |
| GET | `/api/learning/insights` | 搜尋／工具／修正洞察 |
| GET | `/api/learning/corrections` | 修正紀錄 |
| POST | `/api/learning/approve` | 管理者審核修正紀錄 |
| GET | `/api/visual-backend/status` | InsForge 設定／可用性；不回傳金鑰 |
| POST | `/api/visual-sync/run` | 以 asset id 批次同步 metadata，逐檔部分成功與 idempotency |
| GET | `/api/visual-sync/{id}` | 查看同步 manifest、逐檔狀態與錯誤 |
| POST | `/api/visual-sync/{id}/retry` | 只重試未完成項目，建立新的 resumed run |
| POST | `/api/visual-sync/{id}/rollback` | 非破壞性本地回滾參照，不刪原圖或遠端資料 |
| POST | `/api/backend-sync/run` | 專案、Artifact、活動、研究來源、知識、視覺與 organization mapping dry-run／同步 |
| GET | `/api/backend-sync/{id}` | 通用同步 manifest、逐項狀態與錯誤 |
| POST | `/api/backend-sync/{id}/retry` | 只續傳未完成的 resource |
| POST | `/api/backend-sync/{id}/rollback` | 非破壞性回滾本地 backend reference |
| GET/POST | `/api/data-organization/inventory` | 真實資料盤點；可指定 project 並保存報告 |
| POST | `/api/data-organization/organize` | 可重跑、部分成功的現有資料 mapping |
| GET | `/api/data-organization/runs/{id}` | 查看 manifest、逐筆狀態與錯誤 |
| POST | `/api/data-organization/runs/{id}/retry` | 只重試失敗 mapping |
| GET | `/api/data-organization/queue` | 資料整理中心分頁佇列 |
| POST | `/api/library/context-nodes` | 建立 Story／Scene／Shot／Output context |
| POST | `/api/library/context-search` | ACL-first 任務脈絡素材搜尋 |

## InsForge adapter 與 migration

遠端呼叫集中於 server-only `app/services/insforge_adapters.py`：
`InsForgeDatabaseAdapter`（PostgREST CRUD/RPC）、`InsForgeStorageAdapter`
（upload-strategy）、`InsForgeSearchAdapter`（hybrid-search RPC）與
`InsForgeFunctionAdapter`（Edge Function）。`InsForgeSyncAdapter` 負責把本地
source-of-truth 組成 visual_assets/entities/asset_entities/embeddings payload，並以
`sync_runs`、`sync_run_items`、`visual_asset_backend_refs` 保存可續傳狀態。前端不載入
InsForge SDK，也不會取得 service key。

本地啟動會套用 `0003_insforge_visual_backend`，建立 `sources`、`entities`、
`asset_entities`、`events`、`review_queue`、`embeddings`、`sync_runs` 等相容表；
PostgreSQL/pgvector 的正式 DDL 在 `migrations/insforge/001_visual_backend.sql`，
必須由 InsForge migration runner 套用後才可啟用遠端搜尋。未設定
`INSFORGE_BASE_URL`、server key、`INSFORGE_OWNER_ID` 或未明確設
`INSFORGE_TRUSTED=true` 時，sync 會保留本地資料並回傳
`BLOCKED_BY_EXTERNAL_DEPENDENCY`。private 素材還需要
`INSFORGE_ALLOW_PRIVATE_SYNC=true` 才允許送出 binary。

核心資料同步使用 `0004_insforge_core_data_sync` 的 `backend_sync_items`、
`backend_resource_refs` 與 `backend_user_mappings` 保存 checkpoint。遠端核心 schema 在
`migrations/insforge/002_core_data_layer.sql`，包含 projects、artifacts、activities、
activity_tasks、research_sources、knowledge_documents、knowledge_chunks 與
`knowledge_hybrid_search` RPC。同步 manifest 永遠排除 sessions、messages、
working_memory、retrieval_cache、audit IP 與 credentials，也不回傳 local_path。

`0005_data_organization_depth` 以 mapping 方式新增 inventory、lineage、Entity Graph 與
Library → Project → Scene → Shot context，沒有重建既有 people/club/school domain tables。
對應遠端 migration 是 `migrations/insforge/003_data_organization_depth.sql`。知識文件 ID
與 current-version 判定包含 project scope，避免不同專案同路徑互相覆蓋；所有 sync
idempotency key 也包含 sync source，避免 visual/core/organization 互撞。

知識文件採相對路徑＋SHA-256 版本化；原始 Markdown 存 private Storage，chunks 批次
upsert。PostgreSQL 不接受的 NUL 控制字元只在遠端 payload 邊界清除，本機原檔不修改。

## 部署設定

```env
DB_PATH=/persistent/agent.sqlite3
VISUAL_ASSET_DIR=/persistent/visual-assets
VISUAL_EXPORT_DIR=/persistent/visual-exports
VISUAL_MAX_FILE_BYTES=20000000
VISUAL_MAX_BATCH=30
VISUAL_SEARCH_LIMIT=60
INSFORGE_BASE_URL=https://your-project.insforge.app
INSFORGE_SERVICE_KEY=（只放伺服器 secret）
INSFORGE_OWNER_ID=（與 InsForge Auth user id 的明確映射）
INSFORGE_TRUSTED=false
INSFORGE_ALLOW_PRIVATE_SYNC=false
INSFORGE_ALLOW_ARTIFACT_FILE_SYNC=false
INSFORGE_ALLOW_KNOWLEDGE_SYNC=false
INSFORGE_ALLOW_CONVERSATION_KNOWLEDGE_SYNC=false
INSFORGE_SYNC_LIMIT=5000
INSFORGE_SYNC_WORKERS=6
FAL_KEY=...
```

Zeabur 必須把 SQLite、原圖與衍生圖放在持久化磁碟；只設 DB_PATH 而沒有持久化
`VISUAL_ASSET_DIR` 會留下資料列但在容器重啟後失去檔案。備份時兩者要一起備份。

本機資料整理可用 `DATA_ORGANIZATION_IMPORT_ROOTS` 擴大素材白名單，預設為
`data,output,outputs,mobile-shots`；若工作區同層存在 `淡大劇本`、`淡大所有照片`、
`05_Photos`、`06_Video`、`招生影片` 等內部素材樹，也會自動納入。服務只讀取支援的
圖片（含 CR2/ARW/PSD/SVG）、影片、音訊與文件／專案副檔名，並排除
SQLite、playwright 暫存、`.git`、`.venv`、程式碼與憑證；相對路徑和 SHA-256 會保存於
manifest/lineage，原始檔不會被搬移或覆蓋。已匯入檔案會以 `root:relative_path` 與
SHA-256 做雙重 idempotency 判定；`data/visual-assets` 內的 thumbnail/rendition 只作
衍生檔 lineage，不會被重新當成獨立素材。

目前 embedding 是不需外部金鑰的可重現文字特徵、色彩向量與感知雜湊，適合社團
規模的 SQLite 內排序。未來可在保留相同欄位與來源證據的前提下，換成專用向量
服務；不得用未審核修正直接訓練或改模型權重。

## 驗證

`tests/test_visual_asset_database.py`、`tests/test_visual_asset_phase1.py` 與
`tests/test_data_organization_depth.py` 涵蓋上傳、品質、原圖不變、重複圖、海報 OCR、
日期衝突、活動／場景、跨校隔離、同名人物、自然語言搜尋、以圖搜圖、修正、
學習紀錄、素材包、不可變欄位、未登入、跨使用者隔離、超大與損壞圖片、manifest、
部分失敗、續傳、idempotency、重新同步、XLSX/Apps Script 文件擷取、owner-scoped
Entity mapping、ACL-first scene/shot context、外部阻擋標記與手機 UI 契約。
成功路徑的 Vision 使用 deterministic fake，CI 不會傳送私人圖片或消耗額度；
真實外部模型若未配置，一律列為 `BLOCKED_BY_EXTERNAL_DEPENDENCY`，不列 PASS。
