"""資料驅動的公開研究入口地圖。

這個模組只保存已核對的 HTTPS 入口與 provenance 規則；它不是爬蟲、
社團 SSOT，也不會從社群網站推論人物或組織身分。公開頁面要不要即時
讀取仍由 ``public_sources`` 的 server-side allowlist 決定。

PR #33 合併後 main 先放精簡 stub，本檔在不改 entity id 的前提下
補上 2026-08-27 核過的 HTTPS 官方入口。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse


SOCIAL_HOSTS = frozenset(
    {
        "facebook.com",
        "www.facebook.com",
        "m.facebook.com",
        "l.facebook.com",
        "instagram.com",
        "www.instagram.com",
        "x.com",
        "twitter.com",
        "www.twitter.com",
        "linkedin.com",
        "www.linkedin.com",
    }
)


@dataclass(frozen=True)
class PublicSourceEntity:
    """一個可供研究導覽使用的公開來源類別。"""

    id: str
    category: str
    name: str
    source_kind: str
    official_https: tuple[str, ...]
    reference_only: tuple[str, ...]
    fetch_policy: str
    aliases: tuple[str, ...] = ()
