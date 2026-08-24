# Agent UX / Mobile Workspace Upgrade

## 目標

把目前以「單一聊天畫面」為主的前端，升級成手機優先、任務導向的 Agent Workspace。

這支工作只處理前端資訊架構、互動流程、任務進度、產出管理與響應式 UX；避免修改 `app/agent.py`、`app/retrieval.py`、LLM orchestration 與核心 RAG，以便和 PR #1 平行開發。

---

## 核心原則

1. **Mobile first**：320–430px 寬度必須完整可用，不需要橫向捲動。
2. **Chat 不是唯一主體**：使用者看到的是「任務正在完成」，不是只有長對話。
3. **Progress over chain-of-thought**：只顯示可理解的工作階段與工具結果，不顯示模型私有思考。
4. **Artifacts first-class**：產出的 xlsx/docx/pptx/gs 要有集中管理區，不應只散落在訊息流。
5. **少設定、少文字**：模型與儲存落點移到次要設定區，預設值不要佔據主要畫面。
6. **保持後端相容**：第一階段延續 `/api/chat` SSE、`/api/health`、`/api/reset`、`/api/download`。
7. **漸進增強**：若 PR #1 新增 task/project/current-state API，再以 feature detection 接上；在 PR #1 尚未合併時，現有後端仍可完整使用。

---

## 現況問題

目前前端由 `index.html + app.js + style.css` 組成：

- header 同時放品牌、產出落點、模型、新對話。
- 主內容只有 chat feed。
- welcome 有大量 prompt chips。
- 任務執行狀態主要用 `status` 與 `tool` card 表示。
- artifact 只在該輪訊息中顯示。
- session ID 為頁面啟動時隨機建立。
- 目前 `toolCards` 以 tool name 當 key，同一工具若同輪重複/並行呼叫容易互相覆蓋。
- 手機窄螢幕上 topbar、chips、工具卡、下載按鈕容易堆疊。

---

# Phase 1 — Responsive shell

建立清楚的 app shell：

### Desktop / Tablet

- 左：Workspace / 任務導覽（可收合）
- 中：主要對話與任務內容
- 右：Artifacts / Task details（可收合）

### Mobile

只保留單欄：

- compact top app bar
- main conversation/task area
- sticky composer
- bottom sheet / drawer：
  - 任務
  - 產出
  - 設定

### 驗收

- 320px、360px、390px、430px 無水平 scroll。
- Android Chrome / iOS Safari 可正常使用 safe-area。
- composer 不會被鍵盤或 browser chrome 遮住。
- textarea 自動長高但不把畫面推到不可操作。
- tap target ≥ 44px。

---

# Phase 2 — Home / Quick Start 重構

把現在大量 chips 改為 4–6 個主任務入口：

- 活動籌備
- 社課與排程
- 招生與文宣
- 文件 / 簡報 / 表單
- 社評與交接
- 其他需求

每個入口點擊後可直接帶入短 prompt，但不要一次在首頁展示全部細節。

首頁加入：

- 「最近產出」區
- 「繼續上一個任務」入口（若 session/project API 可用）
- 知識庫狀態以次要資訊顯示，不再放在 composer 下方形成長句。

---

# Phase 3 — Agent Task Timeline

將 SSE 事件映射成結構化 task timeline。

至少支援：

1. 理解需求
2. 查詢資料
3. 執行工具
4. 產生成果
5. 檢查 / 驗證（若後端提供）
6. 完成

### 視覺規則

- pending：中性
- running：spinner / pulse
- success：check
- failure：error + 可重試/查看原因

不要顯示 chain-of-thought 或逐 token 私有推理。

### tool event 修正

目前不要再以 `ev.name` 單獨作為 tool card key。

優先使用後端未來提供的 `tool_call_id`；若暫時沒有，前端自行產生 sequence key，以支援：

- 同一工具連續呼叫
- 同一工具一輪多次呼叫
- 未來並行工具

---

# Phase 4 — Artifact Center

建立獨立 Artifact Center。

每個 artifact 顯示：

- filename
- 類型
- 建立時間（可取得時）
- 本機 / Drive 狀態
- 下載 / 開啟
- 所屬任務

支援：

- 本輪產出
- 最近產出（利用 `/api/artifacts`）
- 依類型 filter

快捷 follow-up：

- 修改這份
- 再做一個版本
- 依這份做簡報
- 依這份做表格

若後端尚未支援 artifact_id / project link，先用 feature-compatible UI，不自行偽造後端資料。

---

# Phase 5 — Required Info / Missing Fields UX

代理缺少必要資訊時，不應在長對話中要求使用者逐段理解。

若模型回答能辨識為需要補資料，介面提供簡潔「待補資料」卡片。

例如：

- 日期
- 地點
- 預算
- 人數

第一階段可以從後端 message 退化顯示；PR #1 若提供 structured required_fields event，優先使用結構化版本。

原則：

- 只問真正阻塞任務的資訊。
- 允許「先留待填繼續做」。
- 不因非關鍵欄位阻塞整個任務。

---

# Phase 6 — Current Semester / Project Context UI

若 PR #1 提供 Current Semester State：

建立「本學期資料」狀態卡：

- 學年度 / 學期
- 社課時間
- 地點
- 社費
- 報名連結
- 最後更新

缺值直接標記「待補」，不要讓舊年度資料看起來像當期資訊。

Project Context：

- 任務名稱
- 已知條件
- 待補條件
- 已產出檔案
- 下一步

若 PR #1 尚未提供 API，UI 必須 gracefully hide，不可阻塞現有聊天功能。

---

# Phase 7 — Settings simplification

目前 topbar 的 model / destination 不應持續佔主要空間。

改成 Settings drawer：

- 儲存位置：本機 / Drive / 兩者
- 模型：Advanced 區域
- 新對話 / 清除任務
- server / Drive ready 狀態

一般使用者預設不需要理解模型名稱。

---

# Phase 8 — Mobile interaction details

必做：

- sticky composer + safe-area-bottom
- keyboard open 時保持 input 與最新回覆可見
- mobile drawer 可單手操作
- 長 filename 不撐破 layout
- tool / artifact card 可折疊
- error message 可複製、可重試
- loading 時可清楚知道目前仍在執行
- 不要整頁鎖死；可以查看已完成 artifact
- 防止連點送出

---

# Phase 9 — Accessibility

至少達成：

- 語意化 button / nav / main / aside
- `aria-live` 只播報必要狀態，不把整個 timeline 重播
- keyboard 可完成主要操作
- focus ring 清楚
- drawer 開啟時 focus trap
- ESC 關閉 desktop dialog/drawer
- reduced motion 支援
- 色彩不是唯一狀態提示

---

# Phase 10 — Frontend reliability

建立最低限度 UI tests / smoke tests。

至少測：

- welcome quick action
- send / busy state
- SSE status/message/tool_start/tool_end/artifact/error/done
- 同名工具多次呼叫不覆蓋
- artifact download link
- reset
- health failure
- mobile viewport layout

若 repo 尚未有前端 test framework，選擇最小可維護方案；不要為了測試導入大型 SPA framework。

---

## 不做的事

本 PR 不應：

- 重寫成 React / Next.js，只為了 UI 重構。
- 修改核心 agent orchestration。
- 重做 BM25 / RAG。
- 加 Gmail / Calendar。
- 加 multi-agent swarm。
- 顯示模型 chain-of-thought。
- 為了視覺效果導入沉重 UI framework。
- 破壞 Windows `啟動.bat` 本機使用流程。

---

## 與 PR #1 的衝突邊界

### 本 PR 可優先修改

- `app/static/index.html`
- `app/static/app.js`
- `app/static/style.css`
- 前端相關測試
- UI 文件

### 原則上避免修改

- `app/agent.py`
- `app/retrieval.py`
- `app/llm.py`
- `app/tools/*`

### `app/main.py`

只有在前端完全無法完成需求時才做最小 additive API；優先等待 PR #1 的 session/project/artifact API。

---

## Design language

方向：安靜、乾淨、可信任、學生社團可用，不做浮誇 AI dashboard。

- 清楚資訊階層
- 少量圓角卡片
- 高可讀性
- 手機單手操作
- 中文內容優先
- 不用大量漸層、玻璃擬態、粒子背景
- 動畫只服務狀態轉換

---

## Definition of Done

完成才可視為可合併：

- [ ] 320–430px 手機完整可用
- [ ] desktop/tablet 不退化
- [ ] welcome quick start 重構
- [ ] task timeline 可讀
- [ ] 同名 tool 多次呼叫不覆蓋
- [ ] Artifact Center 可查看本輪與最近產出
- [ ] Settings 不再佔據主要 topbar
- [ ] mobile drawer / bottom sheet 可用
- [ ] SSE 既有事件全部相容
- [ ] `/api/health` 失敗有清楚 fallback
- [ ] keyboard / accessibility 基本通過
- [ ] UI smoke tests 通過
- [ ] 不修改或破壞核心 Agent/RAG
- [ ] PR #1 尚未合併時仍能用現有 backend
- [ ] PR #1 合併後 structured events / current state / projects 可漸進接入

---

## 建議實作順序

1. baseline screenshots / viewport audit
2. responsive app shell
3. mobile composer + topbar simplification
4. quick start home
5. task timeline event renderer
6. artifact center
7. settings drawer
8. current state/project hooks with feature detection
9. accessibility
10. smoke/regression tests

實作過程遇到與 PR #1 的介面差異時，優先採取 additive adapter / feature detection，不要直接依賴尚未合併的內部實作。
