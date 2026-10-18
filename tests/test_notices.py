"""Notices/version feed tests for ``app/notices.py``.

Pins the fail-open behaviour, the public ``product=quickly`` request shape, and
the small self-contained semver comparison used to decide ``latest_version`` and
``update_available``.
"""
from __future__ import annotations

import httpx
import pytest

from app import notices

HUB = "https://hub.example"
# Captured before any monkeypatching, so repeated patches in one test still wrap
# the real client (and its injected transport never gets double-wrapped).
_ORIGINAL_ASYNC_CLIENT = httpx.AsyncClient


@pytest.fixture(autouse=True)
def _clear_notices_cache():
    """The in-process cache is module-global; never leak state between tests."""
    notices._reset_cache()
    yield
    notices._reset_cache()


@pytest.fixture(autouse=True)
def _configure(monkeypatch):
    monkeypatch.setattr(notices, "telemetry_url", lambda: HUB)


def _patch_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return _ORIGINAL_ASYNC_CLIENT(*args, **kwargs)

    monkeypatch.setattr(notices.httpx, "AsyncClient", factory)


def _feed_handler(versions=None, announcements=None, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/announcements"):
            if status != 200:
                return httpx.Response(status, request=request)
            return httpx.Response(
                200,
                json={"product": "quickly", "announcements": announcements or []},
                request=request,
            )
        if request.url.path.endswith("/v1/versions"):
            if status != 200:
                return httpx.Response(status, request=request)
            return httpx.Response(
                200,
                json={
                    "source": "github",
                    "product": "quickly",
                    "repo": "example/quickly",
                    "versions": versions or [],
                },
                request=request,
            )
        return httpx.Response(404, request=request)

    return handler


# ---------------------------------------------------------------------------
# Fail-open
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_http_500_fails_open(monkeypatch):
    _patch_transport(monkeypatch, _feed_handler(status=500))
    result = await notices.get_notices()
    assert result["unavailable"] is True
    assert result["announcements"] == []
    assert result["versions"] == []
    assert result["latest_version"] is None
    assert result["update_available"] is False


@pytest.mark.asyncio
async def test_connection_error_fails_open(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("hub down", request=request)

    _patch_transport(monkeypatch, handler)
    result = await notices.get_notices()
    assert result["unavailable"] is True
    assert result["announcements"] == [] and result["versions"] == []


@pytest.mark.asyncio
async def test_cache_serves_last_good_copy_on_failure(monkeypatch):
    monkeypatch.setattr(notices, "app_version", lambda: "2.4.0")
    _patch_transport(
        monkeypatch,
        _feed_handler(
            versions=[{"name": "2.5.0", "tag": "v2.5.0", "prerelease": False}],
        ),
    )
    good = await notices.get_notices()
    assert good["unavailable"] is False
    assert good["latest_version"] == "2.5.0"

    # Force the cache to look stale, then make the hub fail.
    monkeypatch.setattr(notices, "CACHE_SECONDS", 0)
    _patch_transport(monkeypatch, _feed_handler(status=500))
    fallback = await notices.get_notices()
    assert fallback["unavailable"] is False
    assert fallback["latest_version"] == "2.5.0"


# ---------------------------------------------------------------------------
# Happy path + version comparison
# ---------------------------------------------------------------------------

VERSIONS = [
    {
        "name": "2.4.0",
        "tag": "v2.4.0",
        "title": "Maintenance",
        "body": "Fixes",
        "published_at": "2026-01-01T00:00:00Z",
        "prerelease": False,
        "security": False,
    },
    {
        "name": "2.5.0",
        "tag": "v2.5.0",
        "title": "Feature release",
        "body": "New stuff",
        "published_at": "2026-02-01T00:00:00Z",
        "prerelease": False,
        "security": True,
    },
    {
        "name": "2.5.0-beta.1",
        "tag": "v2.5.0-beta.1",
        "title": "Beta",
        "body": "Preview",
        "published_at": "2026-02-02T00:00:00Z",
        "prerelease": True,
        "security": False,
    },
]

ANNOUNCEMENTS = [
    {"id": 2, "title": "Second", "body": "b", "level": "info", "published_at": "2026-02-02T00:00:00Z"},
    {"id": 1, "title": "First", "body": "a", "level": "security", "published_at": "2026-01-01T00:00:00Z"},
]


@pytest.mark.asyncio
async def test_happy_path_parses_feeds_and_computes_update(monkeypatch):
    monkeypatch.setattr(notices, "app_version", lambda: "2.4.0")
    _patch_transport(monkeypatch, _feed_handler(versions=VERSIONS, announcements=ANNOUNCEMENTS))

    result = await notices.get_notices()
    assert result["unavailable"] is False
    assert result["announcements"] == ANNOUNCEMENTS
    # Prerelease 2.5.0-beta.1 sorts below its release 2.5.0.
    assert result["latest_version"] == "2.5.0"
    assert result["current_version"] == "2.4.0"
    assert result["update_available"] is True


@pytest.mark.asyncio
async def test_current_newer_than_latest_reports_no_update(monkeypatch):
    monkeypatch.setattr(notices, "app_version", lambda: "2.6.0")
    _patch_transport(monkeypatch, _feed_handler(versions=VERSIONS))
    result = await notices.get_notices()
    assert result["latest_version"] == "2.5.0"
    assert result["update_available"] is False


@pytest.mark.asyncio
async def test_invalid_current_version_reports_no_update(monkeypatch):
    monkeypatch.setattr(notices, "app_version", lambda: "unknown")
    _patch_transport(monkeypatch, _feed_handler(versions=VERSIONS))
    result = await notices.get_notices()
    assert result["current_version"] == "unknown"
    assert result["update_available"] is False


@pytest.mark.asyncio
async def test_request_urls_include_product_quickly(monkeypatch):
    monkeypatch.setattr(notices, "app_version", lambda: "2.4.0")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path.endswith("/v1/announcements"):
            return httpx.Response(200, json={"announcements": []}, request=request)
        return httpx.Response(200, json={"versions": []}, request=request)

    _patch_transport(monkeypatch, handler)
    await notices.get_notices()

    assert seen, "expected both feeds to be fetched"
    for url in seen:
        assert "product=quickly" in url
    assert any("v1/announcements" in u for u in seen)
    assert any("v1/versions" in u for u in seen)


@pytest.mark.asyncio
async def test_second_call_uses_cache(monkeypatch):
    monkeypatch.setattr(notices, "app_version", lambda: "2.4.0")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/v1/announcements"):
            return httpx.Response(200, json={"announcements": []}, request=request)
        return httpx.Response(200, json={"versions": VERSIONS}, request=request)

    _patch_transport(monkeypatch, handler)
    await notices.get_notices()
    first_count = len(calls)
    assert first_count == 2
    await notices.get_notices()
    assert len(calls) == first_count  # served from cache, no new requests


# ---------------------------------------------------------------------------
# Semver helper (direct)
# ---------------------------------------------------------------------------

def test_version_key_orders_core_numerically():
    assert notices.version_key("2.4.0") > notices.version_key("2.3.9")
    assert notices.version_key("10.0.0") > notices.version_key("9.9.9")
    assert notices.version_key("2.4.0") == notices.version_key("v2.4.0")


def test_version_key_prerelease_sorts_below_release():
    assert notices.version_key("2.4.0") > notices.version_key("2.4.0-beta.1")
    assert notices.version_key("2.4.0-beta.2") > notices.version_key("2.4.0-beta.1")
    assert notices.version_key("2.4.0-rc.1") > notices.version_key("2.4.0-alpha.1")


def test_version_key_rejects_invalid_names():
    assert notices.version_key("unknown") is None
    assert notices.version_key("2.4") is None
    assert notices.version_key("") is None
    assert notices.version_key(None) is None


def test_compare_versions_returns_none_on_invalid():
    assert notices.compare_versions("2.4.0", "2.3.0") == 1
    assert notices.compare_versions("2.3.0", "2.4.0") == -1
    assert notices.compare_versions("2.4.0", "2.4.0") == 0
    assert notices.compare_versions("unknown", "2.4.0") is None
    assert notices.compare_versions("2.4.0", "nope") is None


def test_latest_version_skips_invalid_entries():
    versions = [
        {"name": "not-a-version"},
        {"name": "2.4.0"},
        {"name": "2.6.0"},
        {"tag": "v9.9.9"},  # no name → ignored
    ]
    assert notices._latest_version(versions) == "2.6.0"
    assert notices._latest_version([]) is None
    assert notices._latest_version([{"name": "bad"}]) is None
