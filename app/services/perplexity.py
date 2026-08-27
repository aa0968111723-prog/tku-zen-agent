"""Perplexity Search API integration for current public web research.

The service intentionally stays synchronous because it is also used by the
tool registry. API routes and the orchestrator run it in a worker thread so an
upstream timeout never blocks FastAPI's event loop.
"""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from app import config

_ALLOWED_RECENCY = {"hour", "day", "week", "month", "year"}
_DEFAULT_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "tku-zen-agent-perplexity-search/1.0",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalise_domains(domains: list[str] | None) -> list[str]:
    result: list[str] = []
    for raw in domains or []:
        value = str(raw).strip()
        if not value or value.startswith("-") or len(value) > 253 or any(ch.isspace() for ch in value):
            continue
        parsed = urlparse(value if "://" in value else f"https://{value}")
        if parsed.hostname and parsed.hostname not in result:
            result.append(value)
        if len(result) >= 20:
            break
    return result


def _error(code: str, message: str, *, query: str = "", attempt_count: int = 0, started: float | None = None, error_code: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": False,
        "code": code,
        "message": message,
        "provider": "perplexity",
        "query": query,
        "attempt_count": attempt_count,
        "retrieved_at": _now(),
        "external_reference_warning": "公開網路搜尋目前不可用，未產生可引用的外部證據。",
    }
    if error_code:
        result["error_code"] = error_code
    if started is not None:
        result["latency_ms"] = int(max(0.0, time.perf_counter() - started) * 1000)
    return result


def _retry_delay(attempt: int, response: httpx.Response | None = None) -> float:
    """Exponential backoff with a bounded Retry-After for HTTP 429."""
    if response is not None and response.status_code == 429:
        raw = response.headers.get("Retry-After")
        try:
            return min(8.0, max(0.0, float(raw))) if raw is not None else min(8.0, 2.0 ** attempt)
        except (TypeError, ValueError):
            return min(8.0, 2.0 ** attempt)
    return min(8.0, 0.5 * (2.0 ** attempt))


def _source_records(results: list[dict[str, Any]], search_id: str, retrieved_at: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Create conservative provenance records; external results stay unverified."""
    records: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    for item in results[:20]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("name") or "未命名公開來源")[:300]
        url = str(item.get("url") or item.get("link") or "")[:1000]
        snippet = str(item.get("snippet") or item.get("summary") or item.get("description") or "")[:2000]
        published_at = str(item.get("published_at") or item.get("date") or "")[:80]
        confidence = "probable" if url else "unknown"
        record = {
            "provider": "perplexity", "source_type": "external_web", "title": title,
            "url": url, "snippet": snippet, "summary": snippet,
            "published_at": published_at, "last_updated_at": str(item.get("last_updated_at") or "")[:80],
            "retrieved_at": retrieved_at, "search_id": search_id,
            "verification_status": "pending_review", "verification": "pending_review",
            "confidence": confidence,
        }
        records.append(record)
        references.append({
            "source": title, "source_url": url, "excerpt": snippet, "date": published_at,
            "captured_at": retrieved_at, "source_type": "external_web", "provider": "perplexity",
            "search_id": search_id, "verification": "pending_review", "confidence": confidence,
        })
    return records, references


def search_web(
    query: str,
    *,
    domains: list[str] | None = None,
    recency: str | None = None,
    max_results: int = 10,
) -> dict[str, Any]:
    query = query.strip()
    if not query:
        return {"ok": False, "code": "INVALID_QUERY", "message": "搜尋字串不可為空。"}
    if recency and recency not in _ALLOWED_RECENCY:
        return {"ok": False, "code": "INVALID_RECENCY", "message": "日期範圍只能是 hour、day、week、month 或 year。"}
    if not config.PERPLEXITY_SEARCH_ENABLED:
        return _error("BLOCKED_BY_EXTERNAL_DEPENDENCY", "Perplexity 搜尋尚未啟用；請設定 PERPLEXITY_SEARCH_ENABLED=true。", query=query, error_code="CONFIG_MISSING")
    if not config.PERPLEXITY_API_KEY:
        return _error("BLOCKED_BY_EXTERNAL_DEPENDENCY", "缺少 Perplexity server-only API key；請設定 PERPLEXITY_API_KEY。", query=query, error_code="CONFIG_MISSING")

    clean_domains = _normalise_domains(domains)
    payload: dict[str, Any] = {
        "query": query, "country": "TW", "search_language_filter": ["zh"],
        "max_results": max(1, min(20, int(max_results))), "search_context_size": "high",
    }
    if clean_domains:
        payload["search_domain_filter"] = clean_domains
    if recency:
        payload["search_recency_filter"] = recency

    attempts_limit = max(1, int(config.PERPLEXITY_SEARCH_ATTEMPTS))
    timeout_seconds = max(3.0, float(config.PERPLEXITY_SEARCH_TIMEOUT_SECONDS))
    started = time.perf_counter()
    deadline = started + timeout_seconds * attempts_limit
    last_code = "BLOCKED_BY_EXTERNAL_DEPENDENCY"
    last_message = "Perplexity 搜尋目前無法使用。"
    last_attempt_count = 0

    for attempt_index in range(attempts_limit):
        attempt_count = attempt_index + 1
        last_attempt_count = attempt_count
        response: httpx.Response | None = None
        try:
            with httpx.Client(
                base_url=config.PERPLEXITY_BASE_URL,
                timeout=timeout_seconds,
                headers={**_DEFAULT_HEADERS, "Authorization": f"Bearer {config.PERPLEXITY_API_KEY}"},
            ) as client:
                response = client.post("/search", json=payload)
            status = int(response.status_code)
            if status in {401, 403}:
                return _error("AUTH_FAILED", "Perplexity API 授權失敗，請檢查 server-only API key。", query=query, attempt_count=attempt_count, started=started)
            if status in {400, 404, 422} or (400 <= status < 500 and status != 429):
                return _error("UPSTREAM_4XX", f"Perplexity 請求被拒絕（HTTP {status}）。", query=query, attempt_count=attempt_count, started=started)
            if status == 429 or status >= 500:
                last_code = "RATE_LIMITED" if status == 429 else "UPSTREAM_5XX"
                last_message = "Perplexity 暫時達到速率限制，稍後再試。" if status == 429 else "Perplexity 上游服務暫時錯誤。"
                if attempt_count < attempts_limit and time.perf_counter() < deadline:
                    delay = min(_retry_delay(attempt_index, response), max(0.0, deadline - time.perf_counter()))
                    if delay > 0:
                        time.sleep(delay)
                    continue
                break
            if status >= 400:
                return _error("UPSTREAM_4XX", f"Perplexity 請求失敗（HTTP {status}）。", query=query, attempt_count=attempt_count, started=started)
            data = response.json()
            if not isinstance(data, dict):
                return _error("BLOCKED_BY_EXTERNAL_DEPENDENCY", "Perplexity 回傳格式異常。", query=query, attempt_count=attempt_count, started=started)
            raw_results = data.get("results")
            results = [item for item in raw_results if isinstance(item, dict)] if isinstance(raw_results, list) else []
            retrieved_at = _now()
            search_id = str(data.get("id") or "")[:160]
            if not search_id:
                search_id = "pplx_" + hashlib.sha256(f"{query}|{retrieved_at}".encode()).hexdigest()[:24]
            source_records, references = _source_records(results, search_id, retrieved_at)
            return {
                "ok": True, "provider": "perplexity", "query": query,
                "filters": {"domains": clean_domains, "recency": recency, "country": "TW", "language": "zh"},
                "results": results[: payload["max_results"]], "references": references, "source_records": source_records,
                "search_id": search_id, "server_time": data.get("server_time"), "retrieved_at": retrieved_at,
                "attempt_count": attempt_count, "latency_ms": int((time.perf_counter() - started) * 1000),
                "external_reference_warning": "搜尋結果是公開網路參考，需查看原始來源後才能列為已確認事實。",
            }
        except httpx.HTTPStatusError as exc:
            response = getattr(exc, "response", None)
            status = int(getattr(getattr(exc, "response", None), "status_code", 0) or 0)
            if status in {401, 403}:
                return _error("AUTH_FAILED", "Perplexity API 授權失敗，請檢查 server-only API key。", query=query, attempt_count=attempt_count, started=started)
            if status and status != 429 and status < 500:
                return _error("UPSTREAM_4XX", f"Perplexity 請求被拒絕（HTTP {status}）。", query=query, attempt_count=attempt_count, started=started)
            last_code = "RATE_LIMITED" if status == 429 else "UPSTREAM_5XX"
            last_message = "Perplexity 暫時達到速率限制，稍後再試。" if status == 429 else "Perplexity 上游服務暫時錯誤。"
        except httpx.TimeoutException:
            last_code, last_message = "NETWORK_TIMEOUT", "Perplexity 搜尋逾時。"
        except httpx.TransportError:
            last_code, last_message = "NETWORK_TIMEOUT", "Perplexity 網路連線失敗。"
        except (ValueError, TypeError, KeyError):
            last_code, last_message = "BLOCKED_BY_EXTERNAL_DEPENDENCY", "Perplexity 回傳格式異常。"
        if attempt_count < attempts_limit and time.perf_counter() < deadline and last_code in {"NETWORK_TIMEOUT", "RATE_LIMITED", "UPSTREAM_5XX"}:
            delay = min(_retry_delay(attempt_index, response), max(0.0, deadline - time.perf_counter()))
            if delay > 0:
                time.sleep(delay)
            continue
        break

    return _error(last_code, last_message, query=query, attempt_count=last_attempt_count, started=started)
