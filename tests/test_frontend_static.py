"""前端靜態契約：授權碼不落地、手機優先、閘門用詞、渲染契約存在。"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")


def _block(src: str, element_id: str) -> str:
    m = re.search(rf'id="{re.escape(element_id)}"[^>]*>(.*)', src, re.S)
    assert m, f"找不到 #{element_id}"
    text = m.group(1)
    depth = 1
    i = 0
    while i < len(text) and depth:
        nxt_open = text.find("<div", i)
        nxt_close = text.find("</div>", i)
        if nxt_close < 0:
            break
        if nxt_open >= 0 and nxt_open < nxt_close:
            depth += 1
            i = nxt_open + 4
        else:
            depth -= 1
            i = nxt_close + 6
    return text[: i - 6] if i >= 6 else text


def test_html_lang_and_viewport():
    assert re.search(r'<html[^>]*lang="zh-Hant"', HTML)
    assert "viewport-fit=cover" in HTML
    assert re.search(r'<meta[^>]+name="viewport"', HTML)


def test_gate_copy_uses_access_phrase_not_account_words():
    gate = _block(HTML, "gate")
    assert "授權碼" in gate
    assert "進入工作台" in gate
    assert "登入" not in gate
    assert "註冊" not in gate
    assert "帳號" not in gate


def test_quick_actions_at_most_six():
    block = _block(HTML, "quick-actions")
    buttons = re.findall(r"<button\b", block)
    assert 1 <= len(buttons) <= 6


def test_index_has_no_hardcoded_model_names():
    lowered = HTML.lower()
    forbidden = (
        "llama",
        "gpt-",
        "gpt4",
        "claude",
        "gemini",
        "mixtral",
        "qwen",
        "kimi",
        "nemotron",
        "nvidia/",
        "grok-",
    )
    for name in forbidden:
        assert name not in lowered, f"index.html 不該寫死模型名 {name}"


def test_js_does_not_touch_web_storage_or_document_cookie():
    assert "localStorage" not in JS
    assert "sessionStorage" not in JS
    assert "document.cookie" not in JS


def test_css_has_motion_color_and_min_tap_rules():
    assert "prefers-reduced-motion" in CSS
    assert "prefers-color-scheme" in CSS
    assert re.search(r"min-height:\s*44px", CSS)
    assert re.search(r"min-width:\s*44px", CSS)


def test_app_shell_is_flex_so_chat_can_scroll():
    assert re.search(r"#app\s*\{[^}]*display:\s*flex", CSS, re.S)
    assert "100dvh" in CSS
    assert "visualViewport" in JS


def test_social_render_contract_present():
    for lang in (
        "ig-post",
        "ig-carousel",
        "ig-story",
        "reels-script",
        "content-calendar",
        "ab-test",
        "image-prompt",
        "video-prompt",
    ):
        assert lang in JS
    for title in ("其他學校怎麼做", "為什麼可能有效", "淡江可以怎麼改良", "哪些內容不能直接照抄"):
        assert title in JS


def test_guided_input_and_preflight_contract_present():
    for label in ("問 AI", "直接生成", "製作範本", "執行計畫", "填入：做網宣"):
        assert label in HTML
    for label in ("確認開始", "先跳過", "返回修改", "在開始前，還需要確認"):
        assert label in HTML or label in JS
    assert "questions: questions.slice(0, 3)" in JS
    assert "preflightPrompt" in JS


def test_progress_controls_do_not_expose_internal_tool_names_in_ui_labels():
    for label in ("已理解需求", "確認必要資料", "產生初稿", "檢查內容", "產出完成"):
        assert label in JS
    for label in ("停止生成", "暫停任務", "重試失敗步驟", "查看任務摘要"):
        assert label in JS
    assert "TOOL_PHASES" in JS


def test_accessibility_contract_for_new_sheets_and_composer():
    assert 'aria-label="任務需求"' in HTML
    assert 'aria-modal="true"' in HTML
    assert "focus-visible" in CSS
    assert "closePreflight" in JS


def test_attachment_entry_supports_images_without_persisting_them_in_ui_state():
    assert "image/jpeg" in HTML and "image/png" in HTML and "image/webp" in HTML
    assert "只會傳給這次任務，不會保存" in JS
    assert "fal.ai 視覺服務" in JS
    assert "attachments" in JS


def test_visual_output_uses_fal_endpoint_with_a_download_action():
    assert "/api/visual/generate" in JS
    assert "生成視覺稿" in JS
    assert "開啟並下載" in JS


def test_auth_errors_use_backend_detail():
    assert "readDetail" in JS
    assert "429" in JS
    assert "系統忙碌中，請稍後再試" in JS


@pytest.mark.parametrize("forbidden", ["RAG", "Token"])
def test_home_screen_markup_hides_internal_jargon(forbidden):
    home = _block(HTML, "home")
    gate = _block(HTML, "gate")
    assert forbidden not in home
    assert forbidden not in gate
