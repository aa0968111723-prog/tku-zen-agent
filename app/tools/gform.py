"""Google 表單產生器。

Google 表單沒有可直接呼叫的建立 API（Forms API 需要另外開 GCP 專案與 OAuth），
所以這裡改走 Apps Script：產生一支 .gs 腳本，使用者貼到 script.google.com
按一次執行，表單 + 回覆試算表就建好了，而且會直接歸在自己的 Google 帳號下。

同時附一份 .md 說明，寫清楚題目內容，方便先審過再送出。
"""

from __future__ import annotations

import json
from typing import Any

from .base import Artifact, dated_dir, deliver, safe_filename, unique_path

TEXT_MIME = "text/plain"

TYPES = {
    "short_text": "簡答",
    "paragraph": "段落",
    "multiple_choice": "單選",
    "checkbox": "複選",
    "dropdown": "下拉式選單",
    "scale": "線性刻度",
    "date": "日期",
    "time": "時間",
    "email": "電子郵件",
}


def _js(value: Any) -> str:
    """安全地轉成 JS 字面值。"""
    return json.dumps(value, ensure_ascii=False)


def _normalize_questions(questions: Any) -> list[dict]:
    if isinstance(questions, str):
        try:
            questions = json.loads(questions)
        except Exception:  # noqa: BLE001
            return []
    if isinstance(questions, dict):
        questions = [questions]
    if not isinstance(questions, list):
        return []

    out: list[dict] = []
    for q in questions:
        if isinstance(q, str):
            out.append({"type": "short_text", "title": q, "required": False, "options": []})
            continue
        if not isinstance(q, dict):
            continue
        qtype = str(q.get("type") or "short_text").strip().lower()
        if qtype not in TYPES:
            qtype = "multiple_choice" if q.get("options") else "short_text"
        options = q.get("options") or []
        if isinstance(options, str):
            options = [o.strip() for o in options.replace("\n", ",").split(",") if o.strip()]
        out.append(
            {
                "type": qtype,
                "title": str(q.get("title") or q.get("question") or "未命名題目"),
                "help": str(q.get("help") or q.get("description") or ""),
                "required": bool(q.get("required", False)),
                "options": [str(o) for o in options],
                "scale_min": int(q.get("scale_min", 1) or 1),
                "scale_max": int(q.get("scale_max", 5) or 5),
                "scale_min_label": str(q.get("scale_min_label") or ""),
                "scale_max_label": str(q.get("scale_max_label") or ""),
            }
        )
    return out


def _emit_question(q: dict) -> str:
    t = q["type"]
    title, help_text, required = _js(q["title"]), _js(q["help"]), "true" if q["required"] else "false"
    opts = _js(q["options"])

    if t in {"multiple_choice", "checkbox", "dropdown"}:
        method = {"multiple_choice": "addMultipleChoiceItem", "checkbox": "addCheckboxItem", "dropdown": "addListItem"}[t]
        if not q["options"]:
            return (
                f"  form.addTextItem().setTitle({title}).setHelpText({help_text}).setRequired({required});"
                f"  // 原本要做成{TYPES[t]}，但沒有給選項"
            )
        return (
            f"  form.{method}()\n"
            f"    .setTitle({title})\n"
            f"    .setHelpText({help_text})\n"
            f"    .setChoiceValues({opts})\n"
            f"    .setRequired({required});"
        )
    if t == "paragraph":
        return f"  form.addParagraphTextItem().setTitle({title}).setHelpText({help_text}).setRequired({required});"
    if t == "scale":
        return (
            f"  form.addScaleItem()\n"
            f"    .setTitle({title})\n"
            f"    .setHelpText({help_text})\n"
            f"    .setBounds({q['scale_min']}, {q['scale_max']})\n"
            f"    .setLabels({_js(q['scale_min_label'])}, {_js(q['scale_max_label'])})\n"
            f"    .setRequired({required});"
        )
    if t == "date":
        return f"  form.addDateItem().setTitle({title}).setHelpText({help_text}).setRequired({required});"
    if t == "time":
        return f"  form.addTimeItem().setTitle({title}).setHelpText({help_text}).setRequired({required});"
    if t == "email":
        return (
            f"  form.addTextItem()\n"
            f"    .setTitle({title})\n"
            f"    .setHelpText({help_text})\n"
            f"    .setValidation(FormApp.createTextValidation().requireTextIsEmail().build())\n"
            f"    .setRequired({required});"
        )
    return f"  form.addTextItem().setTitle({title}).setHelpText({help_text}).setRequired({required});"


def _build_script(form_title: str, description: str, questions: list[dict], collect_email: bool) -> str:
    body = "\n\n".join(_emit_question(q) for q in questions)
    return f"""/**
 * 淡江大學領袖禪學社 —— 自動建立 Google 表單
 * 表單名稱：{form_title}
 *
 * 使用方式：
 *   1. 開 https://script.google.com/home/projects/create
 *   2. 把整份程式碼貼進去，覆蓋原本的 myFunction
 *   3. 按上方「執行」（第一次會要求授權，選你的 Google 帳號 →
 *      「進階」→「前往...（不安全）」→ 允許。這是因為腳本沒有經過
 *      Google 驗證，但它只會在你自己的帳號建立表單）
 *   4. 執行完成後看「執行紀錄」，裡面有表單的編輯連結與填寫連結
 */

function 建立表單() {{
  var form = FormApp.create({_js(form_title)});
  form.setDescription({_js(description)});
  form.setCollectEmail({str(collect_email).lower()});
  form.setProgressBar(true);
  form.setAllowResponseEdits(true);
  form.setConfirmationMessage('已收到你的填寫，感謝！我們會盡快與你聯絡。');

{body}

  // 建立對應的回覆試算表
  var ss = SpreadsheetApp.create({_js(form_title + "（回覆）")});
  form.setDestination(FormApp.DestinationType.SPREADSHEET, ss.getId());

  Logger.log('表單編輯連結：' + form.getEditUrl());
  Logger.log('表單填寫連結：' + form.getPublishedUrl());
  Logger.log('回覆試算表：' + ss.getUrl());
}}

// Apps Script 預設會執行第一個函式，這裡對應過去
function myFunction() {{
  建立表單();
}}
"""


def _build_readme(form_title: str, description: str, questions: list[dict], collect_email: bool) -> str:
    lines = [
        f"# {form_title}",
        "",
        description or "_（沒有填寫表單說明）_",
        "",
        f"- 收集填答者信箱：{'是' if collect_email else '否'}",
        f"- 題目數：{len(questions)}",
        "",
        "## 怎麼把它變成真的 Google 表單",
        "",
        "1. 開 <https://script.google.com/home/projects/create>",
        "2. 把同名的 `.gs` 檔內容整份貼進去（覆蓋原本的 `myFunction`）",
        "3. 按上方「**執行**」",
        "4. 第一次會跳授權：選你的 Google 帳號 →「進階」→「前往 未命名的專案（不安全）」→「允許」",
        "   （會出現這個警告，是因為腳本沒經過 Google 官方驗證；它只會在你自己的帳號裡建表單）",
        "5. 執行完，下方「**執行紀錄**」會印出三個連結：表單編輯、表單填寫、回覆試算表",
        "",
        "## 題目一覽",
        "",
        "| # | 題型 | 題目 | 必填 | 選項 |",
        "|---|---|---|---|---|",
    ]
    for i, q in enumerate(questions, 1):
        opts = "／".join(q["options"]) if q["options"] else ""
        if q["type"] == "scale":
            opts = f"{q['scale_min']}–{q['scale_max']}"
        lines.append(
            f"| {i} | {TYPES.get(q['type'], q['type'])} | {q['title']} | "
            f"{'是' if q['required'] else ''} | {opts} |"
        )
    return "\n".join(lines) + "\n"


def create_google_form(
    filename: str,
    form_title: str,
    questions: Any,
    description: str = "",
    collect_email: bool = False,
    destination: str | None = None,
) -> dict:
    parsed = _normalize_questions(questions)
    if not parsed:
        return {
            "ok": False,
            "message": "questions 是空的或格式不對。請傳陣列，每題是 "
            '{"type": "short_text|paragraph|multiple_choice|checkbox|dropdown|scale|date|time|email", '
            '"title": "題目", "required": true/false, "options": ["選項1","選項2"]}。',
        }

    base = safe_filename(filename, "").rstrip(".") or "表單"
    folder = dated_dir()

    gs_path = unique_path(folder, f"{base}.gs")
    gs_path.write_text(_build_script(form_title, description, parsed, collect_email), encoding="utf-8")

    # 孿生說明檔走 unique_path＋專屬字尾：直接 with_suffix 會跟同 stem 的
    # 文件工具 .md 互相覆寫（稽核不可靠 #36）
    md_path = unique_path(gs_path.parent, f"{base}-表單說明.md")
    md_path.write_text(_build_readme(form_title, description, parsed, collect_email), encoding="utf-8")

    art = Artifact(
        filename=gs_path.name,
        summary=f"表單「{form_title}」共 {len(parsed)} 題。",
        details=[
            f"　· 說明與題目一覽：{md_path.name}",
            "　· 下一步：把 .gs 內容貼到 script.google.com 按執行，表單與回覆試算表會一次建好。",
        ],
    )
    result = deliver(gs_path, destination, art, mime=TEXT_MIME).to_result()
    # 只給檔名，不洩漏伺服器絕對路徑（稽核半成品 #31）
    result["extra_files"] = [md_path.name]
    return result


SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_google_form",
        "description": (
            "產生一支可以一鍵建立 Google 表單的 Apps Script，同時附上題目一覽說明。"
            "適用於：新生報名表、營隊報名表、社課回饋問卷、幹部意願調查、"
            "活動出席統計、社員基本資料表。"
            "執行後會自動連好回覆試算表。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "檔名，不用加副檔名"},
                "form_title": {"type": "string", "description": "表單標題，填答者會看到"},
                "description": {"type": "string", "description": "表單開頭的說明文字"},
                "collect_email": {"type": "boolean", "description": "是否自動收集填答者的 Google 帳號信箱"},
                "questions": {
                    "type": "array",
                    "description": "題目清單",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": list(TYPES.keys()),
                                "description": "題型",
                            },
                            "title": {"type": "string", "description": "題目文字"},
                            "help": {"type": "string", "description": "題目下方的補充說明"},
                            "required": {"type": "boolean", "description": "是否必填"},
                            "options": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "選項（單選/複選/下拉式才需要）",
                            },
                            "scale_min": {"type": "integer", "description": "線性刻度最小值"},
                            "scale_max": {"type": "integer", "description": "線性刻度最大值"},
                            "scale_min_label": {"type": "string", "description": "刻度最小值的說明"},
                            "scale_max_label": {"type": "string", "description": "刻度最大值的說明"},
                        },
                        "required": ["type", "title"],
                    },
                },
                "destination": {
                    "type": "string",
                    "enum": ["local", "drive", "both"],
                    "description": "落點：local=只存本機、drive=上傳Google雲端硬碟、both=兩者",
                },
            },
            "required": ["filename", "form_title", "questions"],
        },
    },
}
