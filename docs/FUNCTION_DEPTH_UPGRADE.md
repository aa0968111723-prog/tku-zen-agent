# 功能深度優化：現況地圖與第一批驗收標準

本文件記錄本次功能深度優化的範圍。目標是加深既有工作台的任務連續性與可追溯性，保留既有 `/api/chat` SSE、NVIDIA Build、BM25／LSA 混合檢索、工具與文件產出入口。

## 現況功能地圖

| 能力 | 目前狀態 | 主要實作位置 | 本次判定 |
|---|---|---|---|
| 對話與 SSE | 已可用；`/api/chat` 送出結構化事件 | `app/main.py`, `app/orchestrator/` | 保持相容，增加工作流事件 |
| 規則式路由 | 已有 9 個 skill，可限制工具 schema | `app/skills/__init__.py` | 半完成；單輪路由，無複合任務圖 |
| 工作流狀態 | 有 stage、plan steps、working memory | `app/orchestrator/state.py`, `app/services/memory.py` | 半完成；沒有控制命令與失敗步驟 checkpoint |
| Session／Project | SQLite 持久化、同 session 綁 project | `app/services/session_store.py` | 已可用；缺少可操作的 resume／retry／cancel API |
| 知識庫 | BM25 + LSA + metadata rerank；本學期資料優先 | `app/retrieval.py`, `app/rag/` | 半完成；來源欄位與衝突報告不足 |
| 外部研究 | 可查 `knowledge/社群/`，回傳四段分析 | `app/tools/social.py` | 半完成；來源沒有完整 provenance record，無可信度／驗證狀態 |
| 文件與 artifact | 可產 Word／Excel／PPT／表單／網宣草稿，產後驗證 | `app/tools/`, `app/verification/` | 已可用；版本鏈已有 parent_id，主要清單仍會列所有版本 |
| 多模型與容錯 | 有模型路由、HTTP retry、全域 telemetry | `app/llm.py` | 半完成；任務成本與工作流失敗沒有綁在 project |
| 前端工作台 | 手機優先、設定頁、產出清單 | `app/static/` | 本次不做大型 UI 改版 |

目前沒有真正完成的項目包括：多步驟任務的可恢復執行、複合任務的明確依賴關係、外部研究的來源級追溯與衝突處理。這三項會直接影響「研究後產出」、「沿用上一份」與長任務可靠性，因此列為第一批升級。

## 第一批三項升級與驗收標準

### 1. 可恢復任務工作流

- 每次任務都保存工作流狀態、步驟狀態、嘗試次數、最後錯誤與下一步；伺服器重啟後可讀回。
- 提供使用者可理解的狀態 API：查詢、暫停、繼續、重試失敗步驟、取消。
- 暫停／取消只在安全 checkpoint 生效，不會把已完成步驟標成失敗。
- 重試只把 `failed` 步驟恢復為 `pending`，已完成的檢索與產出不重跑；可分類 timeout、工具錯誤與模型錯誤。
- 舊 `/api/chat` SSE 事件維持不變，另加工作流事件；不傳送內部推理內容。

### 2. 複合任務與任務延續

- 「研究其他學校後，產生淡江招生輪播」會建立研究→淡江網宣的依賴步驟，暴露兩階段所需工具。
- 「把輪播改成 Reels 腳本」、「把上一份改成簡報」、「接續剛才」會沿用同一 project 的狀態、事實與最新 artifact。
- 同一 project 中已成功的檢索查詢會被快取；延續任務不重複查相同內容，除非知識索引已變更。
- 複合任務中任一階段失敗時，能指出失敗節點與下一步，不把整個任務宣稱完成。

### 3. 來源分層、研究 provenance 與衝突偵測

- 每個檢索段落明確標記本學期、社團固定資料、歷史資料或外部研究資料，並遵守來源優先級。
- 外部研究結果保存來源標題、網址、日期、摘要、可信度與驗證狀態；缺少可驗證欄位時標示「待驗證」，不得寫成確定事實。
- 同一活動／欄位出現不同年份或不同值時，輸出衝突清單與來源，不自行選一個值冒充當期資料。
- 研究結論可回到來源 record，且可帶入後續淡江產出；外校資料不會變成淡江事實。

## 不在本次範圍

本次不重寫前端、不改變既有工具 schema 的必要參數、不移除 NVIDIA Build／BM25／LSA／文件產出，也不把 Instagram 草稿模式誤標成自動發布。
