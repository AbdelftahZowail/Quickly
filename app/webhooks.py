"""Helpers for firing outbound webhooks when certain events occur.

Currently only a single "email events" webhook is supported; the URL and
optional bearer token are stored in the database and accessed via the
app_settings helpers.  The webhook is invoked asynchronously with a simple
JSON payload of the form:

    {"event": "daily_limit"|"rate_limit"|"token_expired"|..., "data": {...}}

The implementation is deliberately small and tolerant of failures: a network
error or bad URL will be logged but otherwise ignored so that the primary
workflow (sending emails or syncing) is not interrupted.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.app_settings import (
    EMAIL_EVENTS_WEBHOOK_URL_KEY,
    EMAIL_EVENTS_WEBHOOK_TOKEN_KEY,
    LEAD_REPLY_WEBHOOK_URL_KEY,
    LEAD_REPLY_WEBHOOK_TOKEN_KEY,
    get_setting,
)

log = logging.getLogger("quickly.webhooks")


def _build_headers(token: str | None) -> dict[str, str]:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def maybe_fire_email_event(
    db: AsyncSession, event_type: str, data: dict[str, Any]
) -> None:
    """Send *data* to the configured email‑events webhook, if present.

    The event payload always includes an ``event`` field naming the type of
    occurrence and a ``data`` object with event‑specific details.  If no URL
    has been configured the function returns immediately.
    """
    url = await get_setting(db, EMAIL_EVENTS_WEBHOOK_URL_KEY)
    if not url:
        return

    token = await get_setting(db, EMAIL_EVENTS_WEBHOOK_TOKEN_KEY)
    headers = _build_headers(token)
    payload = {"event": event_type, "data": data}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(url, json=payload, headers=headers)
    except Exception as exc:
        log.warning("email event webhook POST failed %s: %s", url, exc)


async def fire_lead_reply_webhook(
    db: AsyncSession, data: dict[str, Any]
) -> None:
    """Fire the dedicated lead-reply webhook, if configured.

    Falls back to the general email-events webhook when no dedicated URL is
    set so integrations that only configure one URL still receive the event.
    """
    # Try dedicated lead-reply webhook first.
    url = await get_setting(db, LEAD_REPLY_WEBHOOK_URL_KEY)
    token: str | None = None
    if url:
        token = await get_setting(db, LEAD_REPLY_WEBHOOK_TOKEN_KEY)
    else:
        # Fall back to the general email-events webhook.
        url = await get_setting(db, EMAIL_EVENTS_WEBHOOK_URL_KEY)
        if url:
            token = await get_setting(db, EMAIL_EVENTS_WEBHOOK_TOKEN_KEY)

    if not url:
        return

    headers = _build_headers(token)
    payload = {"event": "lead.reply", "data": data}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(url, json=payload, headers=headers)
    except Exception as exc:
        log.warning("lead reply webhook POST failed %s: %s", url, exc)
