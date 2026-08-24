"""第五階段前端契約：廠商名不外洩、內部名不外洩、模型下拉中文顯示、
輪播不過度切分、preflight ARIA、焦點陷阱堆疊、stop 防護、語音附加、
手機任務浮動按鈕。"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")


def _strip_js_comments(src: str) -> str:
    """粗略移除 // 行註解與 /* */ 區塊註解（足以檢查使用者可見字串）。"""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"^\s*//.*$|(?<=[;{})]) *//[^\"'\n]*$", "", src, flags=re.M)


def _js_function_body(name: str) -> str:
    start = JS.find(f"function {name}(")
    assert start >= 0, f"app.js 找不到 function {name}"
    end = JS.find("\n}", start)
    assert end > start, f"function {name} 沒有正常結尾"
    return JS[start:end]


# ── 1. 廠商名不外洩 ─────────────────────────────────────────

def test_no_vendor_name_in_user_visible_strings():
    assert "fal.ai" not in _strip_js_comments(JS), "使用者可見字串不得出現 fal.ai"
    assert "fal.ai" not in HTML
    assert "AI 視覺" in JS, "改用中文服務名稱（AI 視覺稿服務／AI 視覺服務）"


# ── 2. 模型下拉中文顯示名 ───────────────────────────────────

def test_model_dropdown_uses_chinese_display_names():
    assert "function modelDisplayName" in JS
    for label in (
        "標準（Llama 3.1 70B）", "標準（Llama 3.3 70B）",
        "推理強化（Nemotron 49B）", "長文（Kimi K2）",
        "通用（Qwen 2.5 72B）", "多語（Mixtral 8x22B）", "自訂模型",
    ):
        assert label in JS, f"缺少模型顯示名：{label}"
    body = _js_function_body("fillModels")
    assert "modelDisplayName" in body, "fillModels 要用中文顯示名"
    assert "o.value = m" in body, "送到後端的 value 必須維持原始模型 ID"
    assert "o.title" in body, "原始 ID 要放在 title 屬性"
    assert 'String(m).split("/").pop()' not in body, "不得把原始 ID 當顯示名"


# ── 3. humanLabel 與 TOOL_LABELS ────────────────────────────

def test_humanlabel_fallback_never_shows_internal_english_names():
    body = _js_function_body("humanLabel")
    assert 'return "執行工具"' in body, "查不到對照時 fallback 要顯示「執行工具」"
    assert re.search(r"\\u3400-\\u9fff", body), "要有中文字檢查，純英數識別字不得原樣顯示"


def test_tool_labels_cover_all_backend_tools_with_same_chinese():
    from app import tools

    start = JS.find("const TOOL_LABELS = {")
    assert start >= 0
    block = JS[start : JS.find("};", start)]
    for name in tools.all_names():
        label = tools.LABELS[name]
        assert f'{name}: "{label}"' in block, (
            f"TOOL_LABELS 缺 {name} 或中文與後端不一致（後端：{label}）"
        )


def test_tool_labels_longer_keys_precede_their_prefixes():
    """humanLabel 是子字串取代——update_activity_task 必須排在 update_activity 前。"""
    start = JS.find("const TOOL_LABELS = {")
    block = JS[start : JS.find("};", start)]
    assert 0 < block.find("update_activity_task:") < block.find("update_activity:")


# ── 4. splitCarousel 不過度切分 ─────────────────────────────

def test_split_carousel_only_splits_on_explicit_page_markers():
    body = _js_function_body("splitCarousel")
    assert "第\\s*\\d+\\s*頁" in body, "要認「第 N 頁」分頁標記"
    assert "Page\\s*\\d+" in body, "要認「Page N」分頁標記"
    assert "【\\s*\\d+\\s*】" in body, "要認「【N】」分頁標記"
    assert "const numbered" not in body, "一般編號清單（1. 2. 3.）不得當分頁依據"


# ── 5. preflight ARIA ──────────────────────────────────────

def test_preflight_options_use_radio_checkbox_semantics_not_pressed():
    body = _js_function_body("renderQuestionControl")
    assert "aria-pressed" not in body, "radio/checkbox 選項不得再用 aria-pressed"
    assert '"aria-checked"' in body
    assert '"radio"' in body and '"checkbox"' in body
    assert '"radiogroup"' in body and '"group"' in body
    for key in ("ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"):
        assert key in body, f"選項要支援方向鍵移動焦點：{key}"


# ── 6. 焦點陷阱堆疊 ─────────────────────────────────────────

def test_focus_trap_uses_a_stack_for_nested_modals():
    assert "const trapStack = []" in JS
    assert "trapStack.push" in JS
    assert "trapStack.pop" in JS
    assert "trapCleanup" not in JS, "單槽 trapCleanup 要換成堆疊"
    # 全域 Escape 監聽在陷阱作用中要讓位（Escape 只關最上層）
    assert re.search(r'if \(e\.key !== "Escape"\) return;\s*\n\s*//[^\n]*\n\s*if \(trapStack\.length\) return;', JS)


# ── 7. #stop null 防護 ─────────────────────────────────────

def test_stop_button_access_is_null_guarded():
    assert '$("stop").' not in JS, '#stop 不得未經 null 檢查直接取用'
    assert re.search(r'const stopBtn = \$\("stop"\);', JS)
    assert "if (stopBtn)" in JS


# ── 8. 語音輸入附加而非覆蓋 ─────────────────────────────────

def test_voice_input_appends_instead_of_overwriting():
    idx = JS.find('$("voice-input").addEventListener')
    assert idx >= 0
    block = JS[idx : JS.find("recognition.start()", idx)]
    assert "lastVoiceChunk" in block
    assert "endsWith(lastVoiceChunk)" in block, "只能替換上一次語音附加的片段"
    assert "current + lastVoiceChunk" in block, "語音結果要附加在既有內容之後"
    assert 'const glue' in block, "既有內容與語音之間要補空格"
    assert re.search(r"\$\(\"input\"\)\.value = base \+ words", block) is None, "不得整段覆蓋輸入框"


# ── 9. 手機任務浮動按鈕 ─────────────────────────────────────

def test_mobile_task_fab_exists_and_opens_task_summary():
    m = re.search(r'<button id="task-fab"[^>]*>', HTML)
    assert m, "index.html 要有 #task-fab"
    assert "aria-label" in m.group(0)
    assert "hidden" in m.group(0), "預設隱藏，任務開始後才顯示"
    assert re.search(r'taskFab\.addEventListener\("click", openTaskSummary\)', JS)
    # 顯示與任務側欄同步：任務開始顯示、新任務隱藏
    assert JS.count("fab.hidden = false") >= 1
    assert JS.count("fab.hidden = true") >= 1


def test_css_shows_task_fab_below_960_with_safe_area():
    start = CSS.find("@media (max-width: 959.98px)")
    assert start >= 0, "任務浮動按鈕要在 <960px 顯示"
    block = CSS[start : CSS.find("\n}", CSS.find(".task-fab[hidden]", start))]
    assert ".task-fab" in block
    assert "position: fixed" in block
    assert "var(--safe-bottom)" in block and "var(--safe-right)" in block
    assert ".task-fab[hidden] { display: none; }" in block
    # 桌機仍走側欄：預設不顯示
    assert ".task-fab { display: none; }" in CSS
    # 位置抬高到 composer 之上，避開 #send／#stop
    m = re.search(r"\.task-fab\s*{[^}]*bottom:\s*calc\((\d+)px", block)
    assert m and int(m.group(1)) >= 80, "浮動按鈕要抬高到 composer 之上，不遮 #send/#stop"


# ── 10. 320px 快速按鈕不橫向溢出的既有防線仍在 ───────────────

def test_narrow_viewport_quick_grid_rules_survive():
    assert "@media (max-width: 380px)" in CSS
    assert "@media (max-width: 360px)" in CSS
    assert re.search(r"\.quick-grid button { padding-inline: 5px; }", CSS)
    assert "grid-template-columns: 1fr 1fr" in CSS


# ── grok 第五階段審查的兩個反例 ──────────────────────────────

def test_preflight_selected_state_css_matches_js_attribute():
    """grok 1：JS 改用 aria-checked 後，CSS 若還綁 aria-pressed，
    點了選項畫面完全沒有選取態——而只掃 JS 的契約測試會誤判為通過。"""
    app_js = JS
    css = CSS
    # JS 用哪個屬性標記選取
    assert 'setAttribute("aria-checked"' in app_js
    # 選取態樣式必須綁同一個屬性
    preflight_rules = [line for line in css.splitlines() if ".preflight-options" in line and "aria-" in line]
    assert preflight_rules, "找不到反問卡選項的選取態樣式"
    for rule in preflight_rules:
        assert "aria-checked" in rule, f"選取態樣式仍綁舊屬性：{rule.strip()}"
        assert "aria-pressed" not in rule, f"選取態樣式仍綁 aria-pressed：{rule.strip()}"


def test_focus_trap_replaces_same_root_instead_of_stacking():
    """grok 2：同一層 modal 連開兩次不得再疊一層（會留下 zombie 陷阱，
    關閉後 Tab 可跑出仍開著的外層抽屜）。"""
    app_js = JS
    trap = app_js.split("function trapFocus(", 1)[1].split("\nfunction ", 1)[0]
    assert "outer.root === root" in trap, "trapFocus 必須偵測同一層重複開啟"
    assert "trapStack.pop()" in trap, "同一層重複開啟要取代而不是疊加"


def test_open_term_guards_against_double_open():
    app_js = JS
    assert "termLoading" in app_js
    open_term = app_js.split("async function openTerm()", 1)[1].split("\nfunction closeTerm", 1)[0]
    assert "if (termLoading" in open_term, "請求進行中要擋掉重複開啟"
    assert "finally" in open_term, "旗標一定要還原，否則之後永遠打不開"
