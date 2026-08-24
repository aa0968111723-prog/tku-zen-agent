"""來源級衝突與過期資料偵測。

不同學年度的值本來就可能不同，因此只有「同一學年度的不同值」會被列為
真正衝突；本學期資料與歷年值不同則列為 stale_reference，明確告訴模型應以
current_term 為準。
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from .hybrid import Scored

_PATTERNS: dict[str, re.Pattern[str]] = {
    "president": re.compile(r"社長\s*[：:]\s*([^\s，,。、|｜\n]{2,10})"),
    "regular_meeting_time": re.compile(r"(?:社課時間|上課時間)\s*[：:]?\s*([^\n。]{2,40})"),
    "regular_meeting_location": re.compile(r"(?:社課地點|上課地點|教室)\s*[：:]?\s*([^\n，,。]{1,30})"),
    "club_fee": re.compile(r"社費\s*[：:]?\s*([^\n，,。]{1,30})"),
    "signup_url": re.compile(r"(?:報名連結|報名網址)\s*[：:]?\s*(https?://[^\s)]+)"),
}


def detect_conflicts(
    hits: list[Scored], current_facts: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    values: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for scored in hits:
        meta = getattr(scored.chunk, "meta", None)
        if not meta:
            continue
        year = meta.academic_year or "unknown"
        for field, pattern in _PATTERNS.items():
            match = pattern.search(scored.chunk.text)
            if not match:
                continue
            value = re.sub(r"\s+", " ", match.group(1)).strip(" ：:")
            if not value:
                continue
            values[(field, year)].setdefault(
                value,
                {
                    "value": value,
                    "year": year,
                    "source": scored.chunk.source,
                    "source_type": meta.source_type,
                    "priority": meta.priority,
                },
            )

    conflicts: list[dict[str, Any]] = []
    for (field, year), records in values.items():
        if len(records) > 1 and year != "unknown":
            conflicts.append({
                "field": field,
                "kind": "same_year_conflict",
                "year": year,
                "values": list(records.values()),
                "resolution": "不要自行選值，請使用者確認；若是本學期欄位，以 current_term 為準。",
            })

    current = current_facts or {}
    for field, current_value in current.items():
        if field not in _PATTERNS or not current_value:
            continue
        historical: list[dict[str, Any]] = []
        for (candidate, _year), records in values.items():
            if candidate != field:
                continue
            for record in records.values():
                if re.sub(r"\s+", " ", str(record["value"])).strip() != re.sub(r"\s+", " ", str(current_value)).strip():
                    historical.append(record)
        if historical:
            conflicts.append({
                "field": field,
                "kind": "stale_reference",
                "current_value": current_value,
                "historical_values": historical[:6],
                "resolution": "本學期 current_term 優先；歷史值只能當格式或過往案例。",
            })
    return conflicts[:12]
