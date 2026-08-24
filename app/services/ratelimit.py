"""輕量的行程內滑動視窗限流。

登入的暴力嘗試鎖定在 auth.py（有失敗計數與鎖定語意）；
這裡處理的是「已登入使用者的請求頻率」——特別是 /api/chat，
每一次都會呼叫外部模型、消耗 NVIDIA 額度。

單一行程、SQLite 等級的部署規模用記憶體結構就夠；
多行程部署時每個行程各自限流，上限仍然存在，只是變成 N 倍。
"""

from __future__ import annotations

import time
from collections import deque
from threading import Lock

_lock = Lock()
_hits: dict[str, deque[float]] = {}

# 防止 key 無限增長：超過這個數量就清掉過期的 bucket
_MAX_KEYS = 4096


def allow(key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
    """回傳 (是否放行, 建議 Retry-After 秒數)。"""
    now = time.monotonic()
    with _lock:
        if len(_hits) > _MAX_KEYS:
            stale = [k for k, q in _hits.items() if not q or q[-1] < now - window_seconds]
            for k in stale:
                _hits.pop(k, None)
        q = _hits.setdefault(key, deque())
        cutoff = now - window_seconds
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= max(1, limit):
            retry_after = int(q[0] + window_seconds - now) + 1
            return False, max(retry_after, 1)
        q.append(now)
        return True, 0


def reset() -> None:
    """測試用。"""
    with _lock:
        _hits.clear()
