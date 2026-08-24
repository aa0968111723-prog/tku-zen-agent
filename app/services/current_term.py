"""本學期的真實資料層。

這是整個 v2 最關鍵的一層。原本的知識庫裡混著 108–115 學年度的歷年檔案，
代理很容易把「113 學年度的社長」「去年的社費」當成今年的事實寫進產出。
所以把「會逐年變動的事實」抽出來獨立管理，並且訂死優先順序：

    Current Term State > Curated Knowledge > Playbook > Historical Archive > 模型常識

規則只有一條，但沒有例外：**這裡查不到就是 unknown，不准往歷年資料找答案。**
產出檔案時缺漏欄位一律填「待填」。
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .. import config

UNKNOWN = "unknown"
PLACEHOLDER = "待填"


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    hint: str = ""
    kind: str = "text"          # text | longtext | list
    sensitive_if_public: bool = False


# 欄位定義同時給 YAML、UI 表單與提示詞用，只有這一份來源
FIELDS: tuple[Field, ...] = (
    Field("academic_year", "學年度", "例如 115", "text"),
    Field("semester", "學期", "上學期 或 下學期", "text"),
    Field("club_name", "社團全名", "預設為淡江大學領袖禪學社", "text"),
    Field("president", "社長", "本學期社長姓名", "text", sensitive_if_public=True),
    Field("officers", "幹部", "一行一位，格式：職稱：姓名", "list", sensitive_if_public=True),
    Field("regular_meeting_time", "社課時間", "例如 每週三 18:30–21:30", "text"),
    Field("regular_meeting_location", "社課地點", "例如 H116", "text"),
    Field("club_fee", "社費", "例如 每學期 300 元", "text"),
    Field("recruitment_period", "招生期間", "例如 9/8–9/30", "text"),
    Field("signup_url", "報名連結", "Google 表單或社群連結", "text"),
    Field("primary_drive_folder", "共用雲端資料夾", "Drive 資料夾 ID 或網址", "text"),
    Field("source_note", "資料來源備註", "這些資料是誰在什麼時候確認的", "longtext"),
)

FIELD_BY_KEY = {f.key: f for f in FIELDS}

TEMPLATE_HEADER = """\
# 淡江大學領袖禪學社 —— 本學期真實資料
#
# 這個檔案是「今年的事實」唯一來源。代理只會從這裡拿當期資訊，
# 查不到就回答 unknown、在產出裡填「待填」，不會去歷年檔案裡湊一個看起來合理的答案。
#
# 留空或維持 unknown 都代表「還沒確認」。寧可留空，不要填錯。
# 也可以在網頁介面的「本學期設定」直接編輯，不用手改這個檔案。
"""


@dataclass
class TermState:
    values: dict[str, Any]
    updated_at: str | None
    path: Path
    exists: bool

    # ── 查詢 ─────────────────────────────────────────────

    def get(self, key: str) -> str | None:
        """回傳已確認的值；沒有就 None。不會猜。"""
        raw = self.values.get(key)
        if raw is None:
            return None
        if isinstance(raw, list):
            items = [str(x).strip() for x in raw if str(x).strip()]
            return "\n".join(items) if items else None
        text = str(raw).strip()
        if not text or text.lower() == UNKNOWN:
            return None
        return text

    def known(self) -> dict[str, str]:
        return {f.key: v for f in FIELDS if (v := self.get(f.key))}

    def missing(self) -> list[str]:
        return [f.key for f in FIELDS if self.get(f.key) is None]

    def for_artifact(self, key: str) -> str:
        """產出檔案要用的值 —— 沒確認就是「待填」，絕不用歷史值頂替。"""
        return self.get(key) or PLACEHOLDER

    def term_label(self) -> str:
        year, sem = self.get("academic_year"), self.get("semester")
        if year and sem:
            return f"{year} 學年度{sem}"
        if year:
            return f"{year} 學年度"
        return "（本學期未設定）"

    # ── 給模型看的區塊 ────────────────────────────────────

    def prompt_block(self) -> str:
        known = self.known()
        missing = self.missing()

        if not self.exists or not known:
            return (
                "## 本學期真實資料\n\n"
                "**目前完全沒有設定本學期資料。**\n"
                "任何跟今年有關的具體事實（社長、社課時間地點、社費、報名連結、日期）"
                "你都不知道，也不可以從歷年檔案推測。使用者問就直接說還沒設定、"
                "請他到「本學期設定」填寫；做檔案就填「待填」。"
            )

        lines = [
            "## 本學期真實資料（唯一可信的今年事實來源）",
            "",
            "以下資料**僅屬淡江大學領袖禪學社**，不得寫成其他學校或其他社團的資料。",
            "",
        ]
        for f in FIELDS:
            v = known.get(f.key)
            if v:
                lines.append(f"- **{f.label}**：{v}" if "\n" not in v else f"- **{f.label}**：\n  " + v.replace("\n", "\n  "))
        if self.updated_at:
            lines.append(f"- （最後更新：{self.updated_at}）")

        if missing:
            labels = "、".join(FIELD_BY_KEY[k].label for k in missing)
            lines += [
                "",
                f"**以下還沒設定：{labels}。**",
                "這些項目你不知道，也不准從歷年檔案或常識推測。"
                "使用者問就說還沒設定、請他補；做檔案就填「待填」。",
            ]
        return "\n".join(lines)


# ── 讀寫 ─────────────────────────────────────────────────────

_cache: TermState | None = None
_cache_key: tuple[str, float] | None = None
_lock = threading.RLock()


def invalidate() -> None:
    global _cache, _cache_key
    with _lock:
        _cache = None
        _cache_key = None


def load() -> TermState:
    """讀取當期狀態，依檔案 mtime 快取。"""
    global _cache, _cache_key
    path = Path(config.CURRENT_TERM_FILE)
    key = (str(path), path.stat().st_mtime if path.exists() else -1.0)

    with _lock:
        if _cache is not None and _cache_key == key:
            return _cache

        values: dict[str, Any] = {}
        exists = path.exists()
        if exists:
            try:
                loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                if isinstance(loaded, dict):
                    values = loaded
            except (yaml.YAMLError, OSError):
                values = {}

        updated = values.get("updated_at")
        state = TermState(
            values=values,
            updated_at=str(updated) if updated else None,
            path=path,
            exists=exists,
        )
        _cache, _cache_key = state, key
        return state


def update(fields: dict[str, Any]) -> TermState:
    """寫入當期狀態。只接受已知欄位，避免介面被塞進奇怪的東西。"""
    path = Path(config.CURRENT_TERM_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)

    with _lock:
        current = load().values.copy()
        for key, raw in fields.items():
            if key not in FIELD_BY_KEY:
                continue
            field = FIELD_BY_KEY[key]
            if field.kind == "list":
                if isinstance(raw, str):
                    items = [line.strip() for line in raw.splitlines() if line.strip()]
                elif isinstance(raw, list):
                    items = [str(x).strip() for x in raw if str(x).strip()]
                else:
                    items = []
                current[key] = items
            else:
                text = ("" if raw is None else str(raw)).strip()
                current[key] = text

        current["updated_at"] = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
        path.write_text(
            TEMPLATE_HEADER + "\n" + yaml.safe_dump(current, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        invalidate()
    return load()


def as_form() -> dict[str, Any]:
    """給「本學期設定」頁用 —— 欄位定義 + 目前值 + 哪些還沒填。"""
    state = load()
    return {
        "term_label": state.term_label(),
        "updated_at": state.updated_at,
        "exists": state.exists,
        "missing": state.missing(),
        "fields": [
            {
                "key": f.key,
                "label": f.label,
                "hint": f.hint,
                "kind": f.kind,
                "value": state.get(f.key) or "",
                "known": state.get(f.key) is not None,
            }
            for f in FIELDS
        ],
    }


def fingerprint() -> str:
    """本學期資料版本；供包含當期事實的 retrieval context 快取失效。"""
    state = load()
    payload = {
        "values": {field.key: state.get(field.key) or "" for field in FIELDS},
        "updated_at": state.updated_at or "",
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
