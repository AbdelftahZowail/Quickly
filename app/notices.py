"""In-app notices and version feed for self-hosted Quickly installs.

Reads two *public* hub feeds — release versions and announcements — and exposes
them to the logged-in UI through the local backend (the browser must not call
the hub directly, because of CORS).

Privacy posture: these are public reads. The only thing this module sends to the
hub is the constant ``product=quickly`` in the query string; nothing about the
install — no instance id, no version, no host, no usage — is transmitted. The
hub base URL is the same one telemetry uses (``app.telemetry.telemetry_url``),
but this module never sends a key and never writes anything.

It is fail-open: it never raises into a request, keeps a short-lived in-process
cache (``CACHE_SECONDS``), serves the last good copy when the hub is briefly
unreachable, and otherwise reports ``unavailable: True`` with empty lists.
"""
from __future__ import annotations

import logging
import re
import time

import httpx

from app.telemetry import app_version, telemetry_url

log = logging.getLogger("quickly.notices")

# Constant product filter. Nothing else about the install is part of the call.
PRODUCT = "quickly"

REQUEST_TIMEOUT_SECONDS = 8.0
# One product, one cache. A release/announcement feed changes at most a few
# times a day, so six hours is plenty and keeps the hub load negligible.
CACHE_SECONDS = 6 * 3600

# ``X.Y.Z`` core plus optional ``-prerelease``/``+build`` suffixes. Small,
# self-contained semver parsing — deliberately not the ``packaging`` module.
_VERSION_RE = re.compile(
    r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.\-]+))?(?:\+([0-9A-Za-z.\-]+))?$"
)

# ``{"data": <dict>, "at": <monotonic seconds>}`` or empty on a cold start.
_cache: dict = {}


def _reset_cache() -> None:
    """Drop the in-process cache (used by tests; also safe to call at runtime)."""
    _cache.clear()


def parse_version(name: object) -> tuple[int, int, int, str | None] | None:
    """Parse ``X.Y.Z[-pre][+build]`` (an optional ``v`` prefix is allowed).

    Returns ``(major, minor, patch, prerelease)`` where ``prerelease`` is
    ``None`` for a release, or ``None`` when the name is not a valid version.
    """
    if not isinstance(name, str):
        return None
    match = _VERSION_RE.match(name.strip())
    if not match:
        return None
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
        match.group(4),
    )


def version_key(name: object) -> tuple[int, int, int, int, str] | None:
    """Sort key for a version name. A prerelease sorts BELOW its release.

    The core ``X.Y.Z`` numbers compare numerically; the fourth element is ``1``
    for a release and ``0`` for a prerelease, so ``2.4.0`` outranks
    ``2.4.0-beta.1``. Prerelease strings compare lexically (a small, predictable
    rule — the hub's versions are release tags, not arbitrary semver).
    """
    parsed = parse_version(name)
    if parsed is None:
        return None
    major, minor, patch, prerelease = parsed
    return (
        major,
        minor,
        patch,
        0 if prerelease is not None else 1,
        prerelease or "",
    )


def compare_versions(a: object, b: object) -> int | None:
    """Return -1/0/1 comparing two version names, or ``None`` if either is invalid."""
    key_a = version_key(a)
    key_b = version_key(b)
    if key_a is None or key_b is None:
        return None
    if key_a < key_b:
        return -1
    if key_a > key_b:
        return 1
    return 0


def _latest_version(versions: list) -> str | None:
    """Highest valid ``name`` in the feed, or ``None`` when none are valid."""
    best_name: str | None = None
    best_key = None
    for item in versions or []:
        name = item.get("name") if isinstance(item, dict) else None
        key = version_key(name)
        if key is None:
            continue
        if best_key is None or key > best_key:
            best_key = key
            best_name = name
    return best_name


def _empty(unavailable: bool) -> dict:
    return {
        "announcements": [],
        "versions": [],
        "current_version": app_version(),
        "latest_version": None,
        "update_available": False,
        "unavailable": unavailable,
    }


def _compose(announcements: list, versions: list) -> dict:
    versions = versions or []
    latest = _latest_version(versions)
    current = app_version()
    update_available = False
    if latest is not None:
        comparison = compare_versions(latest, current)
        # Only a valid local version can be compared; "unknown" / dev builds
        # never report an update.
        update_available = comparison is not None and comparison > 0
    return {
        "announcements": announcements or [],
        "versions": versions,
        "current_version": current,
        "latest_version": latest,
        "update_available": update_available,
        "unavailable": False,
    }


async def _fetch_feeds() -> tuple[list, list]:
    """Fetch both public feeds. Raises on any HTTP/network error (caller handles)."""
    base = telemetry_url()
    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT_SECONDS,
        follow_redirects=False,
    ) as client:
        announcements_resp = await client.get(
            f"{base}/v1/announcements", params={"product": PRODUCT}
        )
        announcements_resp.raise_for_status()
        announcements = (announcements_resp.json() or {}).get("announcements", [])

        versions_resp = await client.get(f"{base}/v1/versions", params={"product": PRODUCT})
        versions_resp.raise_for_status()
        versions = (versions_resp.json() or {}).get("versions", [])

    return announcements, versions


async def get_notices() -> dict:
    """Return announcements + version status. Never raises; fails open.

    Uses a ``CACHE_SECONDS`` in-process cache. On any error it serves the last
    good copy if one exists, otherwise empty lists with ``unavailable: True``.
    """
    now = time.monotonic()
    last_good = _cache.get("data")
    if last_good is not None and now - float(_cache.get("at", 0.0)) < CACHE_SECONDS:
        return last_good

    try:
        announcements, versions = await _fetch_feeds()
    except Exception:
        log.debug("notices: hub feed unavailable", exc_info=True)
        if last_good is not None:
            return last_good
        return _empty(unavailable=True)

    result = _compose(announcements, versions)
    _cache["data"] = result
    _cache["at"] = now
    return result
