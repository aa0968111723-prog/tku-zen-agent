# GROK_RESEARCH_STATUS

最後更新：2026-08-28（CST）

## 本回合範圍

把「規則覆蓋率」做成可量測的東西。只做指定三件事：

1. 寫 `scripts/metadata_coverage.py`、`scripts/index_coverage.py`（載入 `app.retrieval.get_index()`）
2. 腳本檔頭註解寫預期輸出格式
3. 手動抽 40 個真實檔名，照 `DOC_TYPE_RULES` / `ACTIVITY_RULES` / `TEAM_RULES` 與 `infer()` 年份規則手推五欄，寫進 `docs/DATA_COVERAGE_BASELINE_人工抽樣.md`

不加規則、不改 `infer()`、不碰 knowledge 原文、不重做已合併的公開研究地圖。

## 已完成

- 確認 open PR：#32（Perplexity/Meta，base=main）、#34（circular import，base=`feat/public-research-map-20260827`，不是 main）。
- 確認 PR #39 **已合併**進 main（`51d503c7`，Complete PR #33 public research map）。本回合不重開、不重寫 `public_map.py`。
- 分支 `feat/metadata-rule-coverage-20260828` 已從當時最新 main（`51d503c7`）切出，第一顆 commit `cf13edb8` 已含兩支腳本。
- 兩支腳本內容核過：
  - 檔頭有預期輸出範例
  - import / `get_index()` 失敗時印「未執行」並非 0 退出碼裝綠
  - metadata 腳本：檔案層／chunk 層五欄未命中率、逐資料夾、規則命中、別名命中（含零命中標記）、三欄全空清單
  - index 腳本：knowledge/ 有但未進 Index.build 來源、來源有但 0 chunk、索引有但磁碟沒有、逐資料夾對照
- 40 個真實檔名從 main 的 `knowledge/雲端文件/{108–115學年度,營隊}` 目錄列表抽出，用與 `infer()` 相同的 `_YEAR_SEM` / `_YEAR` / `_GREGORIAN` + `_match_first` 手推，寫入 `docs/DATA_COVERAGE_BASELINE_人工抽樣.md`。
- 本檔更新。

## 未完成

- **兩支腳本未在本環境對真實 Index 執行。**
  - 原因：私有 repo，本沙盒沒有完整 clone、沒有 `knowledge/` 實體檔、不能 `import app.retrieval`。
  - 嚴禁把題目給的 507／248／226／472 寫成「本腳本本回合 stdout」。
- 尚未把腳本 stdout 貼進 baseline 文件的預留區塊。

## 下一回合從哪裡接

1. 在有完整 repo 的機器、repo 根目錄跑：
   - `python scripts/metadata_coverage.py`
   - `python scripts/index_coverage.py`
2. 把**原始 stdout** 貼進 `docs/DATA_COVERAGE_BASELINE_人工抽樣.md` 文末預留區，開頭那句「腳本未執行」改成「已於 YYYY-MM-DD 實跑」。
3. 不要改規則來「衝高覆蓋率」；先讓數字存在。
4. draft PR 合進 main 前，確認與 #32 / #34 無檔案衝突（本分支只動 `scripts/*coverage.py` 與兩份 docs）。

## Blocker

- 本環境無法執行 `get_index()`。數字待有 knowledge/ 的環境補。
- #34 base 不是 main，與本回合無關，不要借那條分支改覆蓋率。
- #32 仍 open；本回合沒改它的檔。

## 刻意沒做（超出本回合範圍）

- 沒改 `app/rag/metadata.py` 規則（例如把 `1051` 排除、加「總支出」「抒壓禪」）。
- 沒改 `tests/test_rag.py:98` 那種「meta is not None 就 PASS」的斷言。
- 沒重做 PR #33/#39 的公開研究地圖、knowledge card、tool 註冊。
- 沒抓社群、沒抄財務總表／通訊錄個人列。
