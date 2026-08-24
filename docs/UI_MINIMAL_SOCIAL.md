# 手機優先極簡工作台 UI（v1）

前端只改 `app/static/`。後端契約以「舊後端能跑、新後端能接」為準：端點不存在（404/405）時降級，不把技術細節畫到主畫面。

## 畫面結構

1. **授權入口**：社團名、「請輸入授權碼」、進入工作台。不出現登入／註冊／帳號。錯誤顯示後端 `detail` 原文；429 顯示鎖定提示。授權碼只走 `POST /api/auth` JSON body，不進 URL、不進前端儲存。
2. **工作台首頁**：專案名、底部主輸入框、六顆快捷鈕、最近任務（`GET /api/sessions` 前 5）、最近產出（`GET /api/artifacts` 前 5）。
3. **網宣引導**／**研究引導**：組成結構化 prompt 再 `POST /api/chat`。
4. **設定抽屜**：產出位置、模型下拉（`/api/health.models`）、清除本機授權、管理入口（本學期設定在此）。
5. **草稿細條**：`GET /api/instagram/status` 未連接或端點不存在時，顯示「目前為草稿模式——不會自動發布任何內容」。

主畫面不呈現模型全名、Session ID、Project ID、伺服器路徑、RAG／Token 字樣。

## 快捷鈕

| 鈕 | 行為 |
|---|---|
| 做網宣 | 開引導（目標＋平台＋一句需求） |
| 規劃活動 | 帶入 prompt 到輸入框 |
| 查社團資料 | 帶入 prompt 到輸入框 |
| 研究其他學校 | 開引導（學校可留空＝全部比較） |
| 產生文件 | 帶入 prompt 到輸入框 |
| 繼續上一個任務 | 開最近 session 清單，點了 `POST /api/session` 帶 `session_id` |

## SSE

相容現有事件：`session`／`task_understood`／`plan_created`／`retrieval_*`／`tool_*`／`verification_*`／`repair_started`／`artifact_ready`／`message`／`task_completed`／`error`／`done`。

同時接受新後端可能的 `step`、`artifact`。工具進度列用每次呼叫的獨立 key（`tool_call_id` 或序號），不再用工具名當 DOM key。

## 網宣渲染合約

助手訊息裡的 fenced code block：

`ig-post`／`ig-carousel`／`ig-story`／`reels-script`／`content-calendar`／`ab-test`／`image-prompt`／`video-prompt`

各自做成卡片：複製（Clipboard API＋fallback）、下載（有 artifact 則 `/api/download?artifact_id=`，否則存成文字檔）、重試（原 prompt 再送）。輪播以 `---` 或編號分頁，可左右切換。

研究輸出若含四個標題段（其他學校怎麼做／為什麼可能有效／淡江可以怎麼改良／哪些內容不能直接照抄）→ 可收合參考卡。未匹配的 code block 維持純文字。

## 手機

- 殼層：`#app` 為 flex 直欄＋`100dvh`＋`visualViewport`，對話在 `.chat` 內捲。
- 觸控目標 ≥ 44px；輸入字級 ≥ 16px；`viewport-fit=cover`＋safe-area。
- `prefers-reduced-motion` 關掉動畫；`prefers-color-scheme` 深色；`prefers-contrast: more` 提高對比。
- 錯誤可重試、不鎖死整頁。技術錯誤顯示「系統忙碌中，請稍後再試」。

## Feature detection

| 端點 | 沒有時 |
|---|---|
| `POST /api/auth/logout` | 清 UI 並請使用者重新整理 |
| `POST /api/admin/auth` | 顯示「後端尚未支援」，仍開放本學期設定 |
| `POST /api/admin/logout` | 只關管理區 |
| `GET /api/instagram/status` | 當作未連接，顯示草稿細條 |

## 驗收尺寸

320／360／390／430px：無水平捲軸、快捷鈕兩欄、底部輸入不被鍵盤永久遮住、列表與卡片文字可折行。
