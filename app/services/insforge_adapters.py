"""Server-only, replaceable adapters for the optional InsForge backend.

No browser code imports this module.  The local visual store remains the
source-of-truth; callers may explicitly opt into a remote sync and receive a
structured ``BLOCKED_BY_EXTERNAL_DEPENDENCY`` result when InsForge is absent.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

from .. import config


BLOCKED_BY_EXTERNAL_DEPENDENCY = "BLOCKED_BY_EXTERNAL_DEPENDENCY"


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

    def _headers(self, *, json_body: bool = False) -> dict[str, str]:
        self._require_configured()
        headers = {"Authorization": f"Bearer {self.key}", "apikey": self.key}
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        if not path.startswith("/"):
            path = "/" + path
        try:
            with httpx.Client(base_url=self.base_url, timeout=config.INSFORGE_TIMEOUT_SECONDS, follow_redirects=True) as client:
                request_headers = self._headers(json_body="json" in kwargs)
                request_headers.update(kwargs.pop("headers", {}) or {})
                response = client.request(method, path, headers=request_headers, **kwargs)
                response.raise_for_status()
                return response
        except InsForgeUnavailable:
            raise
        except (httpx.HTTPError, OSError) as exc:
            raise InsForgeUnavailable(f"InsForge request failed: {exc}", detail=str(exc)) from exc

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
        payload = self._json(self._request("GET", f"/rest/v1/{table}", params=query))
        return payload if isinstance(payload, list) else payload.get("data", []) if isinstance(payload, dict) else []

    def insert(self, table: str, rows: list[dict[str, Any]] | dict[str, Any]) -> list[dict[str, Any]]:
        self._validate_table(table)
        body = rows if isinstance(rows, list) else [rows]
        response = self._request("POST", f"/rest/v1/{table}", json=body, headers={**self._headers(json_body=True), "Prefer": "return=representation"})
        payload = self._json(response)
        return payload if isinstance(payload, list) else payload.get("data", []) if isinstance(payload, dict) else []

    def upsert(self, table: str, rows: list[dict[str, Any]] | dict[str, Any], *, on_conflict: str = "id") -> list[dict[str, Any]]:
        self._validate_table(table)
        body = rows if isinstance(rows, list) else [rows]
        response = self._request("POST", f"/rest/v1/{table}", params={"on_conflict": on_conflict}, json=body, headers={**self._headers(json_body=True), "Prefer": "resolution=merge-duplicates,return=representation"})
        payload = self._json(response)
        return payload if isinstance(payload, list) else payload.get("data", []) if isinstance(payload, dict) else []

    def update(self, table: str, filters: dict[str, str], values: dict[str, Any]) -> list[dict[str, Any]]:
        self._validate_table(table)
        response = self._request("PATCH", f"/rest/v1/{table}", params=filters, json=values, headers={**self._headers(json_body=True), "Prefer": "return=representation"})
        payload = self._json(response)
        return payload if isinstance(payload, list) else payload.get("data", []) if isinstance(payload, dict) else []

    def rpc(self, name: str, payload: dict[str, Any]) -> Any:
        if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,62}", name):
            raise ValueError("invalid InsForge RPC name")
        return self._json(self._request("POST", f"/rest/v1/rpc/{name}", json=payload))

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
    """Storage boundary using InsForge's REST upload-strategy helper.

    The helper returns a presigned/direct-upload description.  We never expose
    the service key or a public object URL to the browser.
    """

    def upload_bytes(self, bucket: str, key: str, content: bytes, mime_type: str) -> dict[str, Any]:
        if not config.INSFORGE_TRUSTED:
            raise InsForgeUnavailable("InsForge storage 尚未標記為 trusted，禁止送出檔案")
        strategy_response = self._request(
            "POST", f"/api/storage/buckets/{bucket}/upload-strategy",
            json={"key": key, "contentType": mime_type, "upsert": False},
        )
        strategy = self._json(strategy_response)
        data = strategy.get("data", strategy) if isinstance(strategy, dict) else {}
        url = data.get("url") or data.get("uploadUrl")
        if not url:
            raise InsForgeUnavailable("InsForge 未回傳 upload strategy URL", detail=data)
        method = str(data.get("method") or "PUT").upper()
        headers = {str(k): str(v) for k, v in (data.get("headers") or {}).items()}
        fields = data.get("fields") or {}
        try:
            with httpx.Client(timeout=config.INSFORGE_TIMEOUT_SECONDS, follow_redirects=True) as client:
                if fields:
                    response = client.post(url, data=fields, files={"file": (key.rsplit("/", 1)[-1], content, mime_type)}, headers=headers)
                else:
                    response = client.request(method, url, content=content, headers={"Content-Type": mime_type, **headers})
                response.raise_for_status()
        except (httpx.HTTPError, OSError) as exc:
            raise InsForgeUnavailable(f"InsForge storage upload failed: {exc}", detail=str(exc)) from exc
        return {"bucket": bucket, "key": key, "url": url, "size": len(content)}

    def object_url(self, bucket: str, key: str) -> str:
        self._require_configured()
        # This is a server-side reference only; ACL is still enforced by the
        # database and callers should proxy downloads rather than expose keys.
        return f"{self.base_url}/api/storage/buckets/{bucket}/objects/{key.lstrip('/')}"


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
