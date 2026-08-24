"""第四階段：反問卡、任務摘要、產出卡與 artifact 血統。

稽核對應：
  · 半成品 #7 後半：澄清回覆後原任務主題遺失
  · 不可靠 #35：單一 _N 後綴開出平行血統
  · 不可靠 #36：.md 孿生檔跨工具互相覆寫
  · 半成品 #29：read_artifact 暴露過窄
  · 半成品 #28：三個死工具
  · 不可靠 #34：resolve_activity 不分學期 fallback
  · 半成品 #27：list_activities 無學年過濾
"""

from __future__ import annotations

from pathlib import Path

from app.orchestrator import planner
from app.research import entities as E
from app.skills import route
from app.tools.base import safe_filename


# ── 澄清回覆帶回原任務主題 ───────────────────────────────────

def test_clarified_reply_keeps_original_topic():
    prev, _ = planner.understand("幫我研究政大禪學社的茶會文宣")
    assert prev.clarification_pending
    prev.completion_status = "needs_clarification"

    state, _ = planner.understand(
        "我指的是政大的正式社團，請只使用可驗證的官方公開來源研究。", previous=prev,
    )
    assert not state.clarification_pending
    assert "茶會" in state.intent, "原任務主題不能因為澄清回覆而遺失"
    assert any("茶會" in q for q in state.retrieval_queries)


def test_short_registry_club_reply_is_clarified():
    """反問後直接回「北醫禪學社」→ 視為已澄清（換了對象也算回答）。"""
    res = E.resolve("北醫禪學社", awaiting_clarification=True)
    assert res.clarified
    # 只重複講學校不算（反問問的是哪個社團）
    res2 = E.resolve("就是政大", awaiting_clarification=True)
    assert not res2.clarified


# ── artifact 命名血統 ────────────────────────────────────────

def test_single_version_suffix_is_stripped():
    assert safe_filename("期初茶會企劃書_2", ".docx") == safe_filename("期初茶會企劃書", ".docx")
    assert safe_filename("企劃書_2_2", ".md") == safe_filename("企劃書", ".md")


def test_year_like_suffix_is_kept():
    """三位數（學年度）不是版本痕跡，要保留。"""
    assert "114" in safe_filename("社課教材_114", ".md")


def test_md_twins_do_not_overwrite_across_tools(tmp_output_dir, monkeypatch):
    """報名表.gs 與 報名表.docx 的孿生 .md 不能互相蓋掉。"""
    from app.tools.document import create_document
    from app.tools.gform import create_google_form

    doc = create_document(
        filename="報名表",
        markdown="# 報名表\n\n## 活動目標\n蒐集報名。\n\n## 活動流程\n待填",
        destination="local",
        also_markdown=True,
    )
    assert doc["ok"], doc["message"]
    form = create_google_form(
        filename="報名表",
        form_title="報名表",
        description="期初茶會報名",
        questions=[
            {"type": "short_text", "title": "姓名", "required": True},
            {"type": "short_text", "title": "系級", "required": True},
            {"type": "paragraph", "title": "怎麼知道我們的", "required": False},
        ],
        destination="local",
    )
    assert form["ok"], form["message"]

    doc_path = Path(doc["local_path"])
    twin_docs = list(doc_path.parent.glob("*-文字版*.md"))
    assert twin_docs and twin_docs[0].read_text(encoding="utf-8").startswith("# 報名表")
    form_md = Path(form["local_path"]).parent / form["extra_files"][0]
    assert form_md.exists()
    assert "文字版" not in form_md.name, "兩個孿生檔必須是不同名字"


# ── read_artifact 與按需工具 ─────────────────────────────────

def test_read_artifact_exposed_on_any_continuation():
    r = route("接續剛才，把內容再精簡一點")
    assert r.continuation
    assert "read_artifact" in r.tool_names()


def test_ab_test_tool_exposed_on_demand_only():
    r = route("幫我做 AB 測試兩個版本的招生貼文")
    assert r.preferred_tool == "create_social_ab_test"
    assert "create_social_ab_test" in r.tool_names()

    normal = route("幫我寫招生貼文")
    assert "create_social_ab_test" not in normal.tool_names(), "平常不佔 schema"


# ── 活動查詢的學期邊界 ───────────────────────────────────────

def test_resolve_activity_prefers_current_term(tmp_db, monkeypatch, clean_term):
    from app.services import activities as act
    from app.services import current_term as term

    clean_term.write_text("academic_year: '115'\nsemester: 上學期\n", encoding="utf-8")
    term.invalidate()

    uid = tmp_db.ensure_user("u_act", is_local=True)
    old = tmp_db.create_activity(uid, "去年的期初茶會", academic_year="114")
    import time as _t
    _t.sleep(1.1)  # updated_at 秒級精度，確保舊活動較新（考驗學期優先而非時間排序）
    tmp_db.update_activity(old["id"], uid, {"notes": "剛剛才更新過"})
    current = tmp_db.create_activity(uid, "本學期期初茶會", academic_year="115")

    picked = act.resolve_activity(tmp_db, uid)
    assert picked is not None and picked["id"] == current["id"], "無參數 fallback 要優先本學期活動"
    term.invalidate()


def test_list_activities_filters_by_academic_year(tmp_db):
    uid = tmp_db.ensure_user("u_act2", is_local=True)
    tmp_db.create_activity(uid, "114 茶會", academic_year="114")
    tmp_db.create_activity(uid, "115 茶會", academic_year="115")
    rows = tmp_db.list_activities(uid, academic_year="115")
    assert [r["name"] for r in rows] == ["115 茶會"]


# ── 前端靜態契約 ─────────────────────────────────────────────

APP_JS = (Path(__file__).resolve().parent.parent / "app" / "static" / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (Path(__file__).resolve().parent.parent / "app" / "static" / "index.html").read_text(encoding="utf-8")


def test_task_dock_status_has_no_precedence_bug():
    assert '"狀態：" + ({' not in APP_JS, "＋先於‖的優先序 bug 不得回歸"
    assert "dockStatus" in APP_JS


def test_version_label_never_lies_final():
    assert '"最終版"' not in APP_JS, "第 N 版不能被叫成最終版"


def test_clarify_ig_option_fills_input_instead_of_sending():
    assert 'text.endsWith("@")' in APP_JS
    assert "請補上帳號後送出" in APP_JS


def test_attachment_status_lives_in_composer():
    composer = INDEX_HTML.split('id="composer"', 1)[1]
    assert 'id="attachment-status"' in composer.split("</footer>")[0]
