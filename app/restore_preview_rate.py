"""Simple in-memory rate limit for unauthenticated restore preview."""
from __future__ import annotations

import time
from collections import deque

_WINDOW_SEC = 3600
_MAX_PER_WINDOW = 30

_by_ip: dict[str, deque[float]] = {}


def allow_restore_preview(client_ip: str) -> bool:
    now = time.time()
    dq = _by_ip.setdefault(client_ip, deque())
    while dq and dq[0] < now - _WINDOW_SEC:
        dq.popleft()
    if len(dq) >= _MAX_PER_WINDOW:
        return False
    dq.append(now)
    return True
