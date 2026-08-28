# GROK_RESEARCH_STATUS

最後更新：2026-08-28（CST）

## 本回合範圍

盤點 metadata 規則的誤判與漏接，產出可直接施工的清單。只做指定兩件事：

1. 第 1 回合誤判：績效達人被「績效」判成績效報告；智慧的傳承被「傳承」判成交接。找出全部受害檔，逐檔正確值 + 建議收窄。
2. 第 2 回合漏接：社課N 16 + 社課一～八 9 + 113-1-N 4；`(回應)`；17 份 ppt；活動別名（音樂會、淨灘、抱石、御竹園、金柿、城市微光、開學式、結業式、無耳茶壺山）；家族長／組輔。

寫進 `docs/DATA_METADATA_RULES.md`。不改 `app/rag/metadata.py`。別名比對標記「需 terminal」。規則不得發明 `00_社團知識庫.md` §8 沒有的活動名稱。

## 已完成

- 盤點 open PR（不重工）：
  - #32 Perplexity/Meta（open，base=main）
  - #34 circular import（open，base=`feat/public-research-map-20260827`，不是 main）
  - #40 metadata 覆蓋率腳本（draft open）
  - #41 drive docs manifest 108（open）
  - #42 學年度／學期誤判清單（open）
  - #39 已合併進 main `51d503c7`
- 從最新 main `51d503c7` 切出 `docs/metadata-rules-audit-20260828`。
- 讀完 `app/rag/metadata.py` 三組 RULES + `_match_first` + `infer()` hay 規則；`tests/test_rag.py` label 格式；`tests/test_entity_resolution.py`（本回合不改）；`knowledge/00_社團知識庫.md` §8／§9／§10.1。
- 用 main 的 `knowledge/` tree 實掃檔名，手推 path+label。
- 寫出 `docs/DATA_METADATA_RULES.md`（誤判表、漏接表、建議別名、詞界反例、施工順序）。
- draft PR #43：https://github.com/aa0968111723-prog/tku-zen-agent/pull/43（base=main `51d503c7`，head=`docs/metadata-rules-audit-20260828`）。
- 本續回獨立核對 main tree（518 nodes）+ `00_社團知識庫.md` §8／§10.1；補開 `金柿好日子活動成果.md`、`抱石體驗營企畫書.md`、`營隊/未命名文件.md`、`家族長輔導照顧SOP.md` 檔頭。金柿來源確認為 `2025暑假_挑戰營9th_登峰傳心挑戰營/總召回顧ppt區/`。
- 將完整逐檔表覆寫進 PR #43 的 `docs/DATA_METADATA_RULES.md`（不再只留濃縮版）。

### 實掃與題目數字對照（不編造差額）

| 題目 | 實掃 | 處理 |
|---|---|---|
| 績效達人 6 份演講檔 | 檔名含「績效達人」**7** 份（6 份內容檔 + 1 份 `(回覆)` 表單） | 7 份全列；第 7 份是同分「績效」贏「回覆」 |
| 智慧的傳承 3 份被判交接 | 檔名含「智慧的傳承」且「傳承」勝出 **4** 份；另 4 份因「第七堂」已是社課教材 | 誤判列 4；對照列已正確的 4 |
| 社課N 16 + 一～八 9 + 113-1-N 4 = 29 | 16 + 9 + 4 = 29，與題目相符 | 其中 2 份同時是傳承誤判 |
| `(回應)` 5 份 | 檔名含「(回應)」**2** 份 | 只列 2；不把正文「表單回應」或講題「用心回應」算進去 |
| ppt 17 份無規則 | 檔名含 ppt/pptx/PPT **17** 份 | 17 份全列；註明哪些已被「第三堂／社長時間／入社／期初茶會」打中 |
| 無耳茶壺山 | 只在 `營隊/未命名文件.md` **正文**，hay 吃不到 | 照實寫 |

## 未完成

- **未執行** `pytest tests/test_entity_resolution.py`、`pytest tests/test_rag.py`、兩支 coverage 腳本。
  - 原因：本環境不能 clone 私有 repo 完整 working tree，不能 `import app.retrieval`。
- 未寫 `tests/test_metadata_alias_bounds.py`（下一回合改規則時才寫）。
- draft PR #43 已開；尚未合進 main。
- 本環境仍無法跑 pytest / `get_index()`。

## 下一回合從哪裡接

1. 有完整 repo 的機器，先跑：
   ```bash
   pytest tests/test_entity_resolution.py tests/test_rag.py
   ```
   標記已是「需 terminal」。綠了再動 `app/rag/metadata.py`。
2. 施工順序見 `docs/DATA_METADATA_RULES.md` 文末：先收窄 績效／傳承 → 再補社課編號 → 再補安全形式的「(回應)」→ 最後加活動／組別別名。
3. 每一刀補 `tests/test_metadata_alias_bounds.py` 的反例（文件裡已列出 path）。
4. 不要為了覆蓋率去改規則衝綠燈；#40 的腳本是量測，#42 是學年誤判，本清單是別名誤判／漏接。三份 docs 互補、不要合併重寫。
5. `無耳茶壺山` 就算加了別名，`未命名文件.md` 仍然推不出——要先有檔名或 label 帶地名。

## Blocker

- 本環境無法跑 pytest / `get_index()`。
- 改別名必須 terminal 驗證詞界；特別是裸「回應」會打中「用心回應彼此」講題。
- 不要把裸 `ppt` 加成 document_type。

## 刻意沒做（超出本回合範圍）

- 沒改 `app/rag/metadata.py`。
- 沒改 #32 / #34 / #40 / #41 / #42 的檔。
- 沒收窄績效報告裡過寬的「成果」（會連坐營隊成果檔）。
- 沒把「家長」加成家族長別名。
- 沒抓社群、沒抄財務總表／通訊錄個人列。
- 沒重做公開研究地圖。
