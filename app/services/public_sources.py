"""淡江官方公開資訊與 Instagram 公開 hashtag 的安全讀取服務。"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _source_record(
    *, provider: str, source_type: str, title: str, url: str, snippet: str,
    retrieved_at: str, search_id: str, published_at: str = "", school: str = "",
) -> dict[str, Any]:
    """Canonical conservative provenance shape for every public result."""
    return {
        "provider": provider,
        "source_type": source_type,
        "title": title[:300],
        "url": url[:1000],
        "snippet": snippet[:2000],
        "summary": snippet[:2000],
        "published_at": published_at[:80],
        "last_updated_at": "",
        "retrieved_at": retrieved_at,
        "search_id": search_id[:160],
        "school": school[:120],
        "verification_status": "pending_review",
        "verification": "pending_review",
        "confidence": "probable" if url else "unknown",
    }


def _meta_records(results: list[dict[str, Any]], *, search_id: str, query: str, retrieved_at: str, title_prefix: str = "Instagram 公開內容") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    for index, item in enumerate(results[:25]):
        if not isinstance(item, dict):
            continue
        title = str(item.get("username") or item.get("caption") or f"{title_prefix} {index + 1}")[:300]
        url = str(item.get("permalink") or "")[:1000]
        snippet = str(item.get("caption") or item.get("name") or "")[:2000]
        published = str(item.get("timestamp") or "")[:80]
        record = _source_record(
            provider="meta_instagram", source_type="instagram_public", title=title, url=url,
            snippet=snippet, retrieved_at=retrieved_at, search_id=search_id, published_at=published,
        )
        records.append(record)
        references.append({
            "source": title, "source_url": url, "excerpt": snippet, "date": published,
            "captured_at": retrieved_at, "source_type": "instagram_public", "provider": "meta_instagram",
            "search_id": search_id, "verification": "pending_review", "confidence": record["confidence"],
        })
    return records, references


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
    retrieved_at = _now()
    search_id = "tku_" + hashlib.sha256(f"{source_id}|{query.strip()}|{hashlib.sha256(raw).hexdigest()}".encode()).hexdigest()[:24]
    record = _source_record(
        provider="tku_official", source_type="official_web", title=source["name"], url=str(response.url),
        snippet=text, retrieved_at=retrieved_at, search_id=search_id, school="淡江大學",
    )
    return {
        "ok": True,
        "source": dict(source),
        "provider": "tku_official",
        "retrieved_at": retrieved_at,
        "content_sha256": hashlib.sha256(raw).hexdigest(),
        "query": query.strip(),
        "text": text,
        "matched": bool(text),
        "search_id": search_id,
        "references": [{
            "source": source["name"], "source_url": str(response.url), "excerpt": text[:2000],
            "date": "", "captured_at": retrieved_at, "source_type": "official_web",
            "provider": "tku_official", "search_id": search_id, "verification": "pending_review",
            "confidence": "probable",
        }],
        "source_records": [record],
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
    all_records = [record for item in results for record in (item.get("source_records") or [])]
    all_references = [ref for item in results for ref in (item.get("references") or [])]
    return {
        "ok": True,
        "query": query,
        "results": results,
        "references": all_references,
        "source_records": all_records,
        "failures": failures,
        "external_reference_warning": "官方公開頁面僅作來源證據；不得把外校或未確認內容當成淡江事實。",
    }


def _normalise_hashtag(value: str) -> str:
    value = value.strip().lstrip("#")
    if not re.fullmatch(r"[A-Za-z0-9_.\u0080-\uffff]{1,100}", value):
        raise ValueError("hashtag 格式不合法。")
    return value


def search_instagram_public_account(username: str, limit: int = 10) -> dict[str, Any]:
    username = username.strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_.]{1,30}", username):
        return {"ok": False, "code": "INVALID_USERNAME", "message": "Instagram 使用者名稱格式不合法。"}
    if not config.INSTAGRAM_PUBLIC_SEARCH_ENABLED or not config.INSTAGRAM_ACCESS_TOKEN:
        return {
            "ok": False,
            "code": "BLOCKED_BY_EXTERNAL_DEPENDENCY",
            "message": "Instagram 公開帳號搜尋尚未啟用；請完成 Meta App Review 並設定 server-only token。",
            "username": username,
        }
    if not config.INSTAGRAM_BUSINESS_ACCOUNT_ID:
        return {
            "ok": False,
            "code": "MISSING_BUSINESS_ACCOUNT",
            "message": "Business Discovery 需要已連結的 Instagram 專業帳號 ID。",
            "username": username,
        }

    nested_fields = (
        "id,username,name,biography,website,followers_count,"
        "media.limit(" + str(max(1, min(25, limit))) + ")"
        "{id,caption,media_type,media_url,permalink,timestamp}"
    )
    params = {
        "fields": "business_discovery.username(" + username + "){" + nested_fields + "}",
    }
    version = config.INSTAGRAM_GRAPH_API_VERSION
    try:
        with httpx.Client(
            timeout=config.INSTAGRAM_API_TIMEOUT_SECONDS,
            headers={"User-Agent": "tku-zen-agent/1.0", "Authorization": f"Bearer {config.INSTAGRAM_ACCESS_TOKEN}"},
        ) as client:
            response = client.get(
                f"https://graph.facebook.com/{version}/{config.INSTAGRAM_BUSINESS_ACCOUNT_ID}",
                params=params,
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        return {
            "ok": False,
            "code": "BLOCKED_BY_EXTERNAL_DEPENDENCY",
            "message": f"Meta Business Discovery 目前無法使用：{type(exc).__name__}。",
            "username": username,
        }

    profile = payload.get("business_discovery")
    if not profile:
        retrieved_at = _now()
        search_id = "meta_account_" + hashlib.sha256(username.encode()).hexdigest()[:24]
        return {
            "ok": True,
            "username": username,
            "profile": None,
            "results": [],
            "source": "meta_business_discovery",
            "provider": "meta_instagram",
            "search_id": search_id,
            "retrieved_at": retrieved_at,
            "references": [],
            "source_records": [],
        }
    retrieved_at = _now()
    search_id = "meta_account_" + hashlib.sha256(username.encode()).hexdigest()[:24]
    media_results = (profile.get("media") or {}).get("data", [])
    source_records, references = _meta_records(media_results, search_id=search_id, query=username, retrieved_at=retrieved_at, title_prefix=f"@{username}")
    profile_record = _source_record(
        provider="meta_instagram", source_type="instagram_public",
        title=f"Instagram @{username}", url=f"https://www.instagram.com/{username}/",
        snippet=str(profile.get("biography") or profile.get("name") or "")[:2000],
        retrieved_at=retrieved_at, search_id=search_id,
    )
    source_records.insert(0, profile_record)
    references.insert(0, {
        "source": profile_record["title"], "source_url": profile_record["url"],
        "excerpt": profile_record["snippet"], "date": "", "captured_at": retrieved_at,
        "source_type": "instagram_public", "provider": "meta_instagram", "search_id": search_id,
        "verification": "pending_review", "confidence": profile_record["confidence"],
    })
    return {
        "ok": True,
        "username": username,
        "profile": {
            key: value for key, value in profile.items() if key != "media"
        },
        "results": media_results,
        "source": "meta_business_discovery",
        "provider": "meta_instagram",
        "search_id": search_id,
        "retrieved_at": retrieved_at,
        "references": references,
        "source_records": source_records,
        "privacy_note": "僅回傳指定公開專業帳號的 Meta API 授權內容；不代表帳號所有者身分已驗證。",
    }


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
    }
    try:
        with httpx.Client(timeout=config.INSTAGRAM_API_TIMEOUT_SECONDS, headers={"User-Agent": "tku-zen-agent/1.0", "Authorization": f"Bearer {config.INSTAGRAM_ACCESS_TOKEN}"}) as client:
            tag_response = client.get(f"{base}/ig_hashtag_search", params=params)
            tag_response.raise_for_status()
            tag_id = tag_response.json().get("data", [{}])[0].get("id")
            if not tag_id:
                retrieved_at = _now()
                search_id = "meta_hashtag_" + hashlib.sha256(tag.encode()).hexdigest()[:24]
                return {"ok": True, "hashtag": tag, "results": [], "source": "meta", "provider": "meta_instagram", "search_id": search_id, "retrieved_at": retrieved_at, "references": [], "source_records": []}
            media_response = client.get(
                f"{base}/{tag_id}/recent_media",
                params={
                    "user_id": config.INSTAGRAM_BUSINESS_ACCOUNT_ID,
                    "fields": "id,caption,media_type,media_url,permalink,timestamp,username",
                    "limit": max(1, min(25, limit)),
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
    retrieved_at = _now()
    search_id = "meta_hashtag_" + hashlib.sha256(tag.encode()).hexdigest()[:24]
    source_records, references = _meta_records(results, search_id=search_id, query=tag, retrieved_at=retrieved_at, title_prefix=f"#{tag}")
    return {
        "ok": True,
        "hashtag": tag,
        "results": results[: max(1, min(25, limit))],
        "paging": payload.get("paging", {}),
        "source": "meta",
        "provider": "meta_instagram",
        "search_id": search_id,
        "retrieved_at": retrieved_at,
        "references": references,
        "source_records": source_records,
        "privacy_note": "僅回傳 Meta API 授權範圍內的公開內容；不抓私人帳號、不猜測人物身分。",
    }
