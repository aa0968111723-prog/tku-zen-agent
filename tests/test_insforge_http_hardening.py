"""HTTP hardening for the server-only InsForge adapter: SSRF, redirects, breaker."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from app.services.http_guard import CircuitBreaker, UnsafeOutboundURL, assert_public_https_url
from app.services.insforge_adapters import (
    BLOCKED_BY_EXTERNAL_DEPENDENCY,
    InsForgeDatabaseAdapter,
    InsForgeUnavailable,
    _BREAKER,
    reset_insforge_adapters,
)


def test_rejects_loopback_and_metadata_urls():
    with pytest.raises(UnsafeOutboundURL):
        assert_public_https_url("http://127.0.0.1/secret")
    with pytest.raises(UnsafeOutboundURL):
        assert_public_https_url("https://169.254.169.254/latest/meta-data")
    with pytest.raises(UnsafeOutboundURL):
        assert_public_https_url("file:///etc/passwd")
    with pytest.raises(UnsafeOutboundURL):
        assert_public_https_url("https://localhost/admin")


def test_circuit_breaker_opens_after_threshold():
    breaker = CircuitBreaker(failure_threshold=2, reset_after_seconds=60, _now=lambda: 1.0)
    assert breaker.allow()
    breaker.record_failure()
    assert breaker.allow()
    breaker.record_failure()
    assert breaker.allow() is False
    breaker.record_success()
    assert breaker.allow()


def test_insforge_refuses_private_base_url(monkeypatch):
    reset_insforge_adapters()
    monkeypatch.setattr("app.config.INSFORGE_BASE_URL", "https://127.0.0.1:7130")
    monkeypatch.setattr("app.config.INSFORGE_SERVICE_KEY", "ik_test")
    adapter = InsForgeDatabaseAdapter()
    adapter.base_url = "https://127.0.0.1:7130"
    adapter.key = "ik_test"
    with pytest.raises(InsForgeUnavailable) as exc:
        adapter._request("GET", "/api/health")
    assert exc.value.code == BLOCKED_BY_EXTERNAL_DEPENDENCY


def test_insforge_does_not_follow_redirects(monkeypatch):
    reset_insforge_adapters()
    _BREAKER.record_success()
    captured = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            captured["follow_redirects"] = kwargs.get("follow_redirects")
            captured["base_url"] = kwargs.get("base_url")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def request(self, method, path, **kwargs):
            request = httpx.Request(method, "https://ge5cr87f.us-east.insforge.app" + path)
            return httpx.Response(200, request=request, json={"status": "ok"})

    monkeypatch.setattr("app.services.insforge_adapters.httpx.Client", FakeClient)
    monkeypatch.setattr("app.services.insforge_adapters.assert_public_https_url", lambda url: url)
    adapter = InsForgeDatabaseAdapter()
    adapter.base_url = "https://ge5cr87f.us-east.insforge.app"
    adapter.key = "ik_test"
    adapter._request("GET", "/api/health")
    assert captured["follow_redirects"] is False


def test_circuit_open_short_circuits(monkeypatch):
    _BREAKER.failures = 99
    _BREAKER.open_until = 10**12
    adapter = InsForgeDatabaseAdapter()
    adapter.base_url = "https://example.insforge.app"
    adapter.key = "ik_test"
    with pytest.raises(InsForgeUnavailable, match="circuit breaker"):
        adapter._request("GET", "/api/health")
    _BREAKER.record_success()


def test_headers_include_bearer_and_x_api_key(monkeypatch):
    reset_insforge_adapters()
    adapter = InsForgeDatabaseAdapter()
    adapter.base_url = "https://example.insforge.app"
    adapter.key = "ik_test"
    headers = adapter._headers(json_body=True, request_id="rid")
    assert headers["Authorization"] == "Bearer ik_test"
    assert headers["x-api-key"] == "ik_test"
    assert headers["X-Request-Id"] == "rid"
    assert "ik_test" not in repr(adapter)


class _ScriptedClient:
    def __init__(self, script, captured):
        self._script = script
        self._captured = captured

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def request(self, method, path, **kwargs):
        self._captured.append({"method": method, "path": path, "headers": kwargs.get("headers") or {}})
        status, body = self._script.pop(0)
        request = httpx.Request(method, "https://example.insforge.app" + path)
        return httpx.Response(status, request=request, json=body)


def test_upload_uses_direct_strategy_then_put(monkeypatch):
    reset_insforge_adapters()
    _BREAKER.record_success()
    captured: list[dict] = []
    script = [
        (200, {"method": "direct", "uploadUrl": "/api/storage/buckets/visual-assets/objects/a.png", "key": "a.png"}),
        (200, {"key": "a.png", "url": "/api/storage/buckets/visual-assets/objects/a.png", "size": 4}),
    ]

    def fake_client(*args, **kwargs):
        captured.append({"follow_redirects": kwargs.get("follow_redirects")})
        return _ScriptedClient(script, captured)

    monkeypatch.setattr("app.services.insforge_adapters.httpx.Client", fake_client)
    monkeypatch.setattr("app.services.insforge_adapters.assert_public_https_url", lambda url: url)
    monkeypatch.setattr("app.config.INSFORGE_TRUSTED", True)
    from app.services.insforge_adapters import InsForgeStorageAdapter

    adapter = InsForgeStorageAdapter()
    adapter.base_url = "https://example.insforge.app"
    adapter.key = "ik_test"
    result = adapter.upload_bytes("visual-assets", "a.png", b"abcd", "image/png")
    methods = [item.get("method") for item in captured if "method" in item]
    paths = [item.get("path") for item in captured if "path" in item]
    assert methods == ["POST", "PUT"]
    assert paths[0].endswith("/upload-strategy")
    assert result["key"] == "a.png"
    assert result["size"] == 4


def test_upload_falls_back_to_put_when_strategy_missing(monkeypatch):
    reset_insforge_adapters()
    _BREAKER.record_success()
    captured: list[dict] = []
    script = [
        (404, {"error": "NOT_FOUND"}),
        (200, {"key": "b.png", "size": 3}),
    ]

    def fake_client(*args, **kwargs):
        return _ScriptedClient(script, captured)

    monkeypatch.setattr("app.services.insforge_adapters.httpx.Client", fake_client)
    monkeypatch.setattr("app.services.insforge_adapters.assert_public_https_url", lambda url: url)
    monkeypatch.setattr("app.config.INSFORGE_TRUSTED", True)
    from app.services.insforge_adapters import InsForgeStorageAdapter

    adapter = InsForgeStorageAdapter()
    adapter.base_url = "https://example.insforge.app"
    adapter.key = "ik_test"
    result = adapter.upload_bytes("visual-assets", "b.png", b"xyz", "image/png")
    paths = [item.get("path") for item in captured if "path" in item]
    assert any(str(path).endswith("/objects/b.png") for path in paths)
    assert result["key"] == "b.png"


def test_post_timeout_is_not_retried(monkeypatch):
    reset_insforge_adapters()
    _BREAKER.record_success()
    calls: list[str] = []

    class TimeoutClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def request(self, method, path, **kwargs):
            calls.append(method)
            raise httpx.TimeoutException("timed out")

    monkeypatch.setattr("app.services.insforge_adapters.httpx.Client", TimeoutClient)
    monkeypatch.setattr("app.services.insforge_adapters.assert_public_https_url", lambda url: url)
    monkeypatch.setattr("app.config.INSFORGE_HTTP_ATTEMPTS", 4)
    adapter = InsForgeDatabaseAdapter()
    adapter.base_url = "https://example.insforge.app"
    adapter.key = "ik_test"
    with pytest.raises(InsForgeUnavailable):
        adapter.insert("projects", [{"id": "p1", "name": "x"}])
    assert calls == ["POST"]
    assert _BREAKER.failures >= 1
    _BREAKER.record_success()


def test_get_service_unavailable_is_retried(monkeypatch):
    reset_insforge_adapters()
    _BREAKER.record_success()
    captured: list[dict] = []
    script = [(503, {"error": "busy"}), (200, {"status": "ok"})]
    monkeypatch.setattr("app.services.insforge_adapters.httpx.Client", lambda *a, **k: _ScriptedClient(script, captured))
    monkeypatch.setattr("app.services.insforge_adapters.assert_public_https_url", lambda url: url)
    monkeypatch.setattr("app.config.INSFORGE_HTTP_ATTEMPTS", 2)
    monkeypatch.setattr("app.services.insforge_adapters.time.sleep", lambda _s: None)
    adapter = InsForgeDatabaseAdapter()
    adapter.base_url = "https://example.insforge.app"
    adapter.key = "ik_test"
    adapter._request("GET", "/api/health")
    assert [item.get("method") for item in captured if "method" in item] == ["GET", "GET"]
    _BREAKER.record_success()


def test_client_errors_do_not_open_circuit_breaker(monkeypatch):
    reset_insforge_adapters()
    _BREAKER.record_success()
    captured: list[dict] = []

    def always_404(*args, **kwargs):
        return _ScriptedClient([(404, {"error": "missing"}) for _ in range(8)], captured)

    monkeypatch.setattr("app.services.insforge_adapters.httpx.Client", always_404)
    monkeypatch.setattr("app.services.insforge_adapters.assert_public_https_url", lambda url: url)
    adapter = InsForgeDatabaseAdapter()
    adapter.base_url = "https://example.insforge.app"
    adapter.key = "ik_test"
    for _ in range(6):
        captured.clear()
        with pytest.raises(InsForgeUnavailable):
            adapter._request("GET", "/api/health")
    assert _BREAKER.allow() is True
    _BREAKER.record_success()


def test_repeated_server_errors_open_circuit_breaker(monkeypatch):
    reset_insforge_adapters()
    _BREAKER.record_success()
    captured: list[dict] = []

    def always_503(*args, **kwargs):
        return _ScriptedClient([(503, {"error": "down"})], captured)

    monkeypatch.setattr("app.services.insforge_adapters.httpx.Client", always_503)
    monkeypatch.setattr("app.services.insforge_adapters.assert_public_https_url", lambda url: url)
    monkeypatch.setattr("app.config.INSFORGE_HTTP_ATTEMPTS", 1)
    adapter = InsForgeDatabaseAdapter()
    adapter.base_url = "https://example.insforge.app"
    adapter.key = "ik_test"
    breaker = CircuitBreaker(failure_threshold=3, reset_after_seconds=60, _now=lambda: 1.0)
    monkeypatch.setattr("app.services.insforge_adapters._BREAKER", breaker)
    for _ in range(3):
        captured.clear()
        with pytest.raises(InsForgeUnavailable):
            adapter._request("GET", "/api/health")
    assert breaker.allow() is False
