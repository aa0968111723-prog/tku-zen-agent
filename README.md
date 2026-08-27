# 淡江大學領袖禪學社 · 專屬 AI 代理

一個知道社團是誰、會照社團規矩做事，而且**會實際把檔案做出來**的 AI 助理。
不是給你建議，是直接產出試算表、企劃書、簡報、Google 表單。

它讀得到：社團知識庫、八份任務劇本，以及**共用雲端 108–115 學年度累積的
507 份歷年檔案**（企劃書、活動細流、社評報告、社課簡報、IG 文案、財務表、
挑戰營資料），所以做出來的東西跟社團一直以來的格式對得上。

模型層可切換供應商（兩家都是 OpenAI 相容端點，只差設定）：

- **Zeabur AI Hub**（建議）—— 一把金鑰通到 GPT／Claude／Gemini／Grok，預付點數計費，
  工具呼叫穩定度明顯優於開源模型。
- **NVIDIA Build** —— 免費額度、開源模型，不用付月費。

圖片的**輸入（理解上傳的照片、海報）與輸出（生成視覺稿）**都走 **fal.ai**；
沒設定 `FAL_KEY` 時圖片功能會誠實說明未啟用，純文字任務不受影響。

現在另有正式的**人物／場景／日期／活動／社團視覺資料庫**：進入工作台後按
「視覺資料庫」，即可批次上傳、查看品質與 OCR 證據、人工確認人物／活動、用
自然語言或另一張圖片找照片，並匯出社群比例圖片或含 JSON／CSV 的素材包。
設計與部署細節見 [視覺資料庫文件](docs/VISUAL_ASSET_DATABASE.md)。

另提供可選的 **InsForge 遠端資料層**：本機 SQLite 仍是 source-of-truth，可將
專案、Artifact、活動、研究來源、知識文件／chunks 與視覺素材同步至 PostgreSQL、
pgvector 與 private Storage。聊天、工作記憶、快取、audit IP 與憑證預設永不同步；
遠端不可用時本機功能照常運作。

網站頁面、API、資料來源、環境變數、Zeabur 持久化與上線檢查表已整理在
[網站資訊與營運手冊](docs/WEBSITE_OPERATIONS.md)。

---

## 三分鐘上手

### 1. 拿一組模型金鑰（擇一）

**Zeabur AI Hub**（建議）：Zeabur 後台 → AI Hub → API Keys。預付點數，
一把金鑰即可用 GPT-4o／Claude／Gemini／Grok，換模型只要改 `LLM_MODEL`。

**NVIDIA Build**（免費）：<https://build.nvidia.com/settings/api-keys> 註冊並產生金鑰。
不用信用卡，註冊送 1,000 點推論額度（可申請加到 5,000），每分鐘 40 次請求。

圖片功能另外需要 fal.ai 金鑰：<https://fal.ai/dashboard/keys>（選用）。

### 2. 雙擊 `啟動.bat`

第一次會自動建立 Python 環境、安裝套件、開記事本讓你貼金鑰：

```
# Zeabur AI Hub（建議）
LLM_PROVIDER=zeabur
ZEABUR_API_KEY=你的金鑰

# 或 NVIDIA Build（免費）
# NVIDIA_API_KEY=nvapi-你的金鑰

# 圖片理解與視覺稿（選用）
FAL_KEY=你的-fal-金鑰
```

模型可用 `LLM_MODEL` 指定（留空用供應商預設）；端點用 `LLM_BASE_URL`
（Zeabur 東京 `https://hnd1.aihub.zeabur.ai/v1`、美西 `https://sfo1.aihub.zeabur.ai/v1`）。

### 3. 瀏覽器打開 <http://127.0.0.1:8848>

**先按右上角「本學期設定」**填上這學期的社長、社課時間地點、社費。
沒填的欄位代理不會猜——它會直接說「還沒設定」，並在產出的檔案裡填「待填」。

然後直接講話：

> 幫我做一份 115 學年度上學期的社課排程表，18 週

要停止：回到黑色視窗按 `Ctrl+C`。

---

## 它怎麼運作

每一輪都跑同一條流程，畫面上會即時顯示走到哪一步：

```
理解需求 → 建立計畫 → 查知識庫 → 呼叫工具 → 檢查產出 → 自動修正 → 交付
```

顯示的是**進度與依據**，不是模型的推理過程。例如：

> ◇ 已理解：活動籌備（要做文件）
> ☰ 計畫：查社團知識庫與歷年範例 → 建立文件 → 檢查產出是否符合社團規範 → 交付
> ! 本學期尚未設定：社長、社費 —— 這些欄位會填「待填」
> ⌕ 找到 9 段（規範 3、歷年範例 6）　歷年範例：114學年度 · 期初茶會 · 企劃書
> ▸ 建立文件：期初茶會企劃書
> ✓ 期初茶會企劃書.docx：檢查通過
> ● 完成

### 事實的優先順序

這是整個 v2 最核心的設計。知識庫裡混著 108–115 學年度的檔案，
不分層的話代理很容易把「113 學年度的社長」當成今年的事實寫進產出。

| 層級 | 內容 | 代理怎麼用 |
|---|---|---|
| 1. **本學期真實資料** | `data/current_term.yaml`（用網頁介面編輯） | 今年的事實只認這個來源 |
| 2. **已確認的專案事實** | 你在這個對話裡親口說過的 | 直接採用 |
| 3. **社團知識庫／任務劇本** | 精選規範 | 照著做 |
| 4. **歷年檔案** | 108–115 的真實文件 | **只當格式範例**，日期人名金額都是當年的 |
| 5. **外校公開參考** | `knowledge/社群/`（其他學校領袖社／禪學社 IG 公開資料） | **只在網宣研究時檢索**；只能比較、分析、找靈感，禁止照抄，永遠不是淡江的事實 |
| 6. 模型常識 | — | 只能用在通用做法 |

**沒有例外**：第 1 層查不到就是 unknown。代理會說還沒設定、請你補，
產出裡填「待填」——不會去第 4 層找一個看起來合理的答案。

這不只是提示詞裡的一句話。產出後的驗證階段會**實際檢查**：
文件宣稱是本學期卻出現 113 學年度 → 擋下並要求修正；
本學期沒有社長資料卻寫出「社長：陳大明」→ 擋下並要求改成「待填」。

---

## 它會做什麼

| 工具 | 產出 | 用在哪 |
|---|---|---|
| 查詢社團知識庫 | — | 動手前一定先查 |
| 找歷年範例 | — | 對齊社團一直以來的格式與寫法 |
| 查本學期資料 | — | 今年的事實 |
| 建立試算表 | `.xlsx` | 社課排程、招生名單、分工表、預算表、器材清單 |
| 建立文件 | `.docx`（可附 `.md`） | 招生企劃、活動企劃書、教案、交接手冊、評鑑報告 |
| 建立簡報 | `.pptx` | 社課投影片、招生說明會、評鑑簡報 |
| 產生 Google 表單 | `.gs` + 說明 | 報名表、回饋問卷、意願調查 |
| 網宣研究 ×3 | 結構化參考 | 檢索／比較／分析其他學校領袖社、禪學社、禪心社的公開做法，固定輸出「他校怎麼做／為何可能有效／淡江怎麼改良／哪些不能照抄」 |
| 網宣產出 ×8 | `.md` 草稿 | IG 貼文、輪播、限動、Reels 腳本、內容日曆、A/B 版本、圖片與影片 prompt——一律是草稿，發布要人來 |

網宣草稿產出後會強制通過 `verify_social_copy`：療效／保證性宣稱、
未經本學期資料確認的日期、與外校資料的長句重疊（抄襲防護）、
外校專名或連結混進淡江文案——任何一項中都會擋下重寫。

模型每次只會看到**該任務需要的 3–7 個工具**，不是全部。工具多了之後
開源模型的選擇正確率會明顯下降，而且每個 schema 都要佔輸入 token。

活動不再只存在企劃書裡：工作台會正式保存活動日期、地點、目標、總負責人，
以及每項工作的組別、負責人、期限與狀態。因此可以直接問「這場活動缺什麼」、
「誰負責什麼」或「哪些工作逾期」，再沿用同一筆資料產生企劃書、簡報與網宣。

簡短查詢預設使用快速模型，研究、文件與複合任務使用較強模型；每個 project
保存模型呼叫、token、估算成本、工具重試與失敗分類。同一輪相同的唯讀工具
呼叫會直接重用，會寫資料或產檔的工具則永遠不快取。

### 為什麼 Google 表單要多一個步驟

Google 沒有可以直接呼叫的表單建立 API。代理改成產一支 Apps Script：

1. 開 <https://script.google.com/home/projects/create>
2. 把 `.gs` 內容整份貼進去 → 按「執行」→ 第一次會要授權
3. 看「執行紀錄」，裡面有表單編輯、填寫、回覆試算表三個連結

表單會建在**你自己的 Google 帳號**底下，不經過第三方。

---

## 它不會做什麼（刻意的）

- **不編造今年的事實**：社長、幹部、社課時間地點、社費、報名連結、日期
- **不寫療效宣稱**：治療、改善憂鬱、保證成功、改運、開智慧、消業障
- **不把對外文宣寫成宗教招募**
- **不把歷年的數字搬成今年的**

前三項會在產出後被自動檢查，違反就退回重做（最多修 2 次，仍失敗會標警告交付）。

---

## 部署到 Zeabur（給全社幹部用）

專案已經包好 `Dockerfile` 與 `zbpack.json`。

1. 推到 GitHub（`.env`、`data/`、產出檔都在 `.gitignore` 裡）
2. Zeabur → 新增服務 → 從 GitHub 部署
3. 環境變數：
   - `LLM_PROVIDER=zeabur` ＋ `ZEABUR_API_KEY`（或改用 `NVIDIA_API_KEY`）
   - `FAL_KEY`（圖片理解與視覺稿；不設就停用圖片功能）
   - **`APP_ACCESS_TOKEN`** ← 一般授權碼，一定要設，見下
   - **`ADMIN_ACCESS_TOKEN`** ← 管理授權碼（重建索引、改本學期資料、Instagram 連接）
   - `DEFAULT_DESTINATION=drive`
   - `DRIVE_FOLDER_ID`
   - `VISUAL_ASSET_DIR=/persistent/visual-assets`（必須使用持久化磁碟）
   - `VISUAL_EXPORT_DIR=/persistent/visual-exports`
   - 選用 InsForge：`INSFORGE_BASE_URL`、`INSFORGE_SERVICE_KEY`、`INSFORGE_OWNER_ID`
   - 啟用前設 `INSFORGE_TRUSTED=true`；知識／private binary 仍各自需要明確 opt-in

### Drive 上傳需要另外掛憑證

光設 `DEFAULT_DESTINATION=drive` 還不夠：`.dockerignore` 刻意把
`data/google_credentials.json` 與 token 檔排除在映像之外（金鑰不進映像），
而 `GOOGLE_CREDENTIALS_FILE` **只接受檔案路徑**、不接受直接貼 JSON 內容。
部署上要用 Drive，必須把服務帳號 JSON 放上平台的持久化磁碟（或在啟動流程
自行把環境變數內容寫成檔案），再把 `GOOGLE_CREDENTIALS_FILE` 指到那個路徑，
並把目標資料夾分享給服務帳號的信箱。

沒掛憑證時 `destination=drive` 的上傳會失敗——檔案仍會產出，但只存在容器內
的 `outputs/`（暫存空間，**容器重啟即消失**），請定期下載保存。

### 安全注意事項

**沒設 `APP_ACCESS_TOKEN` 就等於公開。** 沒設定時是本機單人模式，
所有人共用同一個身分——任何拿到網址的人都能用你的額度、看到彼此的檔案。
設了之後：

- 進站要先輸入存取碼
- 每個瀏覽器拿到獨立身分，session 與產出互相隔離
- 猜別人的 session id 讀不到東西，別人的檔案也下載不到

**部署環境（偵測到 PORT／ZEABUR／RAILWAY 或綁 0.0.0.0）沒設 `APP_ACCESS_TOKEN` 會直接拒絕啟動**，
不再只是警告。平台上的症狀是容器一直重啟（Zeabur 記錄會看到
`BackOff: Back-off restarting failed container`）；啟動記錄裡會印出完整的
補救步驟，補上環境變數後重新部署即可。

環境變數的值請只填授權碼本身，不要連前後引號一起貼——引號會被當成授權碼的一部分。
（`APP_ACCESS_TOKEN`、`ADMIN_ACCESS_TOKEN`、`ZEABUR_API_KEY`、`NVIDIA_API_KEY`、`FAL_KEY`、`INSTAGRAM_ACCESS_TOKEN`
已經會自動去掉貼錯的引號。）

另外：

- 授權碼連錯 5 次鎖 15 分鐘（IP＋cookie 雙維度），錯誤訊息統一為「授權碼不正確」
- `POST /api/auth` 另有專屬節流：每 IP 每分鐘 10 次，超過回 429——全站 240 次/分的
  通用限流擋不住暴力猜碼，所以這條路獨立收緊
- 反向代理（Zeabur／nginx）後不拿代理位址當 key：只在直連端是可信代理（私有／loopback）
  時才看 `X-Forwarded-For`，且取**最右邊的非私有跳**，客戶端自帶的偽造值一律忽略
- 登入鎖定與限流共用同一套客戶端 IP 判定（`app/services/clientip.py`），
  不會出現「鎖定與限流各認一個 IP」的縫隙
- 空字串、只有空白、`null` 的授權碼都視同錯誤，一樣回 401 並計入失敗次數
- 重新輸入授權碼會沿用同一個身分，歷史任務與產出不會消失
- 管理功能（`/api/reindex`、修改本學期資料、`/api/admin/*`）需要第二組 `ADMIN_ACCESS_TOKEN`
- Instagram 未連接官方 API 前一律「草稿模式」：只產草稿，不會、也不能自動發布

### 公開研究服務（可選）

Perplexity 與 Meta Instagram 公開搜尋都只在 server-side 啟用且完成授權後執行；金鑰不可放前端、Git、資料庫或 log。Perplexity 搜尋結果會以 `pending_review`／`probable` 來源保存於所屬 project，並受 project ACL 隔離；未設定金鑰或外部服務不可用時會回報 `BLOCKED_BY_EXTERNAL_DEPENDENCY`，不會假造成功。

必要設定請以 `.env.example` 為準：`PERPLEXITY_API_KEY`、`PERPLEXITY_BASE_URL`、`PERPLEXITY_SEARCH_ENABLED`、`PERPLEXITY_SEARCH_TIMEOUT_SECONDS`、`PERPLEXITY_SEARCH_ATTEMPTS`、`PERPLEXITY_SEARCH_COST_PER_1000`。成本單價未設定時只記錄 invocation 與成本未知，不宣稱免費。

#### 權限分成四種，不是只有「是不是管理者」

| 權限 | 誰有 | 管到什麼 |
|---|---|---|
| `can_view` | 登入即有 | 讀知識庫、對話、自己的產出 |
| `can_manage` | 管理者 | 改本學期資料、重建索引、看稽核紀錄與 API 規格 |
| `can_approve` | 管理者 **且** `EXTERNAL_PUBLISH_ENABLED=1` | 核准對外發佈 |
| `can_spend` | 管理者 **且** `EXTERNAL_PUBLISH_ENABLED=1` | 動用會花錢／消耗額度的外部 API |

`can_approve` 與 `can_spend` 預設**沒有任何人擁有**。Instagram 的發佈、
回覆留言、傳送訊息要同時通過「登入 → 管理 → 核准權 → 花費權 → 明確確認
（`confirm: true`）→ 功能已啟用」六道關卡，缺任何一道都直接擋下。
這是刻意的：預設永遠是草稿模式。

#### 其他防護

- **`/openapi.json` 在部署模式要管理者授權**才看得到；`/docs`、`/redoc` 一律關閉
- 全站回應帶 `Content-Security-Policy`、`X-Content-Type-Options`、`Referrer-Policy`、
  `X-Frame-Options`、`Permissions-Policy`；部署模式另加 `Strict-Transport-Security`
- 改變狀態的請求會比對 `Origin`（CSRF 縱深防禦），cookie 全部 `HttpOnly`＋`SameSite`，
  部署模式再加 `Secure`；管理 cookie 用 `SameSite=strict`
- `/api/chat` 有獨立的每人限流（預設每分鐘 12 次），其餘 API 每 IP 每分鐘 240 次
- 登入成敗、管理操作、發佈嘗試都寫進 `audit_logs`，但**絕不記錄授權碼本身**
  （管理者可在 `/api/admin/audit` 查看）
- 同一個工作階段同時只跑一輪任務，重複送出回 409；「停止生成」會呼叫
  `/api/chat/cancel` 釋放執行鎖，避免額度被並行請求吃光

**容器重啟後本機檔案與 SQLite 會消失。** 所以：
- 產出落點設 `drive`
- 如果平台有持久化磁碟，把 `DB_PATH` 指到掛載點，否則對話紀錄不會保留
- 視覺資料庫要把 `VISUAL_ASSET_DIR` 與 `DB_PATH` 放在同一組持久化備份；
  原圖不會自動刪除或覆寫，不能只備份資料庫而漏掉圖片目錄

---

## 更新知識庫

代理的所有社團知識都來自 `knowledge/`，都是純 Markdown，直接編輯就好：

```
knowledge/
├── 00_社團知識庫.md      ← 社團是誰、精神、十二特質、社課主題庫、語氣指南
├── 劇本/                 ← 各種任務怎麼做才對（8 份）
└── 雲端文件/             ← 共用雲端匯入的歷年檔案（見下）
```

改完重啟，或呼叫 `POST /api/reindex`。

> 這條流程只適用本機。部署版（Zeabur）的 `knowledge/` 是 **build 時燒進 Docker 映像**的：
> 容器裡改檔案改不到，`/api/reindex` 也只能就映像內既有的內容重建索引。
> 要更新部署版的知識庫，請把改好的檔案推上 GitHub 後**重新部署**。

### 匯入共用雲端的歷年檔案

把 Drive 資料夾下載成 zip 放進一個資料夾，然後：

```bash
python scripts/ingest_drive.py --dry-run
```

沒問題就拿掉 `--dry-run`。腳本直接從 zip 串流讀取，不用解壓那 14GB。

**個資用兩道防線擋**：
1. 表格標題出現姓名／電話／信箱／學號／系級 → 只保留欄位結構
2. 有些名冊沒有標題列（例如以個接人命名的分頁），所以再抽樣看資料列內容

實測 507 份裡 103 份偵測到名冊，稽核確認零筆未遮蔽的手機／信箱／身分證／學號。

> 但企劃書與會議紀錄的**正文裡仍有幹部與講師姓名**，
> 沒辦法在不破壞文件價值的前提下拿掉。**所以這個 repo 不可以轉成 public。**

### 匯入 LINE 對話（預設關閉）

```bash
python scripts/ingest_line.py --dry-run
```

匯入會遮蔽電話／信箱／學號，**但不遮人名**。要在 `.env` 把
`ENABLE_LINE_CORPUS` 設成 `true` 才生效。

---

## 測試

```bash
python scripts/selftest.py    # 快速檢查，30 秒
python -m pytest              # 700+ 個測試（含認證／權限／SSE／視覺資料庫／跨校隔離／以圖搜圖／手機版）
python -m evals               # 64 個真實社團情境
```

**全部不需要 API 金鑰。** 需要模型回應的地方用可腳本化的假模型，
所以 CI 與離線環境都跑得完。要量真實模型加 `python -m evals --live`。

Evals 共 64 個情境，含事件流斷言與學校歸屬幻覺偵測，單一案例失敗 CI 即紅。
九個維度與目前分數：

| 維度 | 分數 |
|---|---|
| intent accuracy | 100% |
| skill routing | 100% |
| tool selection | 98% |
| retrieval relevance | 99% |
| grounding | 95% |
| **hallucination（門檻必須為 0）** | **0 件** |
| task completion | 100% |
| artifact validity | 100% |
| latency（框架開銷） | 平均約 127 ms（依機器與索引快取而異） |

---

## 架構

```
app/
├── main.py               FastAPI 路由、認證、任務控制、活動 API、SSE
├── llm.py                模型用戶端（Zeabur AI Hub／NVIDIA Build 可切換、重試、telemetry）
├── orchestrator/         Understand→Plan→Retrieve→Execute→Verify→Repair→Deliver
│   ├── planner.py        規則式意圖分類、複合任務依賴計畫
│   ├── prompt.py         分層系統提示（事實優先順序）
│   └── state.py          可序列化的任務狀態（步驟、metrics、工作流控制）
├── rag/                  Hybrid 檢索
│   ├── metadata.py       從路徑推學年度/活動/文件類型/對內外；外校 captured_at
│   ├── query.py          Query rewrite
│   ├── semantic.py       本機 LSA（scipy 稀疏 SVD）
│   ├── hybrid.py         RRF 融合 + metadata 重排
│   ├── conflicts.py      來源級衝突與過期資料偵測
│   └── context.py        標註來源與年份的 context builder、外校資料硬過濾
├── research/             實體辨識與驗證層（「政大呢」事故後新增）
│   ├── entities.py       entity registry、學校別名詞界防護、反問判定
│   ├── claims.py         來源證據模型（SourceRecord／ClaimRecord）
│   └── verifier.py       回答品質閘門與資料污染偵測
├── retrieval.py          BM25（中文二元組、標題加權、文件層級加權）
├── services/
│   ├── session_store.py  SQLite：user/session/project/message/artifact/memory
│   ├── activities.py     活動缺口、逾期、分工與衍生產出摘要
│   ├── auth.py           本機單人 / 部署存取碼、登入鎖定
│   ├── clientip.py       反向代理後的真實客戶端 IP 判定（XFF 最右非私有跳）
│   ├── ratelimit.py      行程內滑動視窗限流
│   ├── permissions.py    view/manage/approve/spend 四權限模型
│   ├── audit.py          Append-only 稽核紀錄與敏感字串遮罩
│   ├── security.py       密鑰遮罩與稽核輔助
│   ├── roles.py          角色常數
│   ├── current_term.py   本學期真實資料
│   ├── memory.py         工作記憶、事實抽取、對話壓縮
│   ├── fal.py            fal.ai 視覺服務（圖片理解與視覺稿）
│   ├── data_organization.py 現有資料盤點、lineage、Entity Graph 與可續傳 mapping
│   ├── library_context.py ACL-first Library→Project→Scene→Shot 素材解析
│   └── context.py        請求身分（contextvars）
├── skills/               11 個 skill 與規則式路由
├── verification/         產出檢查與修正指示
├── tools/                26 個工具（含受 ACL 保護的素材庫搜尋）
└── static/               聊天介面、本學期設定
evals/                    64 個情境 + 九維評分 + 事件流斷言
tests/                    700+ 個測試
```

### 幾個刻意的取捨

### 長任務與任務延續

每個專案會保存步驟狀態、失敗原因、最近產出與研究來源。前端仍可直接使用
`/api/chat` SSE；需要外部控制時，可呼叫 `GET /api/tasks/{project_id}` 查看狀態，
並使用同一路徑下的 `/pause`、`/resume`、`/retry`、`/cancel`。`retry` 只重設失敗步驟，
不會清掉已完成步驟。對話中也可以說「接續剛才」、「把上一份改成簡報」或「將輪播改成
Reels 腳本」，系統會沿用同一個 project 的摘要、來源與最新 artifact。

活動營運 API 提供 `GET/POST /api/activities`、`GET/PATCH /api/activities/{id}`、
`POST /api/activities/{id}/tasks`、`PATCH /api/activities/{id}/tasks/{task_id}`，以及
`GET /api/activities/{id}/artifacts`。活動、待辦與產出都會驗證使用者歸屬。

**規劃與路由用規則不用模型。** 確定性、不花額度、不多一輪延遲，
而且 evals 才測得起來。模型負責的是「內容怎麼寫」。

**檢索先做，不等模型想到要查。** 開源模型常常跳過 search_knowledge
直接開始編。現在規則層先把相關內容撈好放進 prompt，模型仍可再查。

**驗證是固定階段，不是工具。** 暴露 `verify_artifact` 期待模型自己想到
要呼叫，等於沒有驗證。

**語意檢索用 LSA 不用 transformer。** sentence-transformers 要拉 torch
（約 2GB），對要在幹部筆電上一鍵啟動、還要塞進容器的工具來說太重；
外部 embedding API 則會吃掉免費額度、還要把知識庫送出去。
LSA 用 scipy 稀疏 SVD，離線、192 維、建索引約 2 秒。
scipy 沒裝或語料太小時自動降級成純 BM25。

**對話壓縮用抽取不用摘要。** 模型摘要會改寫或漏掉關鍵事實，
那正是這一層要防的事。

---

## 排錯

| 症狀 | 原因 |
|---|---|
| 黃色警告說沒有金鑰 | `.env` 的 `ZEABUR_API_KEY`／`NVIDIA_API_KEY` 沒填或填錯 |
| 「金鑰被拒（401）」 | 金鑰失效，重新產一組 |
| 「找不到模型（404）」 | 模型代號改了，到 build.nvidia.com/models 查 |
| 「連續呼叫失敗」 | 免費額度用完、超過每分鐘 40 次、或網路問題 |
| 它只回文字不做檔案 | 換模型（工具呼叫服從度差很多）；或把需求講明確：「**做成試算表**」 |
| 產出寫了「待填」 | 正常。到「本學期設定」補上該欄位就會用真值 |
| 一直說「還沒設定」 | 同上。它不會猜今年的資料 |
| 產出內容有錯 | 先看是不是知識庫要更新，改 `knowledge/` 比改程式有效 |
| 部署後大家共用同一份對話 | 沒設 `APP_ACCESS_TOKEN` |
| 部署後容器一直重啟（BackOff） | 沒設 `APP_ACCESS_TOKEN`，看啟動記錄的指示補上環境變數 |
