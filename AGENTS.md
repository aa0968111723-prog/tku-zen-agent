# AGENTS.md — tku-zen-agent

給之後進來改這個 repo 的 coding agent 看。這是淡江大學領袖禪學社的專用產檔代理，不是 InsForge / TypeScript SDK 專案。

## 這專案是什麼

Python 3.11 + FastAPI。迴路固定為：

Understand → Plan → Retrieve → Execute → Verify → Repair（最多 2 次）→ Deliver

計畫器與 skill 路由是規則式，不讓模型自己規劃。模型每次只看到該任務的 3–7 個工具。驗證是固定階段，不是一個 tool。

事實優先順序（程式強制，不是提示詞願望）：

1. `data/current_term.yaml`（本學期真實資料）
2. 這次對話裡使用者親口確認的事實
3. `knowledge/` 規範與劇本
4. 歷年檔：只當格式範例，日期人名金額都是當年的
5. 外校公開資料：只准比較，永遠不是淡江事實
6. 模型常識：只能用在通用做法

查不到第 1 層就標 unknown、產出填「待填」。禁止用歷年檔填今年社長／社費／教室。

## 目錄地圖

- `app/orchestrator/` 任務狀態、規則計畫、分層 prompt
- `app/skills/__init__.py` 11 個 skill、關鍵字路由、複合任務
- `app/tools/` 產檔與查詢工具；`dispatch()` 會剝未知參數、檢查權限
- `app/rag/` BM25 + 本機 LSA + RRF；外校資料硬過濾
- `app/research/` 實體、claim、驗證閘門（「政大呢」事故後加上）
- `app/services/` session / auth / 權限 / audit / current_term / 視覺庫
- `app/static/` 工作台前端（不要無故重寫）
- `knowledge/` 社團知識；`knowledge/雲端文件/` 含歷年檔與姓名，預設當敏感資料
- `evals/` 社團情境；預設 `ScriptedModel`，`--live` 才是真模型
- `tests/` 離線 pytest，不需 API 金鑰
- `docs/` 審計與設計筆記，不是執行契約；契約以測試為準

## 改程式前先讀

1. `README.md` 的「事實的優先順序」與「它不會做什麼」
2. `SECURITY.md`
3. 相關測試：`tests/test_orchestrator.py`、`tests/test_skills.py`、`tests/test_verification.py`、`tests/test_routing_cases.py`、`evals/cases.py`

## 硭c規則

- 不要把外校資料寫進淡江產出當事實。
- 不要讓模型規劃取代 `planner.plan_for()`。
- 不要一次把全部工具暴露給模型。
- 不要在 prompt 裡摘要「可能是今年的歷年數字」。
- 不要自動發佈 Instagram／對外發文。發佈必須走 HITL（approve + spend + confirm）。
- 不要把 token、服務帳號 JSON、`data/current_term.yaml` 真實值、LINE 語料 commit 進 git。
- 不要把 `knowledge/雲端文件/` 當成可公開素材改寫或擴散。repo 若是 public，先停手問維護者。
- 不要把 README 的 eval 分數寫成「真實模型 0 幻覺」。那些是 ScriptedModel 的框架上限。
- 不要新增 LangChain / LangGraph / 新的 agent 框架。既有 orchestrator 就是框架。
- 不要為了「看起來聰明」放寬驗證閘門。

## 常見地雷

- 外校研究後的下一句「我們的茶會／今年社費／幹部分工」必須回到 INTERNAL，不可繼承外校 scope。`out_test1.txt` 曾經留下這條迴歸痕跡。
- 「請用一句話介紹淡江大學領袖禪學社」不可路由成 social_research。
- 部署沒設 `APP_ACCESS_TOKEN` 必須拒絕啟動，不要改回警告。
- Zeabur 的 `knowledge/` 是映像燒進去的；改知識庫要 commit + 重新部署，容器內改檔無效。
- 容器重啟會丟 SQLite 與 `outputs/`。不要假設本機檔還在。
- `AGENTS.md` 曾經被 InsForge TypeScript SDK 模板覆蓋。不要再貼回 SDK 說明。

## 怎麼驗證

```bash
python -m pytest
python -m evals
```

改路由或驗證時，至少加一個 `evals/cases.py` 案例 + 對應 pytest。需要真模型再跑 `python -m evals --live`（不在預設 CI）。

## 風格

- 繁體中文給使用者看的字串；程式識別子用英文。
- 先補測試再放寬行為。
- 一個 PR 只做一條可說清楚的行為改變。
- 新 skill 要同時改：keywords、plan_for 步驟、tools 白名單、verification rules、eval case。
