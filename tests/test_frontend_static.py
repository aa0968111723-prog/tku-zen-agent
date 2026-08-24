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


# ── 假可信度防線（對抗稽核 46／47／48） ──────────────────────

def _js_function_body(name: str) -> str:
    """取出 app.js 頂層 function 的原始碼（到行首的 } 為止）。"""
    start = JS.find(f"function {name}(")
    assert start >= 0, f"app.js 找不到 function {name}"
    end = JS.find("\n}", start)
    assert end > start, f"function {name} 沒有正常結尾"
    return JS[start:end]


def test_text_url_links_carry_no_credibility_markers():
    """46：訊息文字裡的網址只能變成一般連結；驗證狀態標示只能來自後端 source_cards 事件。"""
    body = _js_function_body("renderTextSourceLinks")
    assert 'el("a", null, url)' in body, "連結必須是無樣式的一般 <a>"
    assert "source-card" not in body, "不得替訊息內網址套用來源卡樣式"
    assert "可信度" not in body, "不得替訊息內網址加可信度說明文字"
    assert "來源日期" not in body, "不得替訊息內網址捏造來源日期"
    assert "st-" not in body, "不得替訊息內網址套用任何狀態章 class"


def test_research_cards_require_backend_research_events():
    """47：只有本輪真的收過 source_cards / research_status 事件，訊息才可拆成研究卡樣式。"""
    assert "researchEventsSeen ? splitResearch(" in JS, "splitResearch 呼叫點必須先檢查旗標"
    assert re.search(r'case "source_cards":\s+researchEventsSeen = true;', JS), "source_cards 事件要設旗標"
    assert re.search(r'case "research_status":\s+researchEventsSeen = true;', JS), "research_status 事件要設旗標"
    assert "researchEventsSeen = false;" in JS, "新任務開始時要重置旗標"
    body = _js_function_body("renderResearch")
    assert "lastResearchStatus" in body, "研究卡要引用後端研究狀態"
    assert "研究狀態" in body, "研究卡頂部要顯示研究狀態標籤"


def test_task_completed_green_requires_allow_verdict():
    """48：task_completed 只有審核放行且研究狀態完成／內部才算全綠成功。"""
    start = JS.find('case "task_completed": {')
    assert start >= 0, "app.js 找不到 task_completed 分支"
    body = JS[start:JS.find("break;", start)]
    assert 'ev.verdict === "allow"' in body, "全綠判斷式必須含 verdict"
    okdone_line = next(line for line in body.splitlines() if "const okDone" in line)
    assert "partially_verified" not in okdone_line, "partially_verified 不得直接算成功全綠"
    assert 'rs === "partially_verified" || rs === "unverified"' in body, "部分驗證／尚未驗證要獨立成黃色層級"
    assert 'setOrb("asking"' in body, "部分驗證要用既有黃色狀態呈現"
    assert 'setOrb("error")' in body, "未通過驗證要用既有紅色狀態呈現"
