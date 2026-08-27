"""視覺化 AI 任務工作台的前端與穩定檔名契約。"""

from __future__ import annotations

from pathlib import Path

from app.tools.base import safe_filename, unique_path


ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "app" / "static" / "style.css").read_text(encoding="utf-8")
HTML = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")


def test_preflight_is_one_question_at_a_time_and_has_summary_gate():
    assert "draft.questions[draft.current]" in JS
    assert "第 \" + (draft.current + 1) + \" 題／共" in JS
    for label in ("跳過此題", "返回上一題", "確認開始", "直接用預設值", "取消任務"):
        assert label in JS or label in HTML
    assert "runTask(preflightPrompt" in JS


def test_question_card_supports_required_control_kinds():
    for kind in ("single", "multi", "date", "time", "location", "people", "tone", "platform"):
        assert kind in JS
    assert 'role", question.type === "multi" ? "checkbox" : "radio"' in JS
    assert "其他（自由輸入）" in JS


def test_workbench_cards_and_human_tool_labels_exist():
    for label in ("任務理解", "任務摘要", "正在製作", "產出結果", "接下來你可以"):
        assert label in JS
    # 工具中文名與後端 app/tools/__init__.py LABELS 對齊（第五階段）
    for label in ("建立貼文草稿", "建立輪播草稿", "查找社團資料", "檢查內容", "自動修正", "產出檔案"):
        assert label in JS


def test_result_preview_and_next_actions_exist():
    for label in (
        "開啟完整預覽", "複製內容", "下載檔案", "修改這份", "重新產生",
        "轉成輪播", "轉成 Reels", "產生簡報", "建立活動待辦", "開始新任務",
    ):
        assert label in JS
    assert "第 \" + (idx + 1) + \" 頁／共" in JS
    assert "修改單頁" in JS and "重新產生單頁" in JS


def test_mobile_and_accessibility_contracts_are_explicit():
    assert "@media (max-width: 639px)" in CSS
    assert "prefers-reduced-motion" in CSS
    assert "aria-live" in HTML
    assert "aria-checked" in JS
    assert "ArrowLeft" in JS and "ArrowRight" in JS
    assert ".ai-orb.is-thinking" in CSS and ".ai-orb.is-error" in CSS


def test_filename_cleanup_removes_storage_suffixes():
    assert safe_filename("115_上_期初茶會_IG貼文草稿_2_2.md", ".md") == "115-1-期初茶會-IG貼文草稿.md"
    assert safe_filename("招生貼文_final_final.md", ".md") == "招生貼文.md"
    assert safe_filename("招生貼文_修正版2.md", ".md") == "招生貼文.md"


def test_unique_versions_keep_the_same_visible_basename(tmp_path: Path):
    first = unique_path(tmp_path, "115-1-期初茶會-IG貼文草稿.md")
    first.write_text("v1", encoding="utf-8")
    second = unique_path(tmp_path, "115-1-期初茶會-IG貼文草稿.md")
    assert second.name == first.name
    assert second.parent != first.parent
    second.write_text("v2", encoding="utf-8")
    assert first.read_text(encoding="utf-8") == "v1"
    assert second.read_text(encoding="utf-8") == "v2"
