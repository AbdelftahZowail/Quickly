"""SSRF follow-up tests: user-controlled outbound URLs beyond the webhook CRUD.

Covers the second pass of the SSRF hardening:

* ``app.backup_pg.post_backup_webhook`` (scheduled backup delivery)
* ``app.routers.inbox`` Beacon connect / disconnect calls
* ``app.routers.system_health`` tracking-domain and Beacon health probes
* ``app.email_verification.CustomHttpProvider`` (user URL template)

Every path is exercised twice: a private (or IPv4-mapped) target must be
rejected without any HTTP client being created, and a public target must still
go through with ``follow_redirects=False``.  The HTTP layer is always mocked.
"""
from __future__ import annotations

import socket
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from app.backup_pg import BackupError, post_backup_webhook
from app.email_verification import (
    UNKNOWN,
    VALID,
    CustomHttpProvider,
    MailtesterNinjaProvider,
)
from app.routers.inbox import (
    _apply_beacon_connection,
    beacon_connect,
    beacon_disconnect,
)
from app.routers.system_health import (
    _fetch_beacon_health_payload,
    _probe_tracking_domain,
)
from app.schemas import BeaconConnectRequest
from tests.conftest import make_inbox


# ---------------------------------------------------------------------------
# Helpers — mock the HTTP layer (no real network)
# ---------------------------------------------------------------------------

PRIVATE_URLS = [
    "https://10.0.0.5/hook",
    "https://127.0.0.1/hook",
    "https://[::1]/hook",
    "https://[::ffff:10.0.0.1]/hook",
    "https://[::ffff:127.0.0.1]/hook",
    "https://169.254.169.254/latest/meta-data",
]

PUBLIC_URL = "https://8.8.8.8/hook"


def _mock_async_client(json_data=None, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = ""
    resp.json = MagicMock(return_value=json_data if json_data is not None else {})
    client = AsyncMock()
    client.post = AsyncMock(return_value=resp)
    client.get = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


def _mock_sync_client(json_data=None, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_data if json_data is not None else {})
    client = MagicMock()
    client.get = MagicMock(return_value=resp)
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    return client


# ---------------------------------------------------------------------------
# backup_pg.post_backup_webhook
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("url", PRIVATE_URLS)
async def test_backup_webhook_rejects_private_target(url):
    with patch("app.backup_pg.httpx.AsyncClient") as mock_cls:
        with pytest.raises(BackupError) as exc_info:
            await post_backup_webhook(url, b"dump", "backup.qbk")

    assert "rejected" in str(exc_info.value).lower()
    mock_cls.assert_not_called()


@pytest.mark.asyncio
async def test_backup_webhook_rejects_rebound_hostname(monkeypatch):
    """A hostname resolving to a private IP at dispatch time is refused."""

    def fake_getaddrinfo(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]

    monkeypatch.setattr("app.security.socket.getaddrinfo", fake_getaddrinfo)

    with patch("app.backup_pg.httpx.AsyncClient") as mock_cls:
        with pytest.raises(BackupError):
            await post_backup_webhook("https://rebind.example/hook", b"dump", "backup.qbk")

    mock_cls.assert_not_called()


@pytest.mark.asyncio
async def test_backup_webhook_allows_public_target_without_redirects():
    mock_client = _mock_async_client()

    with patch(
        "app.backup_pg.httpx.AsyncClient", return_value=mock_client
    ) as mock_cls:
        await post_backup_webhook(PUBLIC_URL, b"dump", "backup.qbk")

    mock_client.post.assert_awaited_once()
    assert mock_cls.call_args.kwargs["follow_redirects"] is False


# ---------------------------------------------------------------------------
# routers/inbox.py — Beacon connect / disconnect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base",
    [
        "https://10.0.0.5",
        "https://127.0.0.1",
        "https://[::1]",
        "https://[::ffff:10.0.0.1]",
        "http://169.254.169.254",
    ],
)
async def test_beacon_connect_rejects_private_base(session, base):
    inbox = await make_inbox(session)

    with patch("app.routers.inbox.httpx.AsyncClient") as mock_cls:
        with pytest.raises(HTTPException) as exc_info:
            await _apply_beacon_connection(session, inbox, base, "setup-token")

    assert exc_info.value.status_code == 422
    assert "not allowed" in exc_info.value.detail.lower()
    mock_cls.assert_not_called()
    assert inbox.beacon_connected is False
    assert inbox.beacon_base_url is None


@pytest.mark.asyncio
async def test_beacon_connect_endpoint_rejects_private_setup_url(session):
    inbox = await make_inbox(session)

    with patch("app.routers.inbox.httpx.AsyncClient") as mock_cls:
        with pytest.raises(HTTPException) as exc_info:
            await beacon_connect(
                inbox.id,
                BeaconConnectRequest(setup_url="https://10.1.2.3/setup?token=abc"),
                db=session,
            )

    assert exc_info.value.status_code == 422
    mock_cls.assert_not_called()


@pytest.mark.asyncio
async def test_beacon_connect_allows_public_base_without_redirects(session):
    inbox = await make_inbox(session)
    mock_client = _mock_async_client(json_data={"ok": True})

    with patch(
        "app.routers.inbox.httpx.AsyncClient", return_value=mock_client
    ) as mock_cls:
        await _apply_beacon_connection(session, inbox, "https://8.8.8.8", "setup-token")

    assert mock_cls.call_args.kwargs["follow_redirects"] is False
    assert mock_client.post.await_args.args[0] == "https://8.8.8.8/api/v1/connect"
    assert inbox.beacon_connected is True
    assert inbox.beacon_base_url == "https://8.8.8.8"


@pytest.mark.asyncio
async def test_beacon_disconnect_skips_private_target(session):
    inbox = await make_inbox(session)
    inbox.beacon_connected = True
    inbox.beacon_base_url = "https://10.0.0.5"
    inbox.beacon_setup_token = "setup-token"
    await session.flush()

    with patch("app.routers.inbox.httpx.AsyncClient") as mock_cls:
        await beacon_disconnect(inbox.id, db=session)

    mock_cls.assert_not_called()
    assert inbox.beacon_connected is False
    assert inbox.beacon_base_url is None


@pytest.mark.asyncio
async def test_beacon_disconnect_allows_public_target_without_redirects(session):
    inbox = await make_inbox(session)
    inbox.beacon_connected = True
    inbox.beacon_base_url = "https://8.8.8.8"
    inbox.beacon_setup_token = "setup-token"
    await session.flush()

    mock_client = _mock_async_client()
    with patch(
        "app.routers.inbox.httpx.AsyncClient", return_value=mock_client
    ) as mock_cls:
        await beacon_disconnect(inbox.id, db=session)

    mock_client.post.assert_awaited_once()
    assert mock_client.post.await_args.args[0] == "https://8.8.8.8/api/v1/disconnect"
    assert mock_cls.call_args.kwargs["follow_redirects"] is False
    assert inbox.beacon_connected is False


# ---------------------------------------------------------------------------
# routers/system_health.py — tracking-domain and Beacon probes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("domain", ["10.0.0.5", "127.0.0.1", "169.254.169.254"])
def test_tracking_domain_probe_rejects_private(domain):
    with patch.object(httpx, "Client") as mock_cls:
        result = _probe_tracking_domain(domain)

    assert result == "error"
    mock_cls.assert_not_called()


def test_tracking_domain_probe_allows_public_without_redirects(monkeypatch):
    mock_client = _mock_sync_client(json_data={"ok": True})
    mock_cls = MagicMock(return_value=mock_client)
    monkeypatch.setattr(httpx, "Client", mock_cls)

    assert _probe_tracking_domain("8.8.8.8") == "ok"
    assert mock_cls.call_args.kwargs["follow_redirects"] is False


@pytest.mark.parametrize(
    "base",
    [
        "https://10.0.0.5",
        "https://[::ffff:10.0.0.1]",
        "http://169.254.169.254",
    ],
)
def test_beacon_health_probe_rejects_private(base):
    with patch.object(httpx, "Client") as mock_cls:
        result = _fetch_beacon_health_payload(base, "setup-token")

    assert result == ("error", None)
    mock_cls.assert_not_called()


def test_beacon_health_probe_allows_public_without_redirects(monkeypatch):
    payload = {"ok": True, "connected": True, "inbox_ids": [1]}
    mock_client = _mock_sync_client(json_data=payload)
    mock_cls = MagicMock(return_value=mock_client)
    monkeypatch.setattr(httpx, "Client", mock_cls)

    status, data = _fetch_beacon_health_payload("https://8.8.8.8", "setup-token")

    assert (status, data) == ("ok", payload)
    assert mock_cls.call_args.kwargs["follow_redirects"] is False


# ---------------------------------------------------------------------------
# email_verification.CustomHttpProvider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "template",
    [
        "https://10.0.0.5/verify?email={email}",
        "https://[::ffff:127.0.0.1]/verify?email={email}",
        "http://169.254.169.254/verify?email={email}",
    ],
)
async def test_custom_email_provider_rejects_private_target(template):
    provider = CustomHttpProvider(template, "status", ["valid"], ["invalid"])

    with patch("app.email_verification.httpx.AsyncClient") as mock_cls:
        result = await provider.verify("person@example.com", "api-key")

    mock_cls.assert_not_called()
    assert result.status == UNKNOWN
    assert "not allowed" in result.message.lower()


@pytest.mark.asyncio
async def test_custom_email_provider_allows_public_without_redirects():
    provider = CustomHttpProvider(
        "https://8.8.8.8/verify?email={email}",
        "status",
        ["valid"],
        ["invalid"],
    )
    mock_client = _mock_async_client(json_data={"status": "valid"})

    with patch(
        "app.email_verification.httpx.AsyncClient", return_value=mock_client
    ) as mock_cls:
        result = await provider.verify("person@example.com", "api-key")

    assert result.status == VALID
    assert mock_cls.call_args.kwargs["follow_redirects"] is False


@pytest.mark.asyncio
async def test_vendor_email_provider_still_works():
    """The built-in vendor provider keeps its constant URL and behaviour."""
    provider = MailtesterNinjaProvider()
    mock_client = _mock_async_client(json_data={"code": "ok", "message": "Accepted"})

    with patch(
        "app.email_verification.httpx.AsyncClient", return_value=mock_client
    ):
        result = await provider.verify("person@example.com", "api-key")

    assert result.status == VALID
    called_url = mock_client.get.await_args.args[0]
    assert called_url.startswith(provider.BASE_URL)
