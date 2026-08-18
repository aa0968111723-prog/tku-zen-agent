"""快速自我檢查：不需要 API 金鑰，確認整條鏈路都活著。

完整測試請跑：
    python -m pytest          # 261 個測試
    python -m evals           # 54 個真實情境的 evals
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass

from app import config, retrieval, skills, tools  # noqa: E402
from app.services import current_term as term  # noqa: E402

PASS, FAIL, INFO = "  [OK] ", "  [!!] ", "  [--] "
failures = 0


def check(label: str, ok: bool, note: str = "") -> None:
    global failures
    print((PASS if ok else FAIL) + label + (f" — {note}" if note else ""))
    if not ok:
        failures += 1


print("=" * 66)
print("  淡江大學領袖禪學社 · AI 代理 —— 自我檢查")
print("=" * 66)

# 1. 設定
print("\n[1] 設定")
for problem in config.missing_config():
    print(INFO + problem)
print(f"       產出資料夾：{config.OUTPUT_DIR}")
print(f"       資料庫：{config.DB_PATH}")
print(f"       認證模式：{config.auth_mode()}")

# 2. 本學期狀態
print("\n[2] 本學期真實資料")
state = term.load()
print(f"       {state.term_label()}")
known, missing = state.known(), state.missing()
print(f"       已設定 {len(known)} 項、未設定 {len(missing)} 項")
if missing:
    labels = "、".join(term.FIELD_BY_KEY[k].label for k in missing)
    print(INFO + f"未設定：{labels}（代理會回答「還沒設定」，產出填「待填」）")
check("當期狀態可讀取", True)

# 3. 知識庫
print("\n[3] 知識庫與檢索")
idx = retrieval.get_index(rebuild=True)
stats = idx.stats()
check(f"索引建立：{stats['檔案數']} 份 / {stats['段落數']} 段", stats["段落數"] > 20)
print(f"       來源：{', '.join(stats['來源'])}")
print(f"       語意層：{stats['語意層']}")

for query, expect in [
    ("招生管道", "路宣"),
    ("青年領袖十二項特質", "體魄"),
    ("社課籌備 SOP", "二籌"),
    ("經費核銷", "核銷"),
    ("對外文宣不能寫什麼", "療效"),
]:
    hits = idx.search(query, k=5)
    check(f'查「{query}」', any(expect in c.text for _, c in hits))

bundle = retrieval.build_context(["期初茶會企劃書"])
check(
    f"Hybrid context：{len(bundle.hits)} 段（規範 {bundle.curated_count} / 歷年 {bundle.archive_count}）",
    bundle.curated_count >= 1,
)

# 4. Skill 路由
print("\n[4] Skill 路由")
for message, expected in [
    ("幫我做期初茶會企劃", "event_planning"),
    ("幫我寫招生 IG 文案", "recruitment"),
    ("做一份社評報告", "evaluation"),
    ("幫我做經費預算表", "finance"),
    ("做一份新生報名表", "google_workspace"),
    ("今年社長是誰", "knowledge"),
]:
    got = skills.route(message).skill.name
    check(f"「{message}」→ {got}", got == expected, "" if got == expected else f"期望 {expected}")

for s in skills.SKILLS:
    n = len(tools.schemas_for(s.tool_names()))
    check(f"  {s.label} 暴露 {n} 個工具", n <= 7)

# 5. 產出工具
print("\n[5] 產出工具")
r = tools.dispatch(
    "create_spreadsheet",
    {
        "filename": "_自我檢查_試算表",
        "sheets": [{"name": "預算", "csv": "項目,單價,數量,小計\n海報,120,10,=B2*C2"}],
    },
)
check("create_spreadsheet", r.get("ok"), r.get("message", "")[:80])
sheet_path = Path(r["local_path"]) if r.get("local_path") else None

r = tools.dispatch(
    "create_document",
    {"filename": "_自我檢查_文件", "markdown": "# 標題\n\n## 目標\n\n內容。\n\n## 流程\n\n步驟。"},
)
check("create_document", r.get("ok"), r.get("message", "")[:80])
doc_path = Path(r["local_path"]) if r.get("local_path") else None

r = tools.dispatch(
    "create_slides",
    {"filename": "_自我檢查_簡報", "title": "封面", "slides_markdown": "## 一\n- a\n\n## 二\n- b"},
)
check("create_slides", r.get("ok"), r.get("message", "")[:80])

r = tools.dispatch(
    "create_google_form",
    {
        "filename": "_自我檢查_表單",
        "form_title": "測試",
        "questions": [{"type": "short_text", "title": "姓名", "required": True}],
    },
)
check("create_google_form", r.get("ok"), r.get("message", "")[:80])

r = tools.dispatch("get_current_term", {})
check("get_current_term", r.get("ok"))

r = tools.dispatch("search_previous_examples", {"query": "期初茶會 企劃書"})
check("search_previous_examples", r.get("ok"), f"{r.get('hit_count', 0)} 份")

# 6. 驗證
print("\n[6] 產出驗證")
from app import verification  # noqa: E402

if doc_path:
    report = verification.verify(doc_path, task_type="event_planning")
    check("文件驗證可執行", isinstance(report.ok, bool), report.summary())
if sheet_path:
    report = verification.verify(sheet_path, task_type="finance")
    check("試算表驗證可執行", report.ok, report.summary())

bad = tools.dispatch(
    "create_document",
    {"filename": "_自我檢查_違規", "markdown": "# 文案\n\n禪定可以治療焦慮，保證成功考上。這是補充內容。"},
)
if bad.get("local_path"):
    report = verification.verify(Path(bad["local_path"]), rules=["no_health_claims"])
    check("療效宣稱會被擋下", not report.ok, report.summary())

# 7. 清理
for p in config.OUTPUT_DIR.rglob("_自我檢查*"):
    try:
        p.unlink()
    except OSError:
        pass

print("\n" + "=" * 66)
if failures:
    print(f"  {failures} 項檢查沒過。")
else:
    print("  全部通過。")
    print("\n  完整測試：python -m pytest")
    print("  情境評測：python -m evals")
print("=" * 66)
sys.exit(1 if failures else 0)
