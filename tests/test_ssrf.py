"""SSRF hardening tests: private-IP detection, webhook URL validation, dispatch guard.

The IP-address cases are pure functions.  Router cases call the endpoint
functions directly with the ``session`` fixture (same style as
``test_webhook_crud.py``); the dispatch cases monkeypatch ``httpx`` so no real
network request is made.
"""
from __future__ import annotations

import socket
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models import Webhook
from app.schemas import WebhookCreate, WebhookUpdate
from app.security import is_private_ip, resolve_and_check, validate_webhook_url
from app.webhooks import _post_webhook
from app.routers.settings import create_webhook, update_webhook


# ---------------------------------------------------------------------------
# is_private_ip — blocked target families
# ---------------------------------------------------------------------------

PRIVATE_IPS = [
    "127.0.0.1",
    "10.0.0.1",
    "172.16.5.4",
    "192.168.1.1",
    "169.254.169.254",       # cloud metadata
    "0.0.0.0",
    "100.64.0.1",            # CGNAT
    "::1",
    "fc00::1",               # unique-local
    "fe80::1",               # link-local
    "::ffff:127.0.0.1",      # IPv4-mapped loopback
    "::ffff:10.0.0.1",       # IPv4-mapped private
    "64:ff9b::7f00:1",       # NAT64 → 127.0.0.1
    "2002:7f00:1::1",        # 6to4 → 127.0.0.1
    "::127.0.0.1",           # deprecated IPv4-compatible loopback
]


@pytest.mark.parametrize("ip", PRIVATE_IPS)
def test_is_private_ip_rejects_internal(ip):
    assert is_private_ip(ip) is True


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700:4700::1111"])
def test_is_private_ip_accepts_public(ip):
    assert is_private_ip(ip) is False


def test_is_private_ip_blocks_unparseable():
    assert is_private_ip("not-an-ip") is True
    assert is_private_ip("") is True


# ---------------------------------------------------------------------------
# validate_webhook_url — accepts public, rejects internal
# ---------------------------------------------------------------------------


def test_validate_webhook_url_accepts_public_https():
    assert validate_webhook_url("https://example.com/hook") is None


def test_validate_webhook_url_accepts_public_ip_literal():
    assert validate_webhook_url("https://8.8.8.8/hook") is None


@pytest.mark.parametrize(
    "url",
    [
        "https://10.0.0.1/hook",
        "https://192.168.1.5/hook",
        "https://169.254.169.254/latest/meta-data",
        "https://[::1]/hook",
        "https://[::ffff:10.0.0.1]/hook",
        "https://[64:ff9b::7f00:1]/hook",
        "https://[2002:7f00:1::1]/hook",
        "http://127.0.0.1/hook",
    ],
)
def test_validate_webhook_url_rejects_private(url):
    assert validate_webhook_url(url) is not None


def test_validate_webhook_url_requires_http_or_https():
    assert validate_webhook_url("ftp://example.com/hook") is not None
    assert validate_webhook_url("file:///etc/passwd") is not None


def test_validate_webhook_url_blocks_metadata_hostname():
    assert validate_webhook_url("https://metadata.google.internal/x") is not None


def test_localhost_http_exception_requires_explicit_opt_in(monkeypatch):
    """Plain HTTP to localhost is preserved for development, but opt-in only."""
    monkeypatch.delenv("QUICKLY_WEBHOOK_ALLOW_LOCALHOST", raising=False)
    assert validate_webhook_url("http://localhost:9000/hook") is not None

    monkeypatch.setenv("QUICKLY_WEBHOOK_ALLOW_LOCALHOST", "true")
    assert validate_webhook_url("http://localhost:9000/hook") is None
    assert validate_webhook_url("http://127.0.0.1:9000/hook") is None


def test_validate_webhook_url_allows_public_http_only_for_localhost(monkeypatch):
    monkeypatch.setenv("QUICKLY_WEBHOOK_ALLOW_LOCALHOST", "true")
    assert validate_webhook_url("http://example.com/hook") == "Production webhooks must use https"


def test_resolve_and_check_strict_dns(monkeypatch):
    """strict_dns=True rejects a lookup failure; save-time mode tolerates it."""

    def fake_getaddrinfo(*args, **kwargs):
        raise socket.gaierror("no such host")

    monkeypatch.setattr("app.security.socket.getaddrinfo", fake_getaddrinfo)
    assert resolve_and_check("unresolvable.example")[0] is False
    assert resolve_and_check("unresolvable.example", strict_dns=False)[0] is True


# ---------------------------------------------------------------------------
# Webhook CRUD — create / update reject internal targets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://10.0.0.1/hook",
        "https://[::ffff:10.0.0.1]/hook",
        "https://[64:ff9b::7f00:1]/hook",
        "http://127.0.0.1/hook",
        "ftp://example.com/hook",
    ],
)
async def test_create_webhook_rejects_private_url(session, url):
    with pytest.raises(HTTPException) as exc_info:
        await create_webhook(payload=WebhookCreate(url=url, events=[]), db=session)

    assert exc_info.value.status_code == 400
    assert "webhook url" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_update_webhook_rejects_private_url(session):
    wh = Webhook(url="https://example.com/hook", events=[], active=True)
    session.add(wh)
    await session.flush()

    with pytest.raises(HTTPException) as exc_info:
        await update_webhook(
            webhook_id=wh.id,
            payload=WebhookUpdate(url="https://192.168.1.10/hook"),
            db=session,
        )

    assert exc_info.value.status_code == 400
    assert wh.url == "https://example.com/hook"


@pytest.mark.asyncio
async def test_update_webhook_accepts_public_url(session):
    wh = Webhook(url="https://old.example.com/hook", events=[], active=True)
    session.add(wh)
    await session.flush()

    updated = await update_webhook(
        webhook_id=wh.id,
        payload=WebhookUpdate(url="https://example.com/new"),
        db=session,
    )

    assert updated.url == "https://example.com/new"


# ---------------------------------------------------------------------------
# Dispatch-time guard
# ---------------------------------------------------------------------------


def _mock_client():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://10.0.0.5/hook",
        "https://[::ffff:127.0.0.1]/hook",
        "http://169.254.169.254/latest/meta-data",
    ],
)
async def test_dispatch_blocks_private_target(url):
    wh = Webhook(id=1, url=url, secret="", events=[], active=True)

    with patch("app.webhooks.httpx.AsyncClient") as mock_cls:
        result = await _post_webhook(wh, "email.sent", {})

    assert result is False
    mock_cls.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_blocks_dns_rebind_to_private(monkeypatch):
    """A hostname that re-resolves to a private IP at dispatch time is blocked."""
    wh = Webhook(id=1, url="https://rebind.example/hook", secret="", events=[], active=True)

    def fake_getaddrinfo(host, *args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0)),
        ]

    monkeypatch.setattr("app.security.socket.getaddrinfo", fake_getaddrinfo)

    with patch("app.webhooks.httpx.AsyncClient") as mock_cls:
        result = await _post_webhook(wh, "email.sent", {})

    assert result is False
    mock_cls.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_does_not_follow_redirects():
    wh = Webhook(id=1, url="https://example.com/hook", secret="", events=[], active=True)
    mock_client = _mock_client()

    with patch("app.webhooks.httpx.AsyncClient", return_value=mock_client) as mock_cls:
        result = await _post_webhook(wh, "email.sent", {"x": 1})

    assert result is True
    assert mock_cls.call_args.kwargs["follow_redirects"] is False
