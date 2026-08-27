"""Server-only, replaceable adapters for the optional InsForge backend.

No browser code imports this module.  The local visual store remains the
source-of-truth; callers may explicitly opt into a remote sync and receive a
structured ``BLOCKED_BY_EXTERNAL_DEPENDENCY`` result when InsForge is absent.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote

import httpx

from .. import config
from .audit import mask_secrets
from .http_guard import CircuitBreaker, UnsafeOutboundURL, assert_public_https_url, new_request_id

logger = logging.getLogger(__name__)
_BREAKER = CircuitBreaker()

# Transport-level retries are only safe for methods that do not create or
# mutate rows. POST/PATCH/PUT/DELETE must not be replayed after a timeout.
_SAFE_RETRY_METHODS = {"GET", "HEAD", "OPTIONS"}
# 500 is retried only for safe methods. Mutating methods still use attempts=1.
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_BREAKER_STATUSES = {500, 502, 503, 504}
_MAX_RETRY_WAIT_SECONDS = 8.0


BLOCKED_BY_EXTERNAL_DEPENDENCY = "BLOCKED_BY_EXTERNAL_DEPENDENCY"


def _retry_after_seconds(response: httpx.Response | None, attempt: int, *, budget: float = _MAX_RETRY_WAIT_SECONDS) -> float:
    fallback = min(2.0, 0.25 * (2 ** attempt))
    delay = fallback
    if response is not None:
        raw = (response.headers.get("Retry-After") or "").strip()
        if raw:
            try:
                delay = float(raw)
            except ValueError:
                try:
                    when = parsedate_to_datetime(raw)
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=timezone.utc)
                    delay = (when - datetime.now(timezone.utc)).total_seconds()
                except (TypeError, ValueError, OverflowError, OSError):
                    delay = fallback
    return max(0.0, min(30.0, min(delay, max(0.0, budget))))


def _safe_provider_detail(text: str | None) -> str:
    """Keep client-facing InsForge errors free of keys, tokens, and paths."""
    raw = mask_secrets(str(text or ""))
    raw = re.sub(r"(?i)(authorization|x-api-key|api[_-]?key|bearer)[=:\s]+[^\s,;]+", "***", raw)
    raw = re.sub(r"(?i)(/[^\s\"']+\.(?:py|sqlite3?|pem|key)\b)", "[redacted-path]", raw)
    return raw[:300]


class InsForgeUnavailable(RuntimeError):
    code = BLOCKED_BY_EXTERNAL_DEPENDENCY

    def __init__(self, message: str, *, detail: Any = None) -> None:
        super().__init__(message)
        self.detail = detail


@dataclass(frozen=True)
class InsForgeStatus:
    configured: bool
    trusted: bool
    available: bool
    code: str = ""
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "trusted": self.trusted,
            "available": self.available,
            "code": self.code,
            "message": self.message,
        }


class _HTTPAdapter:
    def __init__(self) -> None:
        self.base_url = config.INSFORGE_BASE_URL
        self.key = config.INSFORGE_SERVICE_KEY or config.INSFORGE_ANON_KEY

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.key)

    def _require_configured(self) -> None:
        if not self.configured:
            raise InsForgeUnavailable("InsForge URL 或 server-only API key 尚未設定")

    def _headers(self, *, json_body: bool = False, request_id: str = "") -> dict[str, str]:
        self._require_configured()
        # REST docs use Authorization: Bearer; MCP/admin tools also send x-api-key.
        rid = request_id or new_request_id()
        headers = {
            "Authorization": f"Bearer {self.key}",
            "x-api-key": self.key,
            "X-Request-Id": rid,
            "Idempotency-Key": rid,
        }
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        if not path.startswith("/"):
            path = "/" + path
        if not _BREAKER.allow():
            raise InsForgeUnavailable("InsForge circuit breaker open; 稍後再試", detail="circuit_open")
        try:
            assert_public_https_url(self.base_url)
        except UnsafeOutboundURL as exc:
            raise InsForgeUnavailable(f"InsForge base URL 不安全: {exc}") from exc
        request_id = new_request_id()
        last_error: Exception | None = None
        method_upper = method.upper()
        retry_allowed = method_upper in _SAFE_RETRY_METHODS
        attempts = max(1, int(getattr(config, "INSFORGE_HTTP_ATTEMPTS", 2))) if retry_allowed else 1
        extra_headers = kwargs.pop("headers", {}) or {}
        allow_statuses = set(kwargs.pop("allow_statuses", ()) or ())
        json_body = "json" in kwargs
        wait_budget = _MAX_RETRY_WAIT_SECONDS
        for attempt in range(attempts):
            try:
                timeout = httpx.Timeout(config.INSFORGE_TIMEOUT_SECONDS, connect=min(5.0, config.INSFORGE_TIMEOUT_SECONDS))
                # Do not follow redirects: a 3xx to 169.254.169.254 would be SSRF.
                with httpx.Client(base_url=self.base_url, timeout=timeout, follow_redirects=False) as client:
                    request_headers = self._headers(json_body=json_body, request_id=request_id)
                    request_headers.update(extra_headers)
                    response = client.request(method, path, headers=request_headers, **kwargs)
                    if response.status_code in allow_statuses:
                        _BREAKER.record_success()
                        return response
                    if (
                        retry_allowed
                        and response.status_code in _RETRY_STATUSES
                        and attempt + 1 < attempts
                    ):
                        delay = _retry_after_seconds(response, attempt, budget=wait_budget)
                        wait_budget = max(0.0, wait_budget - delay)
                        time.sleep(delay)
                        last_error = httpx.HTTPStatusError("retryable", request=response.request, response=response)
                        continue
                    response.raise_for_status()
                    _BREAKER.record_success()
                    logger.info(
                        "insforge request ok method=%s path=%s status=%s request_id=%s latency_attempt=%s",
                        method, path, response.status_code, request_id, attempt + 1,
                    )
                    return response
            except InsForgeUnavailable:
                raise
            except httpx.HTTPStatusError as exc:
                last_error = exc
                status = exc.response.status_code if exc.response is not None else 0
                if retry_allowed and status in _RETRY_STATUSES and attempt + 1 < attempts:
                    delay = _retry_after_seconds(exc.response, attempt, budget=wait_budget)
                    wait_budget = max(0.0, wait_budget - delay)
                    time.sleep(delay)
                    continue
                break
            except (httpx.HTTPError, OSError) as exc:
                last_error = exc
                if retry_allowed and attempt + 1 < attempts:
                    delay = _retry_after_seconds(None, attempt, budget=wait_budget)
                    wait_budget = max(0.0, wait_budget - delay)
                    time.sleep(delay)
                    continue
                break
        status = 0
        if isinstance(last_error, httpx.HTTPStatusError) and last_error.response is not None:
            status = last_error.response.status_code
            # Client/auth errors are not provider outages.
            if status in _BREAKER_STATUSES:
                _BREAKER.record_failure()
            detail = _safe_provider_detail(last_error.response.text)
            logger.warning(
                "insforge request failed status=%s path=%s request_id=%s",
                status, path, request_id,
            )
            raise InsForgeUnavailable(
                f"InsForge request failed ({status}): {detail or type(last_error).__name__}",
                detail=detail,
            ) from last_error
        _BREAKER.record_failure()
        logger.warning("insforge request failed path=%s request_id=%s error=%s", path, request_id, type(last_error).__name__)
        raise InsForgeUnavailable(
            f"InsForge request failed: {type(last_error).__name__}",
            detail=_safe_provider_detail(str(last_error)),
        ) from last_error

    @staticmethod
    def _json(response: httpx.Response) -> Any:
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise InsForgeUnavailable("InsForge 回應不是有效 JSON", detail=response.text[:500]) from exc


class InsForgeDatabaseAdapter(_HTTPAdapter):
    """PostgREST database boundary; table names are validated server-side."""

    _table = re.compile(r"^[a-z][a-z0-9_]{0,62}$")

    def select(self, table: str, *, params: dict[str, str] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        self._validate_table(table)
        query = dict(params or {})
        query.setdefault("select", "*")
        query.setdefault("limit", str(max(1, min(limit, 200))))
        payload = self._json(self._request("GET", f"/api/database/records/{table}", params=query))
        return payload if isinstance(payload, list) else payload.get("data", []) if isinstance(payload, dict) else []

    def insert(self, table: str, rows: list[dict[str, Any]] | dict[str, Any]) -> list[dict[str, Any]]:
        self._validate_table(table)
        body = rows if isinstance(rows, list) else [rows]
        response = self._request("POST", f"/api/database/records/{table}", json=body, headers={"Prefer": "return=representation"})
        payload = self._json(response)
        return payload if isinstance(payload, list) else payload.get("data", []) if isinstance(payload, dict) else []

    def upsert(self, table: str, rows: list[dict[str, Any]] | dict[str, Any], *, on_conflict: str = "id") -> list[dict[str, Any]]:
        self._validate_table(table)
        body = rows if isinstance(rows, list) else [rows]
        # InsForge resolves conflicts from table PK/unique constraints. The
        # parameter is retained for adapter compatibility and validation.
        if not all(self._table.fullmatch(part.strip()) for part in on_conflict.split(",")):
            raise ValueError("invalid InsForge conflict columns")
        response = self._request("POST", f"/api/database/records/{table}", json=body, headers={"Prefer": "resolution=merge-duplicates,return=representation"})
        payload = self._json(response)
        return payload if isinstance(payload, list) else payload.get("data", []) if isinstance(payload, dict) else []

    def update(self, table: str, filters: dict[str, str], values: dict[str, Any]) -> list[dict[str, Any]]:
        self._validate_table(table)
        response = self._request("PATCH", f"/api/database/records/{table}", params=filters, json=values, headers={"Prefer": "return=representation"})
        payload = self._json(response)
        return payload if isinstance(payload, list) else payload.get("data", []) if isinstance(payload, dict) else []

    def rpc(self, name: str, payload: dict[str, Any]) -> Any:
        if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,62}", name):
            raise ValueError("invalid InsForge RPC name")
        return self._json(self._request("POST", f"/api/database/rpc/{name}", json=payload))

    def health(self) -> InsForgeStatus:
        if not self.configured:
            return InsForgeStatus(False, config.INSFORGE_TRUSTED, False, BLOCKED_BY_EXTERNAL_DEPENDENCY, "InsForge 未設定")
        try:
            self._request("GET", "/api/health")
            return InsForgeStatus(True, config.INSFORGE_TRUSTED, True)
        except InsForgeUnavailable as exc:
            return InsForgeStatus(True, config.INSFORGE_TRUSTED, False, BLOCKED_BY_EXTERNAL_DEPENDENCY, str(exc))

    @classmethod
    def _validate_table(cls, table: str) -> None:
        if not cls._table.fullmatch(table):
            raise ValueError("invalid InsForge table name")


class InsForgeStorageAdapter(_HTTPAdapter):
    """Storage boundary using InsForge's authenticated object REST API."""

    def upload_bytes(self, bucket: str, key: str, content: bytes, mime_type: str) -> dict[str, Any]:
        if not config.INSFORGE_TRUSTED:
            raise InsForgeUnavailable("InsForge storage 尚未標記為 trusted，禁止送出檔案")
        self._validate_bucket(bucket)
        object_key = key.lstrip("/")
        strategy = self._upload_strategy(bucket, object_key, mime_type, len(content))
        method = str((strategy or {}).get("method") or "").lower()
        if method == "presigned":
            return self._upload_presigned(bucket, object_key, content, mime_type, strategy or {})
        # Direct (current backend) or fallback: deprecated PUT still works on 2.3.1.
        return self._put_object(bucket, object_key, content, mime_type, strategy or {})

    def _upload_strategy(self, bucket: str, key: str, mime_type: str, size: int) -> dict[str, Any] | None:
        """POST /upload-strategy per current REST docs; None means use PUT fallback."""
        try:
            response = self._request(
                "POST",
                f"/api/storage/buckets/{bucket}/upload-strategy",
                json={"filename": key, "contentType": mime_type, "size": size},
                allow_statuses={404},
            )
            if response.status_code == 404:
                logger.info("insforge upload-strategy 404; falling back to PUT")
                return None
            payload = self._json(response)
        except InsForgeUnavailable as exc:
            logger.info("insforge upload-strategy unavailable; falling back to PUT path=%s", type(exc).__name__)
            return None
        return payload if isinstance(payload, dict) else None

    def _put_object(
        self,
        bucket: str,
        key: str,
        content: bytes,
        mime_type: str,
        strategy: dict[str, Any],
    ) -> dict[str, Any]:
        safe_key = quote(key, safe="/._-")
        upload_url = str(strategy.get("uploadUrl") or "")
        path = upload_url if upload_url.startswith("/") else f"/api/storage/buckets/{bucket}/objects/{safe_key}"
        response = self._request(
            "PUT",
            path,
            files={"file": (key.rsplit("/", 1)[-1] or "object", content, mime_type)},
        )
        payload = self._json(response)
        data = payload.get("data", payload) if isinstance(payload, dict) else {}
        return {
            "bucket": bucket,
            "key": str(data.get("key") or strategy.get("key") or key),
            "url": str(data.get("url") or self.object_url(bucket, key)),
            "size": int(data.get("size") or len(content)),
        }

    def _upload_presigned(
        self,
        bucket: str,
        key: str,
        content: bytes,
        mime_type: str,
        strategy: dict[str, Any],
    ) -> dict[str, Any]:
        upload_url = str(strategy.get("uploadUrl") or "")
        try:
            assert_public_https_url(upload_url)
        except UnsafeOutboundURL as exc:
            raise InsForgeUnavailable(f"InsForge presigned upload URL 不安全: {exc}") from exc
        fields = strategy.get("fields") if isinstance(strategy.get("fields"), dict) else {}
        filename = key.rsplit("/", 1)[-1] or "object"
        timeout = httpx.Timeout(config.INSFORGE_TIMEOUT_SECONDS, connect=min(5.0, config.INSFORGE_TIMEOUT_SECONDS))
        try:
            # Do not attach InsForge Authorization; S3 rejects extra auth headers.
            with httpx.Client(timeout=timeout, follow_redirects=False) as client:
                response = client.post(
                    upload_url,
                    data=fields,
                    files={"file": (filename, content, mime_type)},
                )
                response.raise_for_status()
        except (httpx.HTTPError, OSError) as exc:
            raise InsForgeUnavailable(f"InsForge presigned upload failed: {exc}") from exc
        if strategy.get("confirmRequired"):
            confirm = str(strategy.get("confirmUrl") or "")
            if confirm.startswith("/"):
                self._request(
                    "POST",
                    confirm,
                    json={"size": len(content), "contentType": mime_type},
                )
        return {
            "bucket": bucket,
            "key": str(strategy.get("key") or key),
            "url": str(strategy.get("url") or self.object_url(bucket, key)),
            "size": len(content),
        }

    def object_url(self, bucket: str, key: str) -> str:
        self._require_configured()
        # This is a server-side reference only; ACL is still enforced by the
        # database and callers should proxy downloads rather than expose keys.
        return f"{self.base_url}/api/storage/buckets/{bucket}/objects/{quote(key.lstrip('/'), safe='/._-')}"

    def download_strategy(self, bucket: str, key: str) -> dict[str, Any]:
        """GET download-strategy; callers must still proxy bytes, never expose signed URLs to the browser."""
        self._validate_bucket(bucket)
        safe_key = quote(key.lstrip("/"), safe="/._-")
        payload = self._json(self._request("GET", f"/api/storage/buckets/{bucket}/download-strategy/objects/{safe_key}"))
        return payload if isinstance(payload, dict) else {"raw": payload}

    def head_object(self, bucket: str, key: str) -> dict[str, Any]:
        """HEAD an object; used for resume/checksum without downloading bytes."""
        self._validate_bucket(bucket)
        safe_key = quote(key.lstrip("/"), safe="/._-")
        response = self._request("HEAD", f"/api/storage/buckets/{bucket}/objects/{safe_key}")
        return {
            "bucket": bucket,
            "key": key,
            "status": response.status_code,
            "content_length": response.headers.get("content-length"),
            "etag": response.headers.get("etag", "").strip('"'),
            "content_type": response.headers.get("content-type", ""),
        }

    @staticmethod
    def _validate_bucket(bucket: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", bucket):
            raise ValueError("invalid InsForge bucket name")


class InsForgeSearchAdapter(_HTTPAdapter):
    def search(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        if not config.INSFORGE_TRUSTED:
            raise InsForgeUnavailable("InsForge search 尚未標記為 trusted")
        result = InsForgeDatabaseAdapter().rpc(config.INSFORGE_SEARCH_RPC, payload)
        if isinstance(result, dict):
            result = result.get("data", result.get("results", []))
        return result if isinstance(result, list) else []


class InsForgeFunctionAdapter(_HTTPAdapter):
    def invoke(self, slug: str, payload: dict[str, Any], *, method: str = "POST") -> Any:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", slug):
            raise ValueError("invalid InsForge function slug")
        if not config.INSFORGE_TRUSTED:
            raise InsForgeUnavailable("InsForge function 尚未標記為 trusted")
        return self._json(self._request(method.upper(), f"/{config.INSFORGE_FUNCTION_PREFIX}/{slug}", json=payload))


@dataclass
class InsForgeAdapters:
    database: InsForgeDatabaseAdapter
    storage: InsForgeStorageAdapter
    search: InsForgeSearchAdapter
    function: InsForgeFunctionAdapter


_ADAPTERS: InsForgeAdapters | None = None


def get_insforge_adapters() -> InsForgeAdapters:
    global _ADAPTERS
    if _ADAPTERS is None:
        _ADAPTERS = InsForgeAdapters(InsForgeDatabaseAdapter(), InsForgeStorageAdapter(), InsForgeSearchAdapter(), InsForgeFunctionAdapter())
    return _ADAPTERS


def reset_insforge_adapters() -> None:
    global _ADAPTERS
    _ADAPTERS = None
