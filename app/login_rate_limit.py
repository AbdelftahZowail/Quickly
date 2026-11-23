"""In-memory brute-force limiter for login and first-admin registration.

Two independent sliding windows guard ``POST /api/auth/login``:

* per client IP    — ``QUICKLY_LOGIN_MAX_FAILURES_PER_IP`` (default 10) over
                     ``QUICKLY_LOGIN_WINDOW_SECONDS`` (default 300);
* per identifier   — ``QUICKLY_LOGIN_MAX_FAILURES_PER_ACCOUNT`` (default 5)
                     over ``QUICKLY_LOGIN_ACCOUNT_WINDOW_SECONDS``
                     (default 900).  The identifier is the lowercased
                     username *or* email, so "" / mixed case cannot be used
                     to dodge the limit.

Exceeding either threshold locks that bucket for
``QUICKLY_LOGIN_LOCK_SECONDS`` (default 900) — the caller must answer 429
with ``Retry-After``.  Registration has its own per-IP window (same
``QUICKLY_LOGIN_MAX_FAILURES_PER_IP`` / ``QUICKLY_LOGIN_WINDOW_SECONDS`` /
lock values) so guessing the setup code is throttled too, without letting
login failures lock out registration.

Only *failed* attempts are recorded (bad credentials, wrong setup code); a
successful login or registration clears the relevant buckets.  Checks happen
before credentials are verified, so a locked bucket cannot be used as an
oracle for a valid password.

State is process-local and dependency-free: with multiple workers each holds
its own counters, so the effective rate is multiplied by the worker count.
That is an acceptable best-effort defence; use a shared store if you need
exact global limits.  All settings are re-read on every call and
``reset_for_tests()`` clears everything.
"""
from __future__ import annotations

import math
import os
import threading
import time
from collections import deque

MAX_FAILURES_PER_IP_ENV = "QUICKLY_LOGIN_MAX_FAILURES_PER_IP"
MAX_FAILURES_PER_ACCOUNT_ENV = "QUICKLY_LOGIN_MAX_FAILURES_PER_ACCOUNT"
WINDOW_ENV = "QUICKLY_LOGIN_WINDOW_SECONDS"
ACCOUNT_WINDOW_ENV = "QUICKLY_LOGIN_ACCOUNT_WINDOW_SECONDS"
LOCK_ENV = "QUICKLY_LOGIN_LOCK_SECONDS"

DEFAULT_MAX_FAILURES_PER_IP = 10
DEFAULT_MAX_FAILURES_PER_ACCOUNT = 5
DEFAULT_WINDOW_SECONDS = 300.0
DEFAULT_ACCOUNT_WINDOW_SECONDS = 900.0
DEFAULT_LOCK_SECONDS = 900.0

# Generic message; says nothing about whether the account/IP exists.
LOGIN_RATE_LIMIT_DETAIL = "Too many login attempts. Try again later."
REGISTER_RATE_LIMIT_DETAIL = "Too many registration attempts. Try again later."


class _Bucket:
    """Failure timestamps plus an optional lock expiry for one key."""

    __slots__ = ("failures", "locked_until")

    def __init__(self) -> None:
        self.failures: deque[float] = deque()
        self.locked_until: float = 0.0


_lock = threading.Lock()
_login_by_ip: dict[str, _Bucket] = {}
_login_by_identifier: dict[str, _Bucket] = {}
_register_by_ip: dict[str, _Bucket] = {}


def _now() -> float:
    """Clock indirection so tests can advance time without sleeping."""
    return time.time()


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


def _config() -> tuple[int, int, float, float, float]:
    """(max/ip, max/account, ip window, account window, lock) from the env."""
    return (
        max(1, _env_int(MAX_FAILURES_PER_IP_ENV, DEFAULT_MAX_FAILURES_PER_IP)),
        max(
            1,
            _env_int(
                MAX_FAILURES_PER_ACCOUNT_ENV, DEFAULT_MAX_FAILURES_PER_ACCOUNT
            ),
        ),
        max(1.0, _env_float(WINDOW_ENV, DEFAULT_WINDOW_SECONDS)),
        max(1.0, _env_float(ACCOUNT_WINDOW_ENV, DEFAULT_ACCOUNT_WINDOW_SECONDS)),
        max(1.0, _env_float(LOCK_ENV, DEFAULT_LOCK_SECONDS)),
    )


def normalize_identifier(identifier: str | None) -> str:
    """Canonical account key: usernames and emails are case-insensitive."""
    return (identifier or "").strip().lower()


def _prune(bucket: _Bucket, window: float, now: float) -> None:
    cutoff = now - window
    while bucket.failures and bucket.failures[0] <= cutoff:
        bucket.failures.popleft()


def _wait_seconds(bucket: _Bucket | None, max_failures: int, window: float, now: float) -> float:
    """Seconds until *bucket* may be retried (0 = allowed right now)."""
    if bucket is None:
        return 0.0
    if bucket.locked_until > now:
        return bucket.locked_until - now
    if bucket.locked_until:
        # Lock has expired: the bucket starts fresh.
        return 0.0
    _prune(bucket, window, now)
    if len(bucket.failures) >= max_failures:
        # Max may have been lowered at runtime while failures are on record.
        return max(bucket.failures[0] + window - now, 1.0)
    return 0.0


def _record(
    state: dict[str, _Bucket],
    key: str,
    max_failures: int,
    window: float,
    lock_seconds: float,
    now: float,
) -> None:
    bucket = state.get(key)
    if bucket is None:
        bucket = _Bucket()
        state[key] = bucket
    if bucket.locked_until and bucket.locked_until <= now:
        # Previous lock expired — forget the old failures.
        bucket.locked_until = 0.0
        bucket.failures.clear()
    _prune(bucket, window, now)
    bucket.failures.append(now)
    if len(bucket.failures) >= max_failures:
        bucket.locked_until = now + lock_seconds


def _reset(state: dict[str, _Bucket], key: str) -> None:
    state.pop(key, None)


def _clear_state(state: dict[str, _Bucket]) -> None:
    state.clear()


def _prune_state(state: dict[str, _Bucket], window: float, lock_seconds: float, now: float) -> None:
    """Drop buckets whose failures and locks are all in the past."""
    horizon = max(window, lock_seconds)
    stale = [
        key
        for key, bucket in state.items()
        if bucket.locked_until <= now
        and (not bucket.failures or bucket.failures[-1] <= now - horizon)
    ]
    for key in stale:
        state.pop(key, None)


def _retry_after(waits: list[float]) -> int | None:
    blocking = [w for w in waits if w > 0]
    if not blocking:
        return None
    return max(1, math.ceil(max(blocking)))


def check_login(client_ip: str | None, identifier: str | None) -> int | None:
    """Return ``Retry-After`` seconds when either login bucket is locked."""
    ip_key = client_ip or "unknown"
    identifier_key = normalize_identifier(identifier) or "unknown"
    max_ip, max_account, ip_window, account_window, lock_seconds = _config()
    now = _now()
    with _lock:
        _prune_state(_login_by_ip, ip_window, lock_seconds, now)
        _prune_state(_login_by_identifier, account_window, lock_seconds, now)
        waits = [
            _wait_seconds(_login_by_ip.get(ip_key), max_ip, ip_window, now),
            _wait_seconds(
                _login_by_identifier.get(identifier_key),
                max_account,
                account_window,
                now,
            ),
        ]
    return _retry_after(waits)


def record_login_failure(client_ip: str | None, identifier: str | None) -> None:
    """Count one bad-credentials attempt against the IP *and* the account."""
    ip_key = client_ip or "unknown"
    identifier_key = normalize_identifier(identifier) or "unknown"
    max_ip, max_account, ip_window, account_window, lock_seconds = _config()
    now = _now()
    with _lock:
        _record(_login_by_ip, ip_key, max_ip, ip_window, lock_seconds, now)
        _record(
            _login_by_identifier,
            identifier_key,
            max_account,
            account_window,
            lock_seconds,
            now,
        )


def clear_login(client_ip: str | None, identifier: str | None) -> None:
    """Forget failures after a successful login (account and IP buckets)."""
    ip_key = client_ip or "unknown"
    identifier_key = normalize_identifier(identifier) or "unknown"
    with _lock:
        _reset(_login_by_ip, ip_key)
        _reset(_login_by_identifier, identifier_key)


def check_register(client_ip: str | None) -> int | None:
    """Return ``Retry-After`` seconds when the registration IP is locked."""
    ip_key = client_ip or "unknown"
    max_ip, _max_account, ip_window, _account_window, lock_seconds = _config()
    now = _now()
    with _lock:
        _prune_state(_register_by_ip, ip_window, lock_seconds, now)
        wait = _wait_seconds(_register_by_ip.get(ip_key), max_ip, ip_window, now)
    return _retry_after([wait])


def record_register_failure(client_ip: str | None) -> None:
    """Count one rejected registration (e.g. wrong setup code)."""
    ip_key = client_ip or "unknown"
    max_ip, _max_account, ip_window, _account_window, lock_seconds = _config()
    with _lock:
        _record(_register_by_ip, ip_key, max_ip, ip_window, lock_seconds, _now())


def clear_register(client_ip: str | None) -> None:
    """Forget registration failures after a successful signup."""
    with _lock:
        _reset(_register_by_ip, client_ip or "unknown")


def reset_for_tests() -> None:
    """Clear every bucket (tests run in one process)."""
    with _lock:
        _clear_state(_login_by_ip)
        _clear_state(_login_by_identifier)
        _clear_state(_register_by_ip)
