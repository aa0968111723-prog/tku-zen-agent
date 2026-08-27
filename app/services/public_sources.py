"""淡江官方公開資訊與 Instagram 公開 hashtag 的安全讀取服務。"""

from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from app import config

TkuSource = dict[str, str]

TKU_PUBLIC_SOURCES: tuple[TkuSource, ...] = (
    {"id": "tku-home", "name": "淡江大學首頁", "url": "https://www.tku.edu.tw/", "kind": "official"},
    {"id": "tku-calendar", "name": "淡江學事曆", "url": "https://acad.tku.edu.tw/get_page?lang=tw&rtdoc_id=OAA201&t=rtdoc", "kind": "official"},
    {"id": "tku-student-affairs", "name": "淡江學生事務處", "url": "https://sa.tku.edu.tw/", "kind": "official"},
    {"id": "tku-clubs", "name": "淡江社團系統", "url": "https://club.sis.tku.edu.tw/", "kind": "official"},
    {"id": "tku-library", "name": "淡江圖書館", "url": "https://www.lib.tku.edu.tw/", "kind": "official"},
    {"id": "tku-times", "name": "淡江時報", "url": "https://tkutimes.tku.edu.tw/", "kind": "official"},
    {"id": "tku-student-portal", "name": "淡江學生資訊入口", "url": "https://classic.tku.edu.tw/campstu.asp", "kind": "official"},
)

_TKU_HOSTS = {"tku.edu.tw", "www.tku.edu.tw", "acad.tku.edu.tw", "sa.tku.edu.tw",
              "club.sis.tku.edu.tw", "www.lib.tku.edu.tw", "tkutimes.tku.edu.tw",
              "classic.tku.edu.tw"}
_HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml")
_DEFAULT_HEADERS = {
    "User-Agent": "tku-zen-agent-public-source-reader/1.0",
    "Accept": "text/html,application/xhtml+xml",
}


class _VisibleTextParser(HTMLParser):
    _SKIP_TAGS = {"script", "style", "noscript", "svg", "template"}
    _BLOCK_TAGS = {"br", "p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        if self._skip_depth == 0 and tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._skip_depth and tag in self._SKIP_TAGS:
            self._skip_depth -= 1
        if self._skip_depth == 0 and tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self.parts.append(data)

    def text(self) -> str:
        value = " ".join("".join(self.parts).split())
        return value[: config.PUBLIC_SOURCE_MAX_TEXT_CHARS]


def _source(source_id: str) -> TkuSource | None:
    return next((item for item in TKU_PUBLIC_SOURCES if item["id"] == source_id), None)


def _host_allowed(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme == "https" and (host in _TKU_HOSTS or host.endswith(".tku.edu.tw"))


def list_public_sources(query: str = "") -> list[TkuSource]:
    needle = query.strip().casefold()
    return [
        dict(item)
        for item in TKU_PUBLIC_SOURCES
        if not needle or needle in item["id"].casefold() or needle in item["name"].casefold()
    ]


def fetch_public_source(source_id: str, query: str = "") -> dict[str, Any]:
    source = _source(source_id)
    if source is None:
        return {"ok": False, "code": "NOT_FOUND", "message": "未知的公開來源。"}
    if not _host_allowed(source["url"]):
        return {"ok": False, "code": "SOURCE_NOT_ALLOWED", "message": "來源不在官方網域白名單。"}

    try:
        with httpx.Client(
            timeout=config.PUBLIC_SOURCE_TIMEOUT_SECONDS,
            headers=_DEFAULT_HEADERS,
            follow_redirects=True,
        ) as client:
            response = client.get(source["url"])
            response.raise_for_status()
    except (httpx.HTTPError, ValueError) as exc:
        return {
            "ok": False,
            "code": "BLOCKED_BY_EXTERNAL_DEPENDENCY",
            "message": f"官方來源目前無法讀取：{type(exc).__name__}。",
            "source": dict(source),
        }

    if not _host_allowed(str(response.url)):
        return {"ok": False, "code": "REDIRECT_NOT_ALLOWED", "message": "來源重新導向到非官方網域。"}
    if response.headers.get("content-type", "").split(";", 1)[0].lower() not in _HTML_CONTENT_TYPES:
        return {"ok": False, "code": "UNSUPPORTED_CONTENT_TYPE", "message": "目前只讀取官方 HTML 公開頁面。"}

    raw = response.content
    if len(raw) > config.PUBLIC_SOURCE_MAX_BYTES:
        return {"ok": False, "code": "SOURCE_TOO_LARGE", "message": "來源頁面超過安全大小限制。"}

    parser = _VisibleTextParser()
    parser.feed(response.text)
    text = parser.text()
    needle = query.strip().casefold()
    if needle and needle not in text.casefold():
        text = ""
    return {
        "ok": True,
        "source": dict(source),
        "retrieved_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "content_sha256": hashlib.sha256(raw).hexdigest(),
        "query": query.strip(),
        "text": text,
        "matched": bool(text),
    }


def search_public_info(query: str, source_ids: list[str] | None = None, limit: int = 4) -> dict[str, Any]:
    query = query.strip()
    if not query:
        return {"ok": False, "code": "INVALID_QUERY", "message": "搜尋字串不可為空。"}

    allowed = {item["id"] for item in TKU_PUBLIC_SOURCES}
    selected = [item for item in (source_ids or []) if item in allowed]
    if not selected:
        selected = [item["id"] for item in TKU_PUBLIC_SOURCES]
    selected = selected[: max(1, min(config.PUBLIC_SOURCE_MAX_SOURCES, limit))]

    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for source_id in selected:
        result = fetch_public_source(source_id, query)
        if result.get("ok"):
            results.append(result)
        else:
            failures.append({"source_id": source_id, "code": result.get("code"), "message": result.get("message")})
    return {
        "ok": True,
        "query": query,
        "results": results,
        "failures": failures,
        "external_reference_warning": "官方公開頁面僅作來源證據；不得把外校或未確認內容當成淡江事實。",
    }


def _normalise_hashtag(value: str) -> str:
    value = value.strip().lstrip("#")
    if not re.fullmatch(r"[A-Za-z0-9_.\u0080-\uffff]{1,100}", value):
        raise ValueError("hashtag 格式不合法。")
    return value


def search_instagram_public_hashtag(hashtag: str, limit: int = 10) -> dict[str, Any]:
    try:
        tag = _normalise_hashtag(hashtag)
    except ValueError as exc:
        return {"ok": False, "code": "INVALID_HASHTAG", "message": str(exc)}

    if not config.INSTAGRAM_PUBLIC_SEARCH_ENABLED or not config.INSTAGRAM_ACCESS_TOKEN:
        return {
            "ok": False,
            "code": "BLOCKED_BY_EXTERNAL_DEPENDENCY",
            "message": "Instagram 公開 hashtag 搜尋尚未啟用；請完成 Meta App Review 並設定 server-only token。",
            "hashtag": tag,
        }
    if not config.INSTAGRAM_BUSINESS_ACCOUNT_ID:
        return {
            "ok": False,
            "code": "MISSING_BUSINESS_ACCOUNT",
            "message": "Instagram Graph API 需要已連結的專業帳號 ID。",
            "hashtag": tag,
        }

    version = config.INSTAGRAM_GRAPH_API_VERSION
    base = f"https://graph.facebook.com/{version}"
    params = {
        "user_id": config.INSTAGRAM_BUSINESS_ACCOUNT_ID,
        "q": tag,
        "fields": "id,name",
        "access_token": config.INSTAGRAM_ACCESS_TOKEN,
    }
    try:
        with httpx.Client(timeout=config.INSTAGRAM_API_TIMEOUT_SECONDS, headers={"User-Agent": "tku-zen-agent/1.0"}) as client:
            tag_response = client.get(f"{base}/ig_hashtag_search", params=params)
            tag_response.raise_for_status()
            tag_id = tag_response.json().get("data", [{}])[0].get("id")
            if not tag_id:
                return {"ok": True, "hashtag": tag, "results": [], "source": "meta"}
            media_response = client.get(
                f"{base}/{tag_id}/recent_media",
                params={
                    "user_id": config.INSTAGRAM_BUSINESS_ACCOUNT_ID,
                    "fields": "id,caption,media_type,media_url,permalink,timestamp,username",
                    "limit": max(1, min(25, limit)),
                    "access_token": config.INSTAGRAM_ACCESS_TOKEN,
                },
            )
            media_response.raise_for_status()
            payload = media_response.json()
    except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
        return {
            "ok": False,
            "code": "BLOCKED_BY_EXTERNAL_DEPENDENCY",
            "message": f"Meta Instagram API 目前無法使用：{type(exc).__name__}。",
            "hashtag": tag,
        }

    results = payload.get("data", [])
    return {
        "ok": True,
        "hashtag": tag,
        "results": results[: max(1, min(25, limit))],
        "paging": payload.get("paging", {}),
        "source": "meta",
        "privacy_note": "僅回傳 Meta API 授權範圍內的公開內容；不抓私人帳號、不猜測人物身分。",
    }
