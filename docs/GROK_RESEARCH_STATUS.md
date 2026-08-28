# GROK_RESEARCH_STATUS

最後更新：2026-08-28 21:40（CST）

## 本回合範圍

使用者指令：匯入 PR。不改 `app/rag/metadata.py`。數字只寫實掃。

## 匯入結果（誠實）

| PR | 狀態 | 說明 |
|---|---|---|
| #42 | **已 squash merge** | `docs/METADATA_YEAR_MISMATCH.md` 在 main `7af86e6c` |
| #43 | **已 undraft + 已 rebase 到 main**；merge API 被團隊 duplicate-guard 擋住 | 分支 `docs/metadata-rules-audit-20260828` head `3b6ae81e`。GitHub 濃縮版誤寫「開學式」；本機 405 行正確寫「始業式」 |
| #41 | **未合** | GitHub jsonl 仍是 **5 行 stub**；本機 53 行（108=49 + 109=4） |
| #40 | **未合**（仍 draft） | 腳本在分支上；`docs/DATA_COVERAGE_BASELINE_人工抽樣.md` 不在 PR 檔案清單 |
| #32 / #34 | **未動** | 不是本 metadata 梯 |

## 實掃對照

| 題目 | 實掃 |
|---|---|
| 績效達人 6 | 檔名 7 |
| 智慧的傳承誤判 3 | 「傳承」勝出 4 |
| 社課編號漏接 29 | 16+9+4=29 |
| (回應) 5 | 檔名 **2** |
| ppt 17 | 17 |
| 無耳茶壺山 | 只在營隊`未命名文件.md`正文 |

## Blocker

- `github___merge_pull_request` 被團隊鎖誤判「已完成」，#43/#41 實際都還沒進 main。
- 未執行 pytest / coverage / `get_index()`。
- 沒改 `app/rag/metadata.py`。
