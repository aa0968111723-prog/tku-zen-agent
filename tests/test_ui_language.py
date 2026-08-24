"""使用者可見文字一律繁體中文，且不得出現內部英文工具名。

掃描範圍：index.html 的可見文字、app.js 裡會顯示給使用者的字串、
以及後端送到前端的工具標籤與錯誤訊息。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
JS = (STATIC / "app.js").read_text(encoding="utf-8")

# 允許出現在介面上的英文（專有名詞與平台名）
ALLOWED_ENGLISH = {
    "Instagram", "IG", "ig", "Reels", "Google", "AI", "A/B", "PA", "OK",
    "Word", "Excel", "PPT", "Markdown", "md", "docx", "xlsx", "pptx", "JPG", "PNG", "WebP", "MB",
}


def _visible_html_text() -> list[str]:
    """抽出 index.html 裡使用者看得到的文字節點與 aria-label / placeholder。"""
    body = re.sub(r"<script.*?</script>", "", HTML, flags=re.DOTALL)
    body = re.sub(r"<svg.*?</svg>", "", body, flags=re.DOTALL)
    texts = [t.strip() for t in re.findall(r">([^<>]+)<", body) if t.strip()]
    texts += re.findall(r'aria-label="([^"]+)"', body)
    texts += re.findall(r'placeholder="([^"]+)"', body)
    return [t for t in texts if t and not t.startswith("&")]


def _has_chinese(text: str) -> bool:
    return bool(re.search(r"[一-鿿]", text))


# ── HTML 可見文字 ────────────────────────────────────────────

def test_all_visible_html_text_is_chinese():
    offenders = []
    for text in _visible_html_text():
        stripped = text
        for allowed in ALLOWED_ENGLISH:
            stripped = stripped.replace(allowed, "")
        leftover = re.sub(r"[^A-Za-z]", "", stripped)
        if leftover and not _has_chinese(text):
            offenders.append(text)
    assert not offenders, f"以下介面文字不是繁體中文：{offenders}"


# 只列繁體中文絕不會使用的簡化字。刻意不含兩邊通用的字（查、后…），
# 也不含繁體本來就有的字形，否則會誤判。
SIMPLIFIED_ONLY = "别务动开关来种产间断继续录设备网响应权无对话会检时给验证结项现将过发"


@pytest.mark.parametrize("name", ["index.html", "app.js"])
def test_no_simplified_chinese_in_ui(name: str):
    source = HTML if name == "index.html" else JS
    banned = sorted({c for c in SIMPLIFIED_ONLY if c in source})
    assert not banned, f"{name} 出現簡體字：{banned}"


# ── 工具名稱 ─────────────────────────────────────────────────

def test_backend_tool_labels_are_all_chinese():
    from app import tools

    for name in tools.all_names():
        label = tools.LABELS[name]
        assert _has_chinese(label), f"{name} 的顯示名稱不是中文：{label}"
        assert label != name, f"{name} 直接把英文內部名當顯示名稱"


def test_frontend_translates_internal_tool_names():
    """前端 humanLabel 要把內部名轉成中文；至少涵蓋所有網宣工具。"""
    assert "function humanLabel" in JS
    for tool in ("create_social_post", "create_social_carousel", "create_social_story", "create_reels_script"):
        assert tool in JS, f"TOOL_LABELS 缺少 {tool} 的對照"


def test_progress_and_stop_phrases_are_chinese():
    for phrase in ["已停止生成", "自動修正", "檢查內容", "查找社團資料"]:
        assert phrase in JS, f"缺少中文字樣：{phrase}"


# ── 錯誤訊息 ─────────────────────────────────────────────────

def test_frontend_error_messages_are_chinese():
    for phrase in [
        "系統忙碌中，請稍後再試", "連線中斷", "連線逾時",
        "伺服器發生錯誤", "沒有權限執行這個操作",
    ]:
        assert phrase in JS


def test_backend_error_details_are_chinese():
    from app.services import auth, permissions

    for detail in (
        auth.UNAUTHORIZED_DETAIL, auth.INVALID_TOKEN_DETAIL, auth.RATE_LIMIT_DETAIL,
        permissions.DRAFT_ONLY_DETAIL, permissions.NEED_APPROVE_DETAIL,
        permissions.NEED_SPEND_DETAIL, permissions.NEED_MANAGE_DETAIL,
        permissions.NEED_CONFIRM_DETAIL,
    ):
        assert _has_chinese(detail), f"錯誤訊息不是中文：{detail}"


def test_main_api_details_are_chinese():
    main_src = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    details = re.findall(r'detail="([^"]+)"', main_src)
    assert details
    for d in details:
        assert _has_chinese(d), f"API 錯誤訊息不是中文：{d}"


# ── 快速操作行為一致（方案 B：填入式按鈕明確標示）────────────

def test_quick_actions_declare_fill_behavior():
    buttons = re.findall(r'<button type="button" data-action="([^"]+)">([^<]+)</button>', HTML)
    assert len(buttons) == 6, f"應該有 6 顆快速按鈕，找到 {len(buttons)}"
    for action, label in buttons:
        assert _has_chinese(label), label
        if action == "continue":
            assert "繼續" in label
        else:
            assert label.startswith("填入："), f"填入式按鈕要明確標示行為：{label}"


# ── 不顯示推理過程 ───────────────────────────────────────────

def test_no_chain_of_thought_surface():
    for banned in ["思考過程", "推理過程", "chain of thought", "reasoning:"]:
        assert banned not in JS
    prompt_src = (ROOT / "app" / "orchestrator" / "prompt.py").read_text(encoding="utf-8")
    assert "不要輸出你的推理過程" in prompt_src


# ── 草稿模式提示 ─────────────────────────────────────────────

def test_draft_mode_banner_present():
    assert "目前為草稿模式——不會自動發布任何內容" in HTML
