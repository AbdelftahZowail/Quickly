"""Helpers for firing outbound webhooks when events occur.

The webhook system supports multiple webhook endpoints, each subscribing
to a configurable set of event types.  Webhooks are stored in the ``webhook``
table and managed via CRUD endpoints in ``app.routers.settings``.

Payload format:

    {"event": "<event_type>", "data": {...}, "timestamp": "..."}

The implementation is deliberately tolerant of failures: a network error or
bad URL will be logged but otherwise ignored so that the primary workflow
(sending emails or syncing) is not interrupted.  Each matching webhook is
called independently.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Webhook
from app import time as time_provider

log = logging.getLogger("quickly.webhooks")


def _build_headers(secret: str | None) -> dict[str, str]:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
    return headers




def _sanitize_for_webhook(value: Any) -> Any:
    """Recursively strip angle-bracket characters from strings.

    Webhook payloads are passed to external endpoints and we want to make
    sure they never contain ``"<"`` or ``">"`` characters, so that downstream
    systems cannot inadvertently interpret them as markup.  The function
    handles nested dictionaries and lists as well as plain strings; other
    values are returned unchanged.
    """
    if isinstance(value, str):
        # remove both opening and closing angle brackets
        return value.replace("<", "").replace(">", "")
    elif isinstance(value, dict):
        return {k: _sanitize_for_webhook(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_sanitize_for_webhook(v) for v in value]
    else:
        return value


async def _post_webhook(wh: Webhook, event_type: str, data: dict[str, Any]) -> bool:
    """POST a single payload to one webhook.  Returns ``True`` on success."""
    # sanitize the inputs before generating JSON so that outgoing payloads
    # have no forbidden characters.
    event_type = _sanitize_for_webhook(event_type)
    data = _sanitize_for_webhook(data)

    timestamp = time_provider.utcnow().isoformat() + "Z"
    payload = {"event": event_type, "data": data, "timestamp": timestamp}
    headers = _build_headers(wh.secret)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(wh.url, json=payload, headers=headers)
            if resp.status_code >= 400:
                log.warning(
                    "webhook id=%s returned HTTP %s for event=%s",
                    wh.id, resp.status_code, event_type,
                )
                return False
            return True
    except Exception as exc:
        log.warning(
            "webhook id=%s POST failed for event=%s: %s",
            wh.id, event_type, exc,
        )
        return False


async def fire_webhook_event(
    db: AsyncSession, event_type: str, data: dict[str, Any]
) -> None:
    """Fire *event_type* with *data* to all active webhooks subscribed to it.

    Each webhook that has *event_type* in its ``events`` list (or has an
    empty events list, meaning "all events") will receive a POST request.
    Failures are logged but never raised.

    Also sends email notifications to users who have opted in.
    """
    result = await db.execute(
        select(Webhook).where(Webhook.active == True)  # noqa: E712
    )
    webhooks = result.scalars().all()

    if webhooks:
        timestamp = time_provider.utcnow().isoformat() + "Z"
        payload = {"event": event_type, "data": data, "timestamp": timestamp}

        for wh in webhooks:
            # If the webhook has specific events configured, check if this event
            # is in the list.  An empty list means "subscribe to everything".
            if wh.events and event_type not in wh.events:
                continue

            await _post_webhook(wh, event_type, data)

    # Email notifications (separate system, failures never propagate)
    try:
        from app.notifications import fire_email_notification
        await fire_email_notification(db, event_type, data)
    except Exception:
        log.debug("Email notification dispatch failed for event=%s", event_type, exc_info=True)


# ── Convenience aliases kept for backward compatibility with callers ──────

async def maybe_fire_email_event(
    db: AsyncSession, event_type: str, data: dict[str, Any]
) -> None:
    """Backward-compatible alias — routes through the new webhook system."""
    await fire_webhook_event(db, event_type, data)


async def fire_lead_reply_webhook(
    db: AsyncSession, data: dict[str, Any]
) -> None:
    """Backward-compatible alias — routes through the new webhook system."""
    await fire_webhook_event(db, "lead.replied", data)
