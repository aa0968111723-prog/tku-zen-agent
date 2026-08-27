"""Shared outbound HTTP guards: SSRF host checks, circuit breaker, correlation ids.

Used by InsForge adapters and any other server-only remote client. Never logs
secrets. User-supplied URLs must pass ``assert_public_https_url`` before fetch.
"""

from __future__ import annotations

import ipaddress
import socket
import time
import uuid
from dataclasses import dataclass, field
from urllib.parse import urlparse


class UnsafeOutboundURL(ValueError):
    """The destination is not a public HTTPS host we are allowed to call."""


_BLOCKED_HOSTS = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
    "metadata.google.internal",
    "metadata.google.internal.",
}


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def _is_private_ip(value: str) -> bool:
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        return False
    return bool(
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def assert_public_https_url(url: str, *, allow_http_loopback_for_tests: bool = False) -> str:
    """Reject file://, data:, internal hosts, and RFC1918 literals.

    DNS is resolved and every address must be public. Used before any
    user-influenced outbound fetch.
    """
    raw = (url or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"https", "http"}:
        raise UnsafeOutboundURL("只允許 http/https 連線")
    if parsed.scheme == "http" and not allow_http_loopback_for_tests:
        raise UnsafeOutboundURL("對外連線必須使用 HTTPS")
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host or host in _BLOCKED_HOSTS or host.endswith(".local") or host.endswith(".internal"):
        raise UnsafeOutboundURL("禁止連到本機或內部主機")
    if _is_private_ip(host):
        raise UnsafeOutboundURL("禁止連到私有 IP")
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise UnsafeOutboundURL("無法解析遠端主機") from exc
    for info in infos:
        sockaddr = info[4][0]
        if _is_private_ip(str(sockaddr)):
            raise UnsafeOutboundURL("遠端主機解析到私有 IP")
    return raw


@dataclass
class CircuitBreaker:
    """Process-local breaker so a down remote does not stall every request."""

    failure_threshold: int = 5
    reset_after_seconds: float = 30.0
    failures: int = 0
    open_until: float = 0.0
    _now: callable = field(default=time.monotonic, repr=False)

    def allow(self) -> bool:
        if self.open_until and self._now() < self.open_until:
            return False
        if self.open_until and self._now() >= self.open_until:
            self.open_until = 0.0
            self.failures = 0
        return True

    def record_success(self) -> None:
        self.failures = 0
        self.open_until = 0.0

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self.open_until = self._now() + self.reset_after_seconds
