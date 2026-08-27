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

盤點範圍為 `u_local` 可存取的完整工作區資料樹：`01_Projects`、`02_Club_淡大禪學社`、
`03_Documents`、`04_Design`、`05_Photos`、`06_Video`、`07_Audio`、`08_AI`、`09_Resources`、
`90_Archive`、`99_ToReview`、`data`、`knowledge`、`mobile-shots`、`outputs`、`visual_assets`、
`zen-public`、`場務場刊E310`、`對話`、`招生影片`、`挑戰營`、`淡大劇本`、`淡大所有照片`、
`聖者影片`、`資源`、`音樂會`。程式碼、SQLite、暫存下載、工具目錄與憑證檔刻意排除。

最新持久化盤點為 `inventory_EB5YdDJDIPu6YcAd`（2026-08-27，`u_local`）：

| 項目 | 數量 |
|---|---:|
| 圖片（去除衍生縮圖後） | 12,494 |
| 影片（原檔引用） | 6,645 |
| 音訊（原檔引用） | 13,341 |
| 文件／專案檔 | 7,017 |
| Artifact | 0 |
| 知識來源 | 517 |
| 社團／學校 | 1／1 |
| 人物／活動／日期 | 0／0／642 |
| 重複素材（保留原始副本） | 7,899 |
| 缺少來源 | 0 |
| 待確認資料 | 102,811 |
| 無法分析素材 | 16（含 15 筆檔案 mapping failure） |

manifest 實際掃描 56,088 個檔案，支援格式候選 50,158 個，刻意排除 5,930 個非素材檔，
`unregistered_candidates=0`，`scan_errors=0`。重複素材以 SHA-256 保留原始路徑，不覆蓋原檔，
不把大型 binary 塞進 SQLite；大型影音、RAW、PSD、SVG、剪輯專案與壓縮檔只寫入外部路徑、
縮圖／安全占位、metadata、lineage 與可搜尋索引。

續傳同步：`sync_IPG0xqTAJGfphCSw` 曾在 21,126 筆完成時中斷，後續由
`sync_GCrzTscjJiuK5DOg` 完成 44,817 筆、15 筆失敗；`sync_04raZBrACFWjFXT9` 再次逐筆重試，
15 筆仍失敗。失敗檔案是實際損壞 PNG、超過 Pillow 安全尺寸的圖片、不可讀文件或無法編碼的
檔案，均以 `failed` lineage、錯誤訊息與 retry item 保留；沒有將它們偽造為可搜尋成功素材。
舊中斷 run 的 pending item 仍保留作為可追溯歷史，已成功映射的檔案不會重複匯入。

`淡大劇本/場景/上學期社課` 等既有資料夾分類會轉成附有 relative-path evidence 的
`probable` scene/event/club/school observation，搜尋可用資料夾語意找到素材，但仍需人工確認，
不會直接宣稱人物姓名或把不同學校、同名社團自動合併。

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
- 支援 JPEG/PNG/WebP/GIF/AVIF/JFIF/TIFF/BMP/ICO、CR2/ARW/DNG/RAW/HEIC/PSD/SVG、
  MP4/MOV/WebM/M4V/AVI/MKV/MTS/M2TS/3GP/WMV/FLV/MXF/RM、MP3/WAV/M4A/FLAC/OGG/AAC/WMA/
  AIFF/OPUS/MIDI、PDF/DOC/DOCX/ODT/ODS/ODP/PPTX/XLSX、Markdown/文字/CSV/JSON/JSONL/YAML/TSV/
  HTML/XML/INI/LOG/ICS/SRT/EDL/FCPXML/URL、ZIP/7Z/WFP/WFPBUNDLE/BDM/CPI/MPL、After Effects／
  Premiere／PowerDirector／motion template／3D／字型專案檔與 Apps Script。
  RAW/PSD/SVG、影音、音訊、壓縮檔與剪輯 sidecar 會保留原始路徑並產生安全占位縮圖；目前沒有
  對應解碼器時不虛構尺寸、OCR、人物或場景結果。
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

- 影片已實際登錄 6,645 個可搜尋資產；原檔仍在外部資料夾，沒有影格解碼器時只提供安全
  占位縮圖，不會虛構人物／場景標籤。
- 現有唯一圖片的外部 vision 分析仍是 `needs_vision_config`，不是 PASS。
- 專用 OCR/vision 與高維 image embedding 仍取決於外部模型；不可用時維持明確 blocker。
- Runtime InsForge owner mapping 與最小權限 server key 完成前，只能 dry-run／本機整理。
- 102,811 筆 probable/pending mapping 需要人在資料整理中心審核，不會自動升級為 verified；
  其中包含新建立的資料夾分類觀察，並非人物身分宣稱。
