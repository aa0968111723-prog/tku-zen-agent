"""手機版（320–430px）與無障礙驗收。

分兩層：
  · 靜態層（永遠會跑）—— CSS/HTML 的手機與無障礙契約
  · 實跑層（需要 node + playwright）—— 真的開瀏覽器量寬度、焦點與溢出，
    見 scripts/mobile_check.mjs。沒有環境時 skip，不會讓 CI 假綠。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
JS = (STATIC / "app.js").read_text(encoding="utf-8")

WIDTHS = [320, 360, 390, 430]


# ── 靜態契約 ─────────────────────────────────────────────────

def test_viewport_supports_safe_areas():
    assert "viewport-fit=cover" in HTML
    for var in ("--safe-bottom", "--safe-top"):
        assert var in CSS


def test_no_fixed_widths_that_break_320px():
    """不可以有大於 320px 的固定寬度，否則最小手機會橫向滾動。"""
    offenders = []
    for m in re.finditer(r"(?<!max-)(?<!min-)width:\s*(\d+)px", CSS):
        if int(m.group(1)) > 320:
            offenders.append(m.group(0))
    assert not offenders, f"固定寬度超過 320px：{offenders}"


def test_touch_targets_are_44px():
    assert re.search(r"min-height:\s*44px", CSS)
    assert "touch-action: manipulation" in CSS


def test_focus_visible_styles_exist():
    assert ":focus-visible" in CSS


def test_dark_and_high_contrast_modes():
    assert "prefers-color-scheme: dark" in CSS
    assert "prefers-contrast: more" in CSS
    assert "prefers-reduced-motion: reduce" in CSS


def test_long_content_wraps_and_scrolls():
    assert "overflow-wrap: anywhere" in CSS or "word-break" in CSS
    assert "overflow-x: hidden" in CSS


def test_keyboard_aware_viewport_handling():
    """visualViewport 監聽：鍵盤彈出時 modal 不會被蓋住。"""
    assert "visualViewport" in JS
    assert "--app-height" in JS and "--app-height" in CSS


def test_dialogs_have_aria_roles():
    for dialog_id in ("settings", "session-sheet", "term-modal"):
        block = re.search(rf'id="{dialog_id}"(.{{0,500}})', HTML, re.DOTALL)
        assert block, dialog_id
        assert 'role="dialog"' in block.group(1) or 'aria-modal' in block.group(1)


def test_focus_trap_and_escape_handling():
    assert "function trapFocus" in JS
    assert 'e.key === "Escape"' in JS


def test_status_region_is_live():
    assert 'role="status"' in HTML and 'aria-live="polite"' in HTML


def test_all_icon_buttons_have_aria_labels():
    for m in re.finditer(r'<button[^>]*class="[^"]*icon-btn[^"]*"[^>]*>', HTML):
        assert "aria-label" in m.group(0), m.group(0)


def test_send_button_is_accessible():
    m = re.search(r'<button id="send"[^>]*>', HTML)
    assert m and "aria-label" in m.group(0)


def test_decorative_svgs_are_hidden_from_screen_readers():
    # 只看 <body> 裡真的會渲染的 SVG；<head> 的 favicon data URI 不算
    body = HTML.split("<body>", 1)[1]
    for svg in re.finditer(r"<svg[^>]*>", body):
        assert 'aria-hidden="true"' in svg.group(0), svg.group(0)


# ── 實跑層 ───────────────────────────────────────────────────

def _playwright_available() -> bool:
    if not shutil.which("node"):
        return False
    try:
        root = subprocess.run(
            ["npm", "root", "-g"], capture_output=True, text=True, timeout=30, check=False
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False
    return (Path(root) / "playwright").is_dir() or (ROOT / "node_modules" / "playwright").is_dir()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.skipif(not _playwright_available(), reason="沒有 node + playwright，跳過實跑檢查")
def test_mobile_viewports_end_to_end(tmp_path):
    """在 320/360/390/430 實際跑一次，任何一項失敗都算失敗。"""
    port = _free_port()
    env = {
        **os.environ,
        "AUTH_MODE": "local",
        "APP_ACCESS_TOKEN": "",
        "DB_PATH": str(tmp_path / "mobile.sqlite3"),
        "OUTPUT_DIR": str(tmp_path / "outputs"),
    }
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(60):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    break
            except OSError:
                time.sleep(0.5)
        else:
            pytest.fail("伺服器沒有啟動")

        out_dir = tmp_path / "shots"
        result = subprocess.run(
            ["node", str(ROOT / "scripts" / "mobile_check.mjs"), base, str(out_dir)],
            capture_output=True, text=True, timeout=600, cwd=str(ROOT), check=False,
        )
        report = out_dir / "results.json"
        if report.exists():
            checks = json.loads(report.read_text(encoding="utf-8"))
            failed = [c for c in checks if not c["ok"]]
            assert not failed, "手機版檢查失敗：" + "；".join(
                f"{c['name']}（{c['detail']}）" for c in failed
            )
            for w in WIDTHS:
                assert any(f"{w}×" in c["name"] for c in checks), f"沒有跑 {w}px"
        assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]
    finally:
        server.terminate()
        server.wait(timeout=30)
