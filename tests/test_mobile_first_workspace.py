"""手機優先工作台：資訊架構、Tabs、composer、safe-area、任務控制契約。

實跑層沿用 tests/test_mobile_accessibility.py 的 Playwright 閘門：
沒有 node + playwright 時 skip，並在訊息標示 BLOCKED_BY_EXTERNAL_DEPENDENCY。
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from app import main

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")


def test_workspace_is_single_primary_surface():
    assert 'id="workbench"' in HTML
    assert 'role="tablist"' in HTML
    assert HTML.count('role="tabpanel"') == 1
    assert 'id="composer"' in HTML
    home = HTML.split('id="home"', 1)[1].split('id="guide-ig"', 1)[0]
    assert home.count("class=\"primary\"") == 0


def test_mode_tabs_have_accessible_names_and_ids():
    for mode, label in (("ask", "問 AI"), ("generate", "直接生成"), ("template", "製作範本"), ("plan", "執行計畫")):
        assert f'data-mode="{mode}"' in HTML
        assert label in HTML
        assert f'id="tab-{mode}"' in HTML


def test_legacy_mode_paths_are_wired_in_js_and_served():
    for path in ("/generate", "/templates", "/prompt-library"):
        assert path in JS
    assert "searchParams.set(\"mode\"" in JS or "searchParams.set('mode'" in JS or 'url.searchParams.set("mode", mode)' in JS
    with TestClient(main.app) as client:
        for path in ("/", "/generate", "/templates", "/prompt-library"):
            resp = client.get(path)
            assert resp.status_code == 200, path
            assert "問 AI" in resp.text
            assert "viewport-fit=cover" in resp.text


def test_topbar_is_compact_and_project_name_ellipsis():
    assert 'id="project-name"' in HTML
    assert "text-overflow: ellipsis" in CSS
    assert "draft-chip" in CSS
    assert "目前為草稿模式——不會自動發布任何內容" in HTML
    assert 'id="more-menu-btn"' in HTML
    assert "視覺資料庫" in HTML
    assert 'id="reset"' in HTML


def test_ai_orb_states_cover_required_statuses():
    for status in ("idle", "queued", "thinking", "searching", "running", "asking", "complete", "error"):
        assert f"is-{status}" in CSS or f'"{status}"' in JS
    for label in ("等待中", "排隊中", "正在思考", "正在搜尋", "正在執行任務", "需要你確認", "任務完成", "需要處理"):
        assert label in JS
    assert "prefers-reduced-motion: reduce" in CSS


def test_composer_is_keyboard_and_safe_area_aware():
    assert "100dvh" in CSS
    assert "env(safe-area-inset-bottom" in CSS
    assert "visualViewport" in JS
    assert "--kb-inset" in JS
    assert 'id="composer-plus"' in HTML
    assert 'id="send"' in HTML and 'id="stop"' in HTML
    assert "font-size: 16px" in CSS
    assert "var(--composer-max)" in CSS
    assert re.search(r"min-height:\s*2\.8em", CSS) or "rows=\"2\"" in HTML


def test_quick_actions_first_screen_has_at_most_three():
    block = HTML.split('id="quick-actions"', 1)[1].split("</div>", 1)[0]
    assert len(re.findall(r"<button\b", block)) <= 3
    assert "找資料" in block
    assert "做網宣" in block
    assert "更多" in block


def test_task_controls_include_stop_retry_continue():
    assert 'controlTask("cancel"' in JS
    assert 'controlTask("retry"' in JS
    assert 'controlTask("resume"' in JS
    assert 'el("button", "primary", "繼續")' in JS or "繼續" in JS
    assert "重試" in JS
    assert "停止生成" in JS
    for kind in ("queued", "running", "succeeded", "failed"):
        assert f"is-{kind}" in CSS


def test_failure_empty_permission_quota_and_provider_have_ui():
    assert "目前沒有進行中的任務" in JS
    assert "權限不足" in JS
    assert "額度不足" in JS
    assert "AI 服務暫時無法使用" in JS
    assert "empty-state" in CSS


def test_sheets_support_escape_history_and_focus_return():
    assert "function pushSheetState" in JS
    assert "popstate" in JS
    assert "restoreFocus" in JS
    assert "function trapFocus" in JS
    assert 'e.key === "Escape"' in JS
    for sheet in ("composer-plus-sheet", "app-menu-sheet", "more-actions-sheet", "task-summary-sheet"):
        assert f'id="{sheet}"' in HTML
        assert "aria-modal" in HTML


def test_glow_is_reserved_for_orb_status_and_primary_cta():
    assert "box-shadow: var(--glow-cyan-soft)" in CSS
    # 次要快速操作不得使用強烈霓虹陰影當預設
    start = CSS.find(".quick-row button")
    chunk = CSS[start : start + 500]
    assert "glow-cyan" not in chunk


def test_touch_targets_stay_at_least_44px_on_narrow_screens():
    narrow = CSS[CSS.find("@media (max-width: 380px)") : CSS.find("@media (max-width: 360px)")]
    assert "min-width: 38px" not in narrow
    assert "min-width: 44px" in CSS
    assert "min-height: 44px" in CSS
