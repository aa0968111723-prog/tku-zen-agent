# tku-zen-agent 架構審計（Grok 實查）

審計時間：2026-08-26  
基準：`main` `03e21a7` Merge pull request #23  
實作分支：`feat/grok-full-repair`（**不是** PR #24 的 plan branch）

本文件只記錄本機與 GitHub 實際查到的事實。未在這台 Linux 主機出現的 Windows 素材樹標為路徑不存在，不是「沒有這個功能」。

## PR 狀態（GitHub `gh pr view` + `git log`）

| PR | 標題 | 狀態 | 進入 main？ |
|---|---|---|---|
| 20 | feat: 整合 InsForge 核心資料與知識同步層 | **MERGED** 2026-08-26T03:58Z | 是 `160e60f` |
| 21 | feat: 現有資料整理與視覺智慧資料庫深度整合 | **MERGED** 2026-08-26T08:52Z | 是 `cec541b` |
| 22 | fix: 完整涵蓋內部素材根目錄並排除衍生縮圖 | **MERGED** 2026-08-26T09:25Z | 是 `423f088` |
| 23 | feat: index curated external media taxonomy | **MERGED** 2026-08-26T10:03Z | 是 `03e21a7` |
| 24 | docs: plan multi-day Grok repair… | **OPEN** plan-only（1 檔） | 否 |

PR #24 不得承載 implementation。本輪實作在 `feat/grok-full-repair`。

## 36 項盤點

| # | 項目 | 判定 | 證據 |
|---|---|---|---|
| 1 | Web framework | FastAPI + 原生 HTML/JS | `app/main.py`, `app/static/` |
| 2 | DB | SQLite `config.DB_PATH` | `app/services/session_store.py` |
| 3 | Source of truth | 本機 SQLite + 檔案系統 | docs + session_store + visual_assets |
| 4 | SessionStore | 存在 | `app/services/session_store.py` users/projects/sessions/messages |
| 5 | VisualAssetStore | 存在 | `app/services/visual_assets.py` |
| 6 | Intelligence Library | 存在 | `app/services/library_context.py` + visual search |
| 7 | BM25 | 存在 | `app/rag/hybrid.py` |
| 8 | LSA | 存在 | `app/rag/hybrid.py` / `app/retrieval.py` |
| 9 | RRF | 存在 | `app/rag/hybrid.py` |
| 10 | Keyword search | 存在 | retrieval + visual search |
| 11 | Semantic search | 本機 LSA；非 pgvector 為預設 | hybrid.py；InsForge RPC 可選 |
| 12 | Image search | 存在 | `/api/visual-assets/search-by-image` |
| 13 | OCR | 本機 parser + fal 外部 | `visual_analysis.py`, `fal.py` |
| 14 | Vision provider | fal.ai；未設定則 blocker | `FAL_KEY`, tests assert BLOCKED |
| 15 | Embedding | 本機 deterministic + 可選遠端 | visual_assets / embeddings 表 |
| 16 | SHA-256 | 存在 | visual import / organizer |
| 17 | Perceptual hash | 存在 | visual_assets |
| 18 | Review queue | 存在 | visual_* + `review_queue` |
| 19 | Entity Graph | 存在 | `entity_relationships`, library_context |
| 20 | Worker queue | FastAPI BackgroundTasks + SQLite jobs | 無獨立 Redis worker |
| 21 | Import queue | 存在 | `visual_imports` / `visual_import_items` |
| 22 | InsForge adapter | 存在 | `insforge_adapters.py` Database/Storage/Search/Function |
| 23 | InsForge migration | 001/002/003 已在 repo；遠端表已存在 | `migrations/insforge/` |
| 24 | Storage | 本機 `VISUAL_ASSET_DIR`；InsForge private bucket `visual-assets` | live GET /api/storage/buckets |
| 25 | ACL | 本機 owner/token；InsForge SQL 含 RLS policy | auth.py + 001/002/003.sql |
| 26 | User mapping | `backend_user_mappings` + `INSFORGE_OWNER_ID` | visual_migrations 0003/0004 |
| 27 | Project mapping | projects 表雙邊 | session_store + insforge 002 |
| 28 | Zeabur | Dockerfile + zbpack.json | 根目錄 |
| 29 | Chat | `/api/chat` SSE | main.py / app.js |
| 30 | Knowledge base | `knowledge/` + retrieval | 517 份已同步到遠端（PR20 紀錄＋本次 live count） |
| 31 | Artifact | SessionStore artifacts | 遠端 artifacts 表存在、本次 count 0 |
| 32 | Document generation | tools artifact / gform / social | app/tools |
| 33 | Existing bugs | HTTP adapter 曾 `follow_redirects=True`（SSRF 風險） | 本輪已改 False |
| 34 | Security risks | 金鑰若貼在聊天室需輪替；前端不得持有 InsForge key | .env.example server-only |
| 35 | Data loss risks | 無刪原檔 API；rollback 只撤 local refs | insforge_data_sync |
| 36 | External blockers | 本機無 Windows 素材樹；本 Grok session 無 InsForge MCP 工具 | 見下 |

## 本機 SQLite visual 遷移 ledger

`app/services/visual_migrations.py`：`0001`–`0005`（initial, phase1 import, insforge visual, core data sync, organization depth）。下一版應為 `0006_*`，禁止重建 visual_* 表。

遠端 InsForge SQL：`001_visual_backend.sql`, `002_core_data_layer.sql`, `003_data_organization_depth.sql`。

## 2026-08-26 對 `ge5cr87f.us-east.insforge.app` 的實測

Grok TUI 本 session 未掛 `@insforge/mcp`（只有 github / google_drive / tasks），因此無法直接呼叫 MCP `fetch-docs` 工具。改呼叫與 MCP 相同的後端端點：`GET /api/docs/instructions`（MCP `fetch-docs` 的 `docType=instructions`），以及 `GET /api/docs/db/rest-api`、`GET /api/docs/storage/rest-api`、`GET /api/docs/functions/rest-api`。金鑰未寫入 repo。

### MCP fetch-docs / instructions（對本專案的約束）

- 這是既有 FastAPI + SQLite 專案，**不要**跑 `download-template`，也**不要**把前端改成 TypeScript SDK / Tailwind。
- 應用層 CRUD 走 REST：`/api/database/records/{table}`。POST body **必須是陣列**；upsert 用 `Prefer: resolution=merge-duplicates,return=representation`。
- MCP 工具只做基礎設施（schema、`run-raw-sql`、bucket、function deploy）；應用邏輯不得把 API key 送到瀏覽器。
- Storage 現行文件：先 `POST /api/storage/buckets/{bucket}/upload-strategy`；direct 再用 PUT；S3 presigned 不得帶 InsForge Authorization。舊 PUT 仍可作 fallback。
- Function invoke 路徑是 `/functions/{slug}`（沒有 `/api` 前綴）。Admin 管理才用 `/api/functions`。
- Auth header：REST 用 `Authorization: Bearer`；MCP 用 `x-api-key`。adapter 兩者都送。

| 探測 | 結果 |
|---|---|
| GET `/api/health` | 200 `status=ok` `version=2.3.1` `Insforge OSS Backend` |
| GET `/api/database/tables` | 20 張表：含 visual_assets, entities, review_queue, embeddings, sync_runs, projects, knowledge_* 等 |
| GET `/api/storage/buckets` | `visual-assets` **public=false** |
| Prefer count | visual_assets 1；projects 11；knowledge_documents 517；knowledge_chunks 2430；embeddings 1；review_queue 0；entities 0；sync_runs 0 |
| artifacts / activities | 遠端表在、列數 0（與 PR20 一致，未造假） |

InsForge 官方能力（文件，非 MCP fetch-docs 回傳）：Postgres + RLS + pgvector、Storage（S3 相容）、Edge Functions、Model Gateway、REST `/api/database/records/{table}`、admin SQL 需 admin key 且擋 system tables。

## 這台主機沒有的路徑

`D:\柏能資料\…` 與 `/root/淡大劇本` 等 Windows 同層媒體樹：**不存在**。`DATA_ORGANIZATION_IMPORT_ROOTS` 只在資料夾存在時才加入，部署環境不會誤掃。標為 `BLOCKED_BY_EXTERNAL_DEPENDENCY`（路徑／掛載不在此 runner）。

## 本輪開始修的缺口（additive）

1. InsForge HTTP：禁止 redirect follow、HTTPS/public host 檢查、circuit breaker、X-Request-Id、429/5xx 有限重試、不 log Authorization。
2. Storage HEAD（續傳／checksum，不下載 body）。
3. 測試覆蓋上述行為。
