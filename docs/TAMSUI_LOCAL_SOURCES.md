# 淡水／淡江在地資料如何進代理

社團代理可以回答「淡水有沒有在地資料庫」，但這不是社團 SSOT。

## 可以加什麼

- 加：公開入口、系統名稱、網址、適用場景、資料等級說明。
- 不加：整座淡水維基館頁面、淡新檔案全文、地籍謹本、戶籍個資。

## 現有接法

1. `knowledge/公開來源/淡水與淡江地方資料入口.md`：代理可讀的導覽卡。
2. `knowledge/00_社團知識庫.md` FAQ：讓 `search_knowledge` 一定命中。
3. `search_tku_public_info`：只讀 `https://*.tku.edu.tw`。淡水維基館主站目前是 HTTP，不進白名單。
4. `search_perplexity_web`：查最新公開網頁時使用，仍要保留原始網址。

## 檢索

現行 `app/retrieval.py` 只索引社團知識庫、劇本、雲端文件、語料、社群外校參考。
`knowledge/公開來源/` 需要靠 FAQ／社團知識庫節的重複摘要才會被 `search_knowledge` 命中。
