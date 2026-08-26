# 現有資料盤點與深度整合報告（2026-08-26）

## PR #20 與目前架構

PR #20 已於 2026-08-26 合併到 `main`（merge commit `160e60f`）。本階段直接在其
SQLite source-of-truth、FastAPI、server-only InsForge Adapter 與既有素材庫上做 additive
擴充，沒有建立第二套人物／社團／學校表，也沒有改寫聊天、知識庫或 Artifact 流程。

實際架構如下：

- FastAPI 單體後端；`SessionStore` 與 `VisualAssetStore` 共用 SQLite。
- Intelligence Library 由 Markdown metadata、BM25、LSA 與 RRF hybrid ranking 組成。
- 圖片／影片／文件 binary 留在持久化檔案系統；SQLite 只保存路徑、雜湊、分析與關聯。
- `visual_*` 表是視覺領域 source-of-truth；generic `entities`、`sources`、`events`、
  `asset_entities`、`review_queue`、`embeddings` 是相容 mapping。
- 任務與 Artifact 由 `projects`、`sessions`、`messages`、`artifacts`、`activities` 保存。
- InsForge 是可替換、可停用的遠端資料層；SDK／金鑰不進前端。
- Zeabur 仍須同時持久化 SQLite 與素材目錄，InsForge 失效時本機功能繼續工作。

## 找到的既有能力

- 圖片、影片與文件上傳，批次／資料夾 manifest、續傳、部分成功、單筆重試與重新同步。
- SHA-256、感知雜湊與 duplicate lineage；原檔不可由 PATCH 修改或刪除。
- 圖片品質、OCR、EXIF、日期候選與衝突、場景／活動／社團／學校 observation。
- 人物 reference、明確確認與 review queue；未確認 observation 不可作姓名搜尋事實。
- keyword + deterministic semantic vector + RRF hybrid search、以圖搜圖與 ACL filter。
- 校別 Entity Resolution、同校社團 unique key、來源證據、修正與學習事件。
- InsForge database/storage/search/function/sync adapters，以及 001/002/003 遠端 migrations。

## 真實本機盤點與執行結果

歷史首次執行（`u_local`）先完成了知識庫、既有視覺資產與 `outputs` 的非破壞匯入；
之後才把內部專用的 `data`、`output`、`mobile-shots` 也加入白名單。這樣可以完整涵蓋
專案內的使用者素材，同時不會把程式碼、SQLite、Playwright 暫存或憑證當成素材。

擴大白名單並納入工作區同層社團媒體樹、套用資料夾分類證據後的最新持久化盤點（`inventory_KvxMaMD86E0ChM-w`，
`u_local`）：

| 項目 | 數量 |
|---|---:|
| 圖片（可搜尋素材） | 4,788 |
| 影片（原檔引用） | 313 |
| 文件（含知識與已匯入輸出） | 963 |
| Artifact | 0 |
| 知識來源 | 517 |
| 社團／學校 | 1／1 |
| 人物／活動／日期 | 0／0／289 |
| 重複素材（保留原始副本） | 598 |
| 缺少來源 | 0 |
| 待確認資料 | 30,644 |
| 無法分析素材（distinct asset） | 1 |

`sync_BagwU6QyBcku1Vqk` 與 `sync_KuSKa5U4BpMk0JF6` 是舊白名單的歷史 run；擴大後的
`sync_mT8lfOq9l9hjACSp` 實際完成 551 筆、0 筆失敗（visual asset 7、school 1、club 1、
knowledge document 517、filesystem asset 25）。所有 6 份輸出文件與 19 份手機／本機圖片
都以 SHA-256 與來源 key 做 idempotent mapping，原始路徑保留，沒有移動或覆蓋原檔。

針對工作區外部資料樹的 `sync_u0YcUNrWUpvyTZUZ` 實際建立 6,743 筆 mapping，6,732 筆完成、
11 筆失敗；`sync_qDui70vdHjUvQ27C` 只重試該 11 筆並完成 10 筆，剩 1 份無法解析文件；
補上 GIF/AVIF/JFIF 後，`sync_Y3AukcnS4UXjytp9` 完成 6,066 筆既有 mapping 與 7 個 JFIF。
其中 `淡大劇本` 3,404 個可讀素材（`場景` 子樹 1,797 個）、`淡大所有照片` 88 個、
`05_Photos` 1,903 個、`06_Video` 547 個、`招生影片` 165 個均已納入；原始檔以外部路徑
引用，沒有複製 29GB 原檔，只有素材庫縮圖、metadata、lineage 與 embedding 寫入本機。

最新的 `sync_G37B09ZwDUDs2jPD` 用 additive reconciliation 將 5,547 個既有 visual asset
與 517 個知識文件重新映射，並把 `淡大劇本`／`招生影片` 的資料夾分類轉成
`directory_taxonomy_v1` 的 `probable` 觀察：例如 `場景/上學期社課` 會產生
`scene=社課`、`event=上學期社課`、`school=淡江大學`、`club=領袖禪學社`（均附
relative path evidence，仍需人工確認）。搜尋索引同時納入 relative path，因此不必依賴
外部 OCR/vision 也能找到「淡江領袖禪學社上學期社課」分類素材。

早期 run 曾把 6 個 `data/visual-assets/**/thumbnail.jpg` 當成鬆散圖片；本次盤點已將它們
標記為 `derived_thumbnail`、保留 lineage 與檔案，但從素材搜尋與統計排除。這是可回溯的
metadata 修正，不是刪除。最新 manifest 掃描 6,771 個支援格式檔案，
`unregistered_candidates=0`；598 組重複是檔案系統原始副本與素材庫副本並存的真實結果。

## 本階段 mapping 與 migration

本機 migration `0005_data_organization_depth` 新增：

- `data_inventory_reports`：具時間點的真實統計與去識別 manifest。
- `data_lineage`：原始 ID、相對路徑、來源、SHA-256、migration version 與確認狀態。
- `entity_relationships`：有 evidence/confidence/status 的 Entity Graph。
- `project_context_nodes`：Story／Scene／Shot／Output context。
- `project_asset_links`：任務脈絡與素材推薦關係。
- `sync_runs.idempotency_scope` 與 source-scoped unique index。

所有 generic entity ID 都由 owner、entity type 與原始 ID 組成，避免兩位使用者或同名人物
覆蓋彼此；原始 ID 仍保存在 lineage/evidence。學校與同名社團只會從該使用者擁有的素材
可達關係載入，不能掃到另一位使用者的 canonical 人物或社團。

遠端 `migrations/insforge/003_data_organization_depth.sql` 已實際套用。它建立同名 lineage、
graph、context 表與 RLS，並為 knowledge documents 增加 project-scoped version identity、
`logical_key`、`is_current`、`superseded_at`；hybrid RPC 只搜尋 current version。

## 同步與搜尋行為

- 現有鬆散圖片／影片／支援文件會依相對路徑建立 manifest，以 SHA-256 對既有素材去重；
  已整理資料夾的 scene/event/club/school 標籤會以 `probable` evidence 寫入 review queue。
- 支援 JPEG/PNG/WebP/GIF/AVIF/JFIF、影片、PDF/DOCX/PPTX/XLSX、Markdown/文字/CSV/Apps Script。
- 一筆失敗只更新自己的 sync item；成功項目會 commit，retry 只發現並重跑失敗項目。
- `LibraryContextResolver` 先確認 project ownership，再解析 scene/shot，最後呼叫素材 ACL 搜尋。
- 「淡江招生影片第二幕」可推導校園、明亮、16:9、品質與人物偏好；推導值標為 probable。
- 人名 filter 只接受已確認人物與已確認 observation；可能人物仍保留特徵與 evidence。
- AI 可呼叫 read-only `search_visual_library`，回傳來源、狀態、信心與匹配原因。

## InsForge 適用性與目前阻擋

InsForge 適合做遠端備份、跨裝置查詢、PostgreSQL/pgvector 與安全 Storage，但對 1–2 人使用
不應取代本機 source-of-truth。migration 003 已落地；應用程式內容同步目前正確標示
`BLOCKED_BY_EXTERNAL_DEPENDENCY`，因為尚未提供 runtime `INSFORGE_SERVICE_KEY`、與
InsForge Auth user 對應的 `INSFORGE_OWNER_ID`，也尚未明確設定 `INSFORGE_TRUSTED=true`。
MCP 管理 API key 沒有被誤寫成前端或 runtime key，private binary 也沒有被上傳。

## 保留風險

- 影片已實際匯入 313 個可搜尋資產；原檔仍在外部資料夾，沒有影格解碼器時只提供安全占位
  縮圖，不會虛構人物／場景標籤。
- 現有唯一圖片的外部 vision 分析仍是 `needs_vision_config`，不是 PASS。
- 專用 OCR/vision 與高維 image embedding 仍取決於外部模型；不可用時維持明確 blocker。
- Runtime InsForge owner mapping 與最小權限 server key 完成前，只能 dry-run／本機整理。
- 30,644 筆 probable/pending mapping 需要人在資料整理中心審核，不會自動升級為 verified；
  其中包含新建立的資料夾分類觀察，並非人物身分宣稱。
