# tku-zen-agent 現況資料盤點與 InsForge 評估

盤點日期：2026-08-26  
分支：`feat/visual-asset-intelligence`  
基準 commit：`eac7e2e feat: 完成視覺素材庫 Phase 1`

## 結論摘要

目前系統已經有可工作的本地視覺素材庫 Phase 1，不是空白專案。資料來源真實儲存在 SQLite 與伺服器檔案系統：素材原檔不進資料庫，資料庫只保存路徑、雜湊、分析結果、審核狀態與關聯。現有聊天、知識庫、Artifact 與社群產出流程仍以本地後端為主。

InsForge 適合作為「視覺資料庫與 AI 後端資料層」的試點：它提供 PostgreSQL／PostgREST、S3 相容儲存、Edge Functions、AI gateway 與 JavaScript SDK。但本專案目前沒有 InsForge URL、金鑰、Postgres、pgvector 或 InsForge Auth；本地使用者 ID 也不是遠端 UUID。因此本階段採取本地 source-of-truth + 可替換 adapter + 可續傳同步，不能直接把既有後端切換成 InsForge。外部端點未設定或不可用時，所有結果明確標記 `BLOCKED_BY_EXTERNAL_DEPENDENCY`，不假造成功、不刪除本地資料。

## 現有架構盤點

| 領域 | 實際現況 | 判定 |
|---|---|---|
| Web/API | FastAPI `app/main.py`，路由分散於 `app/visual_api.py` 等 router；前端為原生 HTML/JS/CSS，不是 React/Next。 | 保留，新增整合只放在後端邊界 |
| 主資料庫 | `app/services/session_store.py` 使用 `config.DB_PATH` SQLite；含 users、projects、sessions、messages、artifacts、knowledge/research、activities、audit logs。 | 仍為聊天與工作流程 source-of-truth |
| 視覺資料庫 | `app/services/visual_assets.py` 建立 visual_assets、analysis jobs、observations、date candidates、people/clubs/events/scenes、collections、learning/corrections 等表。 | 已存在，擴充而非重建 |
| 原始檔案 | `VISUAL_ASSET_DIR/<user>/<asset>/original.ext`；thumbnail/renditions 另存。大量 binary 不塞入 SQLite。 | 符合檔案與資料分離原則 |
| 去重 | SHA-256 與 perceptual hash；重複素材保留原始檔及關聯。 | 可供同步 idempotency 使用 |
| 分析 | `app/services/visual_analysis.py`；本地 Pillow/文件 parser，Fal Vision/OCR 為外部依賴；分析 job 有持久狀態。 | 無 Fal 時會失敗並保留狀態 |
| 搜尋/embedding | `app/rag/hybrid.py`：BM25 + semantic LSA + RRF；視覺素材目前有本地 deterministic text/color embedding 與混合排序。 | 尚非 PostgreSQL/pgvector |
| 任務佇列 | FastAPI BackgroundTasks + visual_analysis_jobs；沒有獨立 worker/Redis。 | reload 後 job 資料仍在 SQLite，但執行需重試掃描 |
| 權限 | `app/services/auth.py` 本地 local/token 模式；`permissions.py` 有 view/manage/approve/publish/spend；視覺 router 以登入與 user ownership 隔離。 | 沒有 InsForge Auth/RLS；adapter 必須 server-only |
| 知識庫 | `app/retrieval.py` 讀取打包在 image 的 markdown，建立 token/LSA 索引。 | 與視覺素材分開，不能因同步改寫 |
| 文件與 Artifact | `app/tools/artifact.py` 與文件/簡報/試算表/社群工具建立檔案及 metadata，SessionStore 儲存 artifact lineage/path。 | 尚未與 visual_assets 自動建關係 |
| 部署 | Dockerfile + Uvicorn，README/Zeabur 設定 `DB_PATH`、`VISUAL_ASSET_DIR`、`VISUAL_EXPORT_DIR` persistent disk。沒有獨立 worker 或 `.openai/hosting.json`。 | InsForge 可減少視覺 metadata 依賴本地 disk，但仍需設定持久化路徑 |
| 外部後端 | repo 搜尋沒有 InsForge、Supabase、Postgres、pgvector 或 SDK 依賴。 | 本階段為新增 adapter，不是假設已存在 |

## 已找到的既有功能

- 單張/批次/資料夾素材匯入，manifest、相對路徑、續傳、部分失敗與單檔重試。
- 圖片、影片、PDF/Office/文字文件 metadata；原始檔、縮圖、尺寸、比例、SHA-256、EXIF 候選日期。
- OCR、場景/活動/社團/學校標籤、人物數量與「可能人物」觀測；未確認人物不會直接宣稱姓名。
- `verified`、`probable`、`pending_review`、`conflicted`、`failed` 狀態與 evidence/review queue。
- 關鍵字 + 語意搜尋、以圖搜圖、日期/人物/活動/社團/場景/畫質/比例等篩選；RRF 排序。
- 重複圖片警告、審核、批次確認、素材包、JSON/CSV 匯出及社群比例輸出。
- 原有聊天、知識庫檢索、文件/Artifact 產出與 session/project ownership。

## InsForge 適配性與風險

適合的部分：遠端 PostgreSQL 可承接結構化視覺 metadata、RLS 可承接 owner/project ACL、S3 相容 Storage 適合原檔與縮圖、Functions 可承接 OCR/embedding pipeline、PostgREST/RPC 可提供關鍵字與 pgvector 搜尋。

目前阻礙：尚未提供 InsForge project URL/API key；無法在本地驗證實際 storage upload endpoint、RLS claim 與 pgvector extension；本地 auth/user ID 與遠端 auth 不相容；Zeabur 仍需保留本地 disk 以支援 fallback。故 adapter 預設不啟用，任何缺少設定、網路錯誤、模型不可用均回傳 `BLOCKED_BY_EXTERNAL_DEPENDENCY`。

## 本階段整合界線

1. 以 migration-first 建立本地相容表及 `migrations/insforge/001_visual_backend.sql`，不替換既有 visual_* 表。
2. 以 server-only `InsForge*Adapter` 集中所有遠端呼叫；前端不持有 SDK 或 API key。
3. 建立可重跑、可續傳、SHA-256/idempotency、部分成功與無破壞性 rollback 的 metadata sync MVP。
4. private 素材預設不送出本機；必須明確設定 trusted external backend 與允許狀態才可同步 binary。
5. 本地資料優先；遠端 unavailable 時仍可上傳、分析、搜尋，並在同步紀錄標示 blocker。

外部依賴文件：

- [InsForge SDK](https://github.com/InsForge/InsForge-sdk-js)
- [InsForge Database SDK Reference](https://insforge-468ccf39.mintlify.app/sdks/typescript/database)
- [InsForge platform repository](https://github.com/InsForge/insforge)

## 2026-08-26 實際整合結果

已透過 `@insforge/mcp` 的 `fetch-docs("instructions")`、REST API 文件與 infrastructure
tools 核對後端 2.3.1。遠端原本是空專案，已成功套用
`001_visual_backend.sql`、`002_core_data_layer.sql`，並建立 private
`visual-assets` bucket。

首次可重複同步完成後，由 MCP `run-raw-sql` 與 backend metadata 驗證：

- projects：11
- knowledge_documents：517
- knowledge_chunks：2,430
- visual_assets：1
- embeddings：1
- private Storage objects：518（517 份知識原檔＋1 份視覺原檔）
- 本機 backend_resource_refs：2,959 筆 completed

目前本機 artifacts、activities、activity_tasks、research_sources 均為 0，因此遠端表已
建立但沒有假造資料。13 個 session、18 則 messages、working_memory、retrieval_cache、
audit logs 與所有 credentials 依設計未同步。同步過程曾有 250 個 chunks 因原始內容含
PostgreSQL 不接受的 NUL 控制字元而失敗；修正 payload 邊界清理後只重試失敗項目並
完成，原始本機文件未被修改。
