# Drive 來源路徑清單狀態

最後更新：2026-08-28（CST）

已完成 108學年度 資料夾，共 49 行，下一個是 109學年度。

## 本回合做了什麼

- 只處理 `knowledge/雲端文件/108學年度/`（49 個 `.md`，與 main 目錄列表一致）。
- 逐檔用 `^> 來源：`([^`]+)`` 抽出原始雲端路徑。
- 寫入 `data/drive_docs_manifest.jsonl`（不放 `knowledge/`，避免被 retrieval 索引）。
- 未改 `app/rag/metadata.py`（階層加權留待可跑覆蓋率腳本的階段）。

## 欄位規則（不准猜）

| 欄位 | 取法 |
|---|---|
| repo_path | repo 相對路徑 |
| original_path | 檔頭 backtick 內原文，未改寫 |
| original_ext | 原始檔名最後一段副檔名 |
| academic_year | 目錄段剛好是 `108` / `108學年度`；不從檔名 `1051`、西元日期推 |
| semester | 目錄段與同學年度相符的 `1081` / `108-2` / `1081社課`；`109-1文宣` 年不符 → null |
| team | 目錄段剛好是組別名（招生組／場務組／總召…）；108 夾沒有這種目錄 → 全 null |
| activity_folder | 掉掉根目錄／學年／組別後的第一個剩餘目錄 |
| lesson_folder | 第二個剩餘目錄（堂次或子活動夾） |

## 108 覆蓋

- 49 行皆有 `academic_year=108`
- semester 有值 22（1=13，2=9），null 27
- team 有值 0（原始路徑沒有組別資料夾，不從檔名「社長時間」補）
- activity_folder 有值 48；唯一沒有的是直接掛在 `108/` 下的 `招生名單.xlsx`
- lesson_folder 有值 18
- original_ext：pdf 23、pptx 15、xlsx 6、docx 5

## 下一個

109學年度（其餘學年度、營隊、講師資訊、開示本回合不碰）。
