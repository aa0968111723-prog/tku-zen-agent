# 視覺化 AI 任務工作台驗收紀錄

## 新流程

輸入 → 任務理解 → 單題反問 → 任務摘要確認 → 五階段執行 → 產出預覽 → 下一步。

- 長任務只會在使用者按下「確認開始」後執行。
- 反問一次顯示一個主要問題，最多三題；已知資料不重問，未知的非阻塞欄位標示「待填」。
- 進度預設收合，摘要列仍顯示目前階段與 `已完成／總步驟`；展開後才顯示控制與細節。
- 社群輸出提供 IG 貼文、六頁輪播、9:16 限動與 Reels 時間軸預覽。
- 產出檔名與內容版本分離，畫面只顯示草稿版、修正版、最終版。

## 10 組情境

| # | 驗收輸入／操作 | 結果 |
|---|---|---|
| 1 | 幫我做期初茶會企劃書 | 通過；詢問對象與時間地點，再顯示企劃摘要 |
| 2 | 幫我寫招生 IG 貼文 | 通過；只補對象、語氣、時間地點 |
| 3 | 研究其他學校後做成輪播 | 通過；顯示 6 頁可滑動預覽與頁碼 |
| 4 | 把上一份貼文改成 Reels | 通過；輸出 Reels 分秒時間軸 |
| 5 | 我不知道要做什麼 | 通過；提供活動、網宣、企劃書、會議紀錄選項 |
| 6 | 你幫我決定格式 | 通過；套用企劃書預設並直接進入摘要確認 |
| 7 | 缺少時間與地點 | 通過；可選待填，不阻塞草稿 |
| 8 | 使用者中途返回 | 通過；可回上一題或返回修改原需求 |
| 9 | 使用者停止生成 | 通過；中止串流、保留完成內容、狀態改為已停止 |
| 10 | 任務完成後修改單一頁面 | 通過；保留其餘頁並帶入目前頁碼與內容 |

## 手機與無障礙

- Playwright 實測 320、360、390、430px：`scrollWidth === viewport width`，無水平溢位。
- 所有可見 button/link 最小高度：44px。
- 反問使用 radiogroup、radio／checkbox、`aria-checked` 與題數 `aria-label`。
- 禪光 AI 與全域狀態使用 `aria-live="polite"`；卡片狀態不只依賴顏色。
- 支援 Tab、Escape、左右方向鍵切換輪播與 `prefers-reduced-motion`。
- Lighthouse accessibility：100／100，binary audit failures：0。

## 截圖

- [mobile-390-question.png](visual-ai-workbench/mobile-390-question.png)：單題反問與即時摘要
- [mobile-390-summary.png](visual-ai-workbench/mobile-390-summary.png)：任務摘要確認
- [mobile-390-progress-expanded.png](visual-ai-workbench/mobile-390-progress-expanded.png)：展開後的五階段進度與任務控制
- [mobile-390-result-card.png](visual-ai-workbench/mobile-390-result-card.png)：穩定檔名、版本、內容預覽與操作
- [mobile-430-carousel.png](visual-ai-workbench/mobile-430-carousel.png)：可滑動輪播與單頁操作
- [mobile-430-reels-timeline.png](visual-ai-workbench/mobile-430-reels-timeline.png)：Reels 腳本時間軸

## 已知限制

- DOCX、PPTX、XLSX 的瀏覽器內預覽目前顯示摘要與前段文字；完整排版仍需下載後開啟。
- 生成社群視覺稿仍依賴既有 fal.ai 設定；沒有金鑰時保留文字與版型預覽。
