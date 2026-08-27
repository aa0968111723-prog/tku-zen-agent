# tku-zen-agent 網站資訊與營運手冊

本文件整理網站目前**實際存在**的頁面、資料、API、權限、儲存與部署需求。內容以目前程式碼為準，不把尚未設定的外部服務或金鑰寫成已啟用功能。

## 1. 網站定位

網站是「淡江大學領袖禪學社 AI 工作台」，提供兩個主要入口：

| 網址 | 用途 | 適用使用者 |
|---|---|---|
| `/` | AI 任務工作台：聊天、知識庫檢索、研究、文件／Artifact 產出、任務續接 | 社團幹部與內部使用者 |
| `/visual-assets` | 視覺資料庫：素材匯入、分析、審核、智慧搜尋、資料整理與素材包輸出 | 需要整理照片／影片／文件的使用者 |

`/openapi.json` 是給管理與整合用途的 API 描述；部署模式下不應公開給未授權訪客。網站沒有獨立的 React/Next 前端，頁面由 FastAPI 提供的原生 HTML、JavaScript 與 CSS 組成。

## 2. 使用者看到的功能

### AI 工作台 `/`

- 四種工作方式：問 AI、直接生成、製作範本、執行計畫。
- 快速入口：做網宣、規劃活動、查社團資料、研究其他學校、產生文件、繼續上一個任務。
- 對話會先整理任務摘要，再以 SSE 顯示檢索、工具、驗證、修復與 Artifact 進度。
- 可上傳文字文件或圖片作為附件。
- 可查看最近任務、最近產出、工作階段與下載連結。
- 設定抽屜可選擇產出位置、模型、一般授權與管理入口。
- 對外 Instagram 操作預設為草稿；除非管理權限、發布開關、確認旗標與外部 API 均通過，不會發佈。

### 視覺資料庫 `/visual-assets`

- **資料庫**：統計、最近上傳、人物／社團／活動／場景捷徑。
- **上傳分析**：拖曳批次、手機拍照、資料夾匯入、manifest、相對路徑、續傳、部分失敗與分析進度。
- **智慧找照片**：自然語言、欄位篩選、日期／比例／畫質／商用權限、重複圖片、以圖搜圖。
- **待確認**：逐筆確認、修正、忽略、批次確認、查看 evidence。
- **資料整理中心**：真實資料盤點、已整理、待確認、衝突、未分類、重複、缺少來源、分析失敗與同步狀態。
- **分析詳情**：原圖／縮圖、OCR、日期候選、場景／活動／社團／學校標籤、人物數量、品質、信心、來源與狀態。
- **輸出**：原圖、縮圖、1:1、4:5、9:16、16:9，以及含圖片、JSON、CSV 的素材包。

人物辨識只產生已確認、可能、未知或不辨識身分的觀測；沒有使用者確認或可靠來源時，介面不應把相似臉部寫成確定姓名。

## 3. 網站資料從哪裡來

### 本機知識與社團資料

| 路徑 | 內容 | 注意事項 |
|---|---|---|
| `knowledge/劇本` | 任務劇本、故事與產出規則 | 會隨映像部署；更新後需重新建置部署 |
| `knowledge/語料` | 社團內部文件與可檢索內容 | 不應放入外部公開研究結果 |
| `knowledge/雲端文件` | Google Drive 同步或匯入的文件 | 需確認來源與權限 |
| `knowledge/社群` | 外部公開研究的參考來源 | 只作公開參考，不能直接當淡江事實 |
| `data/current_term.yaml` | 本學期社長、社課、社費等真實欄位 | 不進 Git；由管理介面或部署磁碟維護 |

網站要顯示「今年」資訊時，請在本學期設定填入：學年度、學期、社團全名、社長、幹部、社課時間、社課地點、社費、招生期間、報名連結、共用雲端資料夾與資料來源備註。空白欄位會顯示為「待填／尚未設定」，系統不會拿歷年資料代替。

### 持久化資料

- `DB_PATH` 指向 SQLite，保存 users、projects、sessions、messages、artifacts、activities、research_sources、audit logs、視覺素材 metadata 與同步狀態。
- `VISUAL_ASSET_DIR` 保存原始檔、縮圖及非破壞性衍生圖；大量 binary 不放進 SQLite。
- `VISUAL_EXPORT_DIR` 保存匯出的素材包與比例版本。
- `OUTPUT_DIR` 保存聊天產出；若使用 Drive，仍建議保留本機持久化路徑作為失敗時的暫存與追蹤。
- `GOOGLE_CREDENTIALS_FILE`、`GOOGLE_TOKEN_FILE` 只存服務端檔案，不可提交到 Git 或送到瀏覽器。

原始素材不可由網站覆寫或刪除。重複檔案以 SHA-256／既有去重流程標記並保留原始檔與 lineage。

## 4. API 導覽

除登入頁與靜態頁面外，以下 API 預設都需要目前使用者授權；回應會依 user/project scope 過濾。

### 身分、工作階段與核心工作流

| 方法 | 路徑 | 功能 |
|---|---|---|
| POST/GET | `/api/auth` | 取得或建立一般登入 cookie |
| POST | `/api/auth/logout` | 清除一般登入 |
| POST | `/api/admin/auth`、`/api/admin/logout` | 管理者登入／離開 |
| POST/GET | `/api/session`、`/api/session/resume` | 建立與恢復工作階段 |
| GET | `/api/sessions`、`/api/artifacts` | 最近任務與產出 |
| POST | `/api/chat` | SSE 聊天、工具呼叫與產出 |
| POST | `/api/chat/cancel` | 停止目前任務 |
| GET/POST/PATCH | `/api/activities...` | 活動與任務管理 |
| POST | `/api/visual/generate` | fal.ai 視覺產出（未設定時回報阻塞） |
| GET | `/api/download` | 下載已授權 Artifact |

### 視覺素材與搜尋

| 方法 | 路徑 | 功能 |
|---|---|---|
| POST | `/api/visual-assets/upload` | 單張／批次上傳 |
| POST | `/api/visual-assets/import` | 資料夾 manifest 匯入、續傳與部分成功 |
| GET | `/api/visual-imports/{id}` | 查看匯入進度與逐檔錯誤 |
| POST | `/api/visual-assets/analyze`、`/{id}/analyze` | 批次或單檔分析 |
| POST | `/api/visual-assets/{id}/retry`、`/{id}/cancel` | 分析重試／取消 |
| GET | `/api/visual-assets/dashboard` | 素材統計與最近上傳 |
| GET | `/api/visual-assets/search` | 關鍵字、語意與欄位混合搜尋 |
| POST | `/api/visual-assets/search-by-image` | 以圖搜圖 |
| GET/PATCH | `/api/visual-assets/{id}` | 查看／修正素材 metadata |
| GET | `/api/visual-assets/{id}/file` | 受 ACL 保護的原圖、縮圖或比例檔 |
| POST | `/api/visual-assets/{id}/confirm`、`/entities/confirm` | 整筆或單一實體確認 |
| GET | `/api/visual-assets/review-queue` | 待確認與失敗資料 |
| GET | `/api/people`、`/api/clubs`、`/api/events`、`/api/scenes` | 實體篩選清單 |
| POST | `/api/visual-assets/export` | 素材包 ZIP、JSON、CSV |
| GET/POST | `/api/visual-collections...` | 素材包、分鏡、社群貼文、海報集合 |

### 資料整理、Entity Graph 與 InsForge

| 方法 | 路徑 | 功能 |
|---|---|---|
| GET/POST | `/api/data-organization/inventory` | 掃描真實資料並保存統計報告 |
| POST | `/api/data-organization/organize` | 可重跑的既有資料整理 |
| GET/POST | `/api/data-organization/runs...` | 查看／重試整理工作 |
| GET/POST | `/api/library/context-nodes`、`/api/library/context-search` | Project → Story → Scene → Shot → Output context resolver |
| GET | `/api/visual-backend/status` | 查看 InsForge 狀態，不回傳金鑰 |
| POST/GET | `/api/visual-sync...` | 視覺 metadata／允許的檔案同步、重試、標記回滾 |
| POST/GET | `/api/backend-sync...` | projects、artifacts、activities、knowledge 等可選同步 |

### 公開研究

| 方法 | 路徑 | 功能 | 來源界線 |
|---|---|---|---|
| GET/POST | `/api/public-sources...` | 淡江官方公開來源 | 只能作淡江官方公開證據 |
| POST | `/api/public-web-search` | Perplexity 公開網路搜尋 | 外部參考、pending_review/probable |
| POST | `/api/instagram/public/account-search` | Meta 授權範圍內公開專業帳號 | 不等於人物身分驗證 |
| POST | `/api/instagram/public/hashtag-search` | Meta 公開 hashtag | 需 Meta 權限與開關 |
| GET | `/api/instagram/status` | 查看 Instagram 連接狀態 | 不回傳 token |

搜尋結果會保存 provider、URL、日期、retrieved_at、search_id、project scope、信心與確認狀態；未設定外部依賴時使用 `BLOCKED_BY_EXTERNAL_DEPENDENCY`，不假造成功。

## 5. 主要資料模型

目前採「既有表擴充＋migration-first」，不是另建第二套資料庫：

- 核心：`users`、`projects`、`sessions`、`messages`、`artifacts`、`working_memory`、`retrieval_cache`、`research_sources`、`activities`、`activity_tasks`、`audit_logs`。
- 視覺：`visual_assets`、`visual_analysis_jobs`、`visual_observations`、`visual_date_candidates`、`visual_people`、`visual_clubs`、`visual_events`、`visual_scenes`、`visual_person_references`、`visual_collections`。
- 匯入與同步：`visual_imports`、`visual_import_items`、`sync_runs`、`sync_run_items`、`visual_asset_backend_refs`、`backend_sync_items`、`backend_resource_refs`、`backend_user_mappings`。
- 關聯與治理：`sources`、`entities`、`asset_entities`、`events`、`review_queue`、`embeddings`、`data_inventory_reports`、`data_lineage`、`entity_relationships`、`project_context_nodes`、`project_asset_links`。
- 外部搜尋快取：`external_search_cache`，以 project、provider、query、domains、recency 做 scope，不讓不同 project 互讀。

正式真相來源目前仍是本機 SQLite＋檔案系統。InsForge 是可選的視覺資料層／同步目標；遠端失效時本機上傳、搜尋與聊天仍可工作。

## 6. 必須由營運者提供的設定

### 最小可用（本機單人）

1. `LLM_PROVIDER=zeabur` 與 `ZEABUR_API_KEY`，或 `NVIDIA_API_KEY`。
2. `LLM_MODEL`（需支援 function calling；留白使用供應商預設）。
3. `FAL_KEY`（只有需要圖片理解、OCR 或視覺稿生成時才必須）。
4. `data/current_term.yaml` 的當期真實資料。
5. 若要使用 Drive：服務帳戶 JSON、`DRIVE_FOLDER_ID` 與正確的 `GOOGLE_CREDENTIALS_FILE` 路徑。

### 部署到 Zeabur 必須

| 設定 | 用途 | 建議 |
|---|---|---|
| `APP_ACCESS_TOKEN` | 一般使用者入口鎖 | 必填；未設等同公開，部署模式會拒絕啟動 |
| `ADMIN_ACCESS_TOKEN` | 管理、索引、當期設定、稽核 | 與一般碼分開 |
| `LLM_PROVIDER` + 對應 key | 聊天與工具規劃 | 只放 server-side secret |
| `HOST=0.0.0.0`、`PORT=8080` | 容器監聽 | Zeabur 依 `PORT` 注入時沿用平台值 |
| `DB_PATH` | SQLite | 指向持久化磁碟 |
| `VISUAL_ASSET_DIR`、`VISUAL_EXPORT_DIR` | 原圖、縮圖、匯出檔 | 與 DB 一起掛持久化磁碟 |
| `DEFAULT_DESTINATION=drive` | 產出保存 | 仍需掛 Google 憑證檔 |

### 可選外部服務

| 設定群組 | 啟用條件 | 未啟用時 |
|---|---|---|
| `INSFORGE_*` | 填 `INSFORGE_BASE_URL`、server key，並明確設 `INSFORGE_TRUSTED=true`；同步模式依需求設定 | local source-of-truth，遠端標示 blocked/fallback |
| `PERPLEXITY_*` | `PERPLEXITY_API_KEY`＋`PERPLEXITY_SEARCH_ENABLED=true` | 公開網路研究回 `BLOCKED_BY_EXTERNAL_DEPENDENCY` |
| `INSTAGRAM_*` | Meta token、Business Account ID、權限與 `INSTAGRAM_PUBLIC_SEARCH_ENABLED=true` | Instagram 搜尋不可用；發布仍是草稿 |
| `FAL_KEY` | fal.ai key | 圖片分析／生成標示 blocked，不影響純文字 |
| `ENABLE_LINE_CORPUS` | 明確設 `true` | LINE 語料不進檢索，也不送外部模型 |

完整鍵名與預設值以根目錄 `.env.example` 為唯一操作清單；金鑰不可進 Git、前端 bundle、prompt、資料庫明文或 log。

## 7. Zeabur 上線步驟

1. 從 GitHub 部署本專案，使用根目錄 `Dockerfile`／`zbpack.json` 的 `python -m app` 啟動命令。
2. 建立持久化磁碟，至少讓 `DB_PATH`、`VISUAL_ASSET_DIR`、`VISUAL_EXPORT_DIR` 與需要保留的 `OUTPUT_DIR` 位於磁碟內。
3. 在 Zeabur Secrets 填入一般／管理授權碼與 LLM key；不要把 secrets 寫進映像或 `.env.example`。
4. 若使用 Drive，把服務帳戶 JSON 以檔案方式放到持久化磁碟，設定 `GOOGLE_CREDENTIALS_FILE`，並將目標資料夾分享給服務帳戶。
5. 若使用 InsForge，先在遠端套用 `migrations/insforge/001_visual_backend.sql`、`002_core_data_layer.sql`，確認 private bucket，再設定 `INSFORGE_TRUSTED=true` 與同步範圍。
6. 重新部署後檢查 `/api/health`、登入、`/api/visual-assets/dashboard`、一張小圖片上傳與下載；再執行資料盤點，不要第一次就匯入整棵大型資料夾。
7. 確認 `audit_logs`、同步 run、原始檔與 SQLite 都在持久化磁碟，並設定備份／快照。

Docker 映像只帶入 `app`、`knowledge`、`scripts`、`evals` 與當期設定範本，不會帶入本機 `data/`、金鑰、歷史 sessions 或工作區外的照片。因此「網站上線」不代表本機資料已自動上傳；要用資料整理中心或明確的同步流程匯入。

## 8. 權限與安全邊界

- `local` 模式適合只有自己在本機使用；部署不可依賴 local 模式。
- `token` 模式先用一般授權碼換 HttpOnly cookie；管理功能另外需要管理 cookie。
- 所有一般 API、視覺 API 與公開研究 API 都依目前 user/project scope 過濾；別人的 session、project、private asset 不應可讀。
- 原圖以受保護的 `/api/visual-assets/{id}/file` 代理提供，不公開儲存 bucket。
- `can_approve`、`can_spend` 預設沒有任何人擁有；外部發布必須逐次明確確認。
- Perplexity、Meta、fal.ai 回傳的內容是不可信外部資料，只能作證據候選，不能改寫系統規則或直接確認人物身分。
- 記錄搜尋、下載、修正、同步與發布嘗試，但不記錄 token、Authorization header 或完整私人檔案。

## 9. 上線前檢查表

- [ ] `/api/health` 回報正常且版本符合目前部署 commit。
- [ ] `APP_ACCESS_TOKEN` 與 `ADMIN_ACCESS_TOKEN` 已設定且不同。
- [ ] LLM key 可用；模型支援工具呼叫。
- [ ] `DB_PATH`、視覺素材與匯出目錄已掛持久化磁碟。
- [ ] 上傳一張圖片，確認原圖、縮圖、metadata、分析狀態與 reload 後資料仍在。
- [ ] 使用兩個不同帳號確認 project、session、private asset 不互通。
- [ ] 執行資料盤點，確認統計來自真實目錄而不是 UI 假資料。
- [ ] 測試重複檔、部分失敗、retry、日期衝突與 pending review。
- [ ] 若啟用 InsForge，先以 dry-run／小批次同步，確認 backend status、run、retry 與 fallback。
- [ ] 若啟用 Perplexity／Meta／fal.ai，確認對應 key、權限、用量與成本記錄；未通過的項目保留 `BLOCKED_BY_EXTERNAL_DEPENDENCY`。
- [ ] 下載素材包與 Artifact，確認檔案可開啟且沒有把 API key 放入內容。
- [ ] 建立磁碟與 SQLite 備份，並保留最近一次同步 manifest。

## 10. 目前明確未保證的事項

下列項目不能只靠程式碼推定已完成，必須由部署環境或服務帳號補齊後再驗證：

- 真實 Zeabur、NVIDIA、Zeabur AI Hub、fal.ai、Perplexity 或 Meta 金鑰是否有效。
- Meta App Review、Business Discovery／Hashtag 權限與 Instagram 帳號是否符合平台政策。
- InsForge 專案的遠端 migration、RLS、private Storage、pgvector／RPC 是否已套用並可連線。
- Zeabur 持久化磁碟是否已掛載到上述路徑。
- 工作區外的 Windows 大型照片／影片資料夾是否已掛載給目前部署環境。
- 沒有外部視覺模型時，OCR、圖片描述與 embedding 只能保留失敗或待重試狀態，不應顯示為成功。

這些是部署與外部服務驗收項目，不是可用假資料補上的網站內容。
