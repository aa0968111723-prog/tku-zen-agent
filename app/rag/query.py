"""Query rewrite —— 把一句自然語言拆成檢索用得上的結構。

規則式，不叫模型：確定性、不花額度、不多一輪延遲。
拆出來的東西直接餵給 metadata filter 與 rerank。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .metadata import ACTIVITY_RULES, DOC_TYPE_RULES, _match_first

# 年份偏好
_THIS_YEAR = re.compile(r"(今年|本學期|這學期|這個學期|當期|最新|現在)")
_LAST_YEAR = re.compile(r"(去年|上學年|上一屆|前一屆|上次|以前|歷年|往年|過去|之前)")
_EXPLICIT_YEAR = re.compile(r"(?<!\d)(1[0-2]\d)\s*學年")

# 用途
_PURPOSE = (
    ("external", ("對外", "招生", "文宣", "貼文", "海報", "宣傳", "新生", "限動")),
    ("official", ("學校", "課外組", "評鑑", "行政", "公文", "申請", "核銷")),
    ("internal", ("幹部", "交接", "傳承", "內部", "檢討")),
)

_NOISE = re.compile(r"(幫我|請你|可以|麻煩|一下|謝謝|好嗎|okay|ok|然後|那個|就是)")


@dataclass
class ParsedQuery:
    raw: str
    topic: str
    document_type: str | None = None
    activity: str | None = None
    year_preference: str = "any"     # current | recent | explicit | any
    explicit_year: str | None = None
    purpose: str = ""
    keywords: list[str] = field(default_factory=list)

    def describe(self) -> str:
        bits = [self.topic]
        if self.activity:
            bits.append(self.activity)
        if self.document_type:
            bits.append(self.document_type)
        return " ".join(b for b in bits if b)


def parse(text: str) -> ParsedQuery:
    raw = text.strip()
    topic = _NOISE.sub("", raw).strip() or raw

    q = ParsedQuery(raw=raw, topic=topic[:100])
    q.document_type = _match_first(raw, DOC_TYPE_RULES)
    q.activity = _match_first(raw, ACTIVITY_RULES)

    m = _EXPLICIT_YEAR.search(raw)
    if m:
        q.year_preference = "explicit"
        q.explicit_year = m.group(1)
    elif _THIS_YEAR.search(raw):
        q.year_preference = "current"
    elif _LAST_YEAR.search(raw):
        q.year_preference = "recent"

    for name, words in _PURPOSE:
        if any(w in raw for w in words):
            q.purpose = name
            break

    q.keywords = [w for w in (q.activity, q.document_type) if w]
    return q


def expansions(q: ParsedQuery) -> list[str]:
    """由結構化結果反推出幾個互補的檢索字串。"""
    out = [q.topic]
    if q.activity and q.document_type:
        out.append(f"{q.activity} {q.document_type}")
    elif q.activity:
        out.append(q.activity)
    elif q.document_type:
        out.append(q.document_type)
    if q.explicit_year and q.activity:
        out.append(f"{q.explicit_year} {q.activity}")
    seen: list[str] = []
    for s in out:
        s = s.strip()
        if s and s not in seen:
            seen.append(s)
    return seen
