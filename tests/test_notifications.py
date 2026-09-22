"""Tests for app.notifications — event labelling and inbox resolution.

Covers issue #3: the ``token_expired`` notification used to claim "OAuth token
expired" for SMTP auth failures and to show "an inbox" when callers only passed
``inbox_id``.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import Inbox, Notification, User
from app.notifications import build_notification, dispatch_notification


# ---------------------------------------------------------------------------
# build_notification — event labelling
# ---------------------------------------------------------------------------


def test_token_expired_labels_smtp_auth_failure():
    info = build_notification(
        "token_expired",
        {
            "inbox_id": 1,
            "inbox_email": "relay@example.com",
            "provider": "smtp",
            "error_type": "auth_failed",
            "error": "535 Authentication failed",
        },
    )
    assert "SMTP" in info["title"]
    assert "relay@example.com" in info["title"]
    assert "OAuth" not in info["title"]


def test_token_expired_labels_oauth_refresh_failure():
    info = build_notification(
        "token_expired",
        {
            "inbox_id": 1,
            "inbox_email": "gmail@example.com",
            "provider": "gmail",
            "error_type": "oauth_refresh_failed",
        },
    )
    assert info["title"].startswith("OAuth token expired")
    assert "gmail@example.com" in info["title"]


def test_token_expired_labels_imap_auth_failure():
    info = build_notification(
        "token_expired",
        {
            "inbox_email": "relay@example.com",
            "provider": "smtp",
            "error_type": "imap_auth_failed",
        },
    )
    assert "IMAP" in info["title"]
    assert "relay@example.com" in info["title"]


def test_token_expired_labels_imap_sync_failure():
    info = build_notification(
        "token_expired",
        {
            "inbox_email": "relay@example.com",
            "provider": "smtp",
            "error_type": "imap_sync_failed",
        },
    )
    assert "sync failed" in info["title"].lower()
    assert "OAuth" not in info["title"]


# ---------------------------------------------------------------------------
# dispatch_notification — inbox email resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_resolves_inbox_email_from_inbox_id(session):
    """Callers that only pass inbox_id must not produce "an inbox"."""
    inbox = Inbox(email="resolved@example.com", provider="smtp")
    session.add(inbox)
    user = User(username="owner", email="owner@example.com", password_hash="x", is_active=True)
    session.add(user)
    await session.flush()

    await dispatch_notification(
        session,
        "token_expired",
        {"inbox_id": inbox.id, "provider": "smtp", "error_type": "auth_failed"},
    )
    await session.flush()

    res = await session.execute(select(Notification).where(Notification.user_id == user.id))
    notif = res.scalar_one()
    assert "resolved@example.com" in notif.title
    assert "an inbox" not in notif.title
    # The enriched payload is persisted so the UI can show details too.
    assert notif.data_json.get("inbox_email") == "resolved@example.com"
