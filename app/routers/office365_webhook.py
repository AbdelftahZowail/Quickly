"""Microsoft Graph change notification (webhook) management for Office 365 inboxes.

Endpoints
---------
GET  /api/office365/graph-webhook/subscriptions
    List all active Graph subscriptions with their expiry times.

POST /api/office365/graph-webhook/subscriptions/{inbox_id}
    Create a new subscription (or renew an existing one) for an inbox.

DELETE /api/office365/graph-webhook/subscriptions/{inbox_id}
    Delete the Graph subscription for an inbox.

POST /api/office365/graph-webhook/notifications  (PUBLIC – called by Microsoft)
    Dual-purpose endpoint:
    • Responds to the validation challenge during subscription creation
      (?validationToken=...).
    • Receives change notifications from Microsoft Graph and triggers an
      incremental inbox sync + SSE broadcast.

Security
--------
The management endpoints require Quickly authentication (Bearer / API key).
The notification callback is intentionally public so Microsoft can reach it,
but each notification is validated by comparing the ``clientState`` shared
secret stored in the database (HMAC-safe comparison) to prevent spoofed calls.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import secrets
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import Inbox, Office365Account, Office365GraphSubscription

log = logging.getLogger("quickly.office365_webhook")

router = APIRouter(tags=["office365-webhook"])

GRAPH_SUBSCRIPTIONS_URL = "https://graph.microsoft.com/v1.0/subscriptions"

# Microsoft allows up to 4230 minutes (~70 h) for mail subscriptions.
# Use a slightly smaller window so renewal always has head-room.
SUBSCRIPTION_LIFETIME_MINUTES = 4200

# Renew subscriptions that will expire within this window.
RENEWAL_THRESHOLD_MINUTES = 60 * 24  # 24 hours


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _graph_request(method: str, url: str, access_token: str, body: dict | None = None) -> dict:
    """Execute a synchronous Microsoft Graph API request.

    Designed to be called inside ``asyncio.to_thread`` so the event loop
    is not blocked.  Raises ``HTTPException(502)`` on API errors.
    """
    data = json.dumps(body).encode() if body else None
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read().decode()
            return json.loads(content) if content.strip() else {}
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode(errors="ignore")
        log.error("Graph API %s %s → %s: %s", method, url, exc.code, error_body)
        raise HTTPException(status_code=502, detail=f"Microsoft Graph API error {exc.code}")
    except Exception as exc:
        log.error("Graph API %s %s unexpected error: %s", method, url, exc)
        raise HTTPException(status_code=502, detail="Microsoft Graph API communication error")


async def _fresh_token(db: AsyncSession, inbox_id: int) -> str:
    """Return a fresh access token for an Office 365 inbox."""
    from app.unibox import _ensure_o365_access_token

    result = await db.execute(
        select(Office365Account).where(Office365Account.inbox_id == inbox_id)
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(404, "Office 365 account not connected for this inbox")
    return await _ensure_o365_access_token(db, account)


def _notification_url() -> str:
    """Build the public notification callback URL from the configured base URL."""
    from app.settings_manager import settings
    return settings.base_url.rstrip("/") + "/api/office365/graph-webhook/notifications"


# ---------------------------------------------------------------------------
# Subscription management  (require Quickly auth)
# ---------------------------------------------------------------------------

@router.get("/api/office365/graph-webhook/subscriptions")
async def list_graph_subscriptions(
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    """List all active Microsoft Graph subscriptions with expiry information."""
    result = await db.execute(
        select(Office365GraphSubscription, Inbox)
        .join(Inbox, Office365GraphSubscription.inbox_id == Inbox.id)
        .order_by(Office365GraphSubscription.expiry.asc())
    )
    rows = result.all()
    now = datetime.utcnow()
    return [
        {
            "id": sub.id,
            "inbox_id": sub.inbox_id,
            "inbox_email": inbox.email,
            "subscription_id": sub.subscription_id,
            "resource": sub.resource,
            "change_type": sub.change_type,
            "expiry": sub.expiry.isoformat() + "Z" if sub.expiry else None,
            "minutes_until_expiry": (
                int((sub.expiry - now).total_seconds() / 60) if sub.expiry else None
            ),
        }
        for sub, inbox in rows
    ]


async def ensure_subscription(db: AsyncSession, inbox_id: int) -> dict:
    """Ensure a Graph webhook subscription exists for *inbox_id*.

    This function is safe to call from internal code (e.g. during OAuth
    callback).  It mirrors the behaviour of the public endpoint and returns a
    dictionary containing ``ok``, ``action`` ("created"|"renewed"),
    ``subscription_id`` and ``expiry``.

    Raises ``HTTPException`` on failure.
    """
    inbox_res = await db.execute(
        select(Inbox).where(Inbox.id == inbox_id, Inbox.provider == "office365")
    )
    inbox = inbox_res.scalar_one_or_none()
    if inbox is None:
        raise HTTPException(404, "Office 365 inbox not found")

    access_token = await _fresh_token(db, inbox_id)

    sub_res = await db.execute(
        select(Office365GraphSubscription).where(Office365GraphSubscription.inbox_id == inbox_id)
    )
    existing = sub_res.scalar_one_or_none()

    new_expiry = datetime.utcnow() + timedelta(minutes=SUBSCRIPTION_LIFETIME_MINUTES)
    expiry_str = new_expiry.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    if existing:
        # Attempt to renew via PATCH
        try:
            await asyncio.to_thread(
                _graph_request,
                "PATCH",
                f"{GRAPH_SUBSCRIPTIONS_URL}/{existing.subscription_id}",
                access_token,
                {"expirationDateTime": expiry_str},
            )
            existing.expiry = new_expiry
            existing.updated_at = datetime.utcnow()
            await db.commit()
            log.info(
                "Renewed Graph subscription %s for inbox_id=%s",
                existing.subscription_id,
                inbox_id,
            )
            return {
                "ok": True,
                "action": "renewed",
                "subscription_id": existing.subscription_id,
                "expiry": new_expiry.isoformat() + "Z",
            }
        except HTTPException:
            # Subscription has likely expired; delete the stale row and create fresh.
            log.warning(
                "PATCH renewal failed for subscription %s – creating new subscription",
                existing.subscription_id,
            )
            await db.delete(existing)
            await db.flush()

    # --- Create new subscription ---
    client_state = secrets.token_hex(32)  # 64-char hex string used for notification auth
    payload = {
        "changeType": "created",
        "notificationUrl": _notification_url(),
        "resource": "me/mailFolders/Inbox/messages",
        "expirationDateTime": expiry_str,
        "clientState": client_state,
    }

    result = await asyncio.to_thread(
        _graph_request, "POST", GRAPH_SUBSCRIPTIONS_URL, access_token, payload
    )
    subscription_id = result.get("id", "")
    if not subscription_id:
        raise HTTPException(502, "Microsoft Graph did not return a subscription ID")

    sub = Office365GraphSubscription(
        inbox_id=inbox_id,
        subscription_id=subscription_id,
        client_state=client_state,
        resource="me/mailFolders/Inbox/messages",
        change_type="created",
        expiry=new_expiry,
    )
    db.add(sub)
    await db.commit()
    log.info("Created Graph subscription %s for inbox_id=%s", subscription_id, inbox_id)
    return {
        "ok": True,
        "action": "created",
        "subscription_id": subscription_id,
        "expiry": new_expiry.isoformat() + "Z",
    }


@router.post("/api/office365/graph-webhook/subscriptions/{inbox_id}")
async def create_or_renew_subscription(
    inbox_id: int,
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    """Create or renew a subscription (public-facing wrapper)."""
    return await ensure_subscription(db, inbox_id)

@router.delete("/api/office365/graph-webhook/subscriptions/{inbox_id}")
async def delete_subscription(
    inbox_id: int,
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    """Delete the Microsoft Graph subscription for an inbox."""
    sub_res = await db.execute(
        select(Office365GraphSubscription).where(Office365GraphSubscription.inbox_id == inbox_id)
    )
    sub = sub_res.scalar_one_or_none()
    if sub is None:
        raise HTTPException(404, "No active Graph subscription found for this inbox")

    try:
        access_token = await _fresh_token(db, inbox_id)
        await asyncio.to_thread(
            _graph_request,
            "DELETE",
            f"{GRAPH_SUBSCRIPTIONS_URL}/{sub.subscription_id}",
            access_token,
        )
    except HTTPException:
        log.warning(
            "Could not delete subscription %s from Microsoft (may already be expired) – removing locally",
            sub.subscription_id,
        )

    deleted_id = sub.subscription_id
    await db.delete(sub)
    await db.commit()
    return {"ok": True, "deleted_subscription_id": deleted_id}


# ---------------------------------------------------------------------------
# Notification callback  (PUBLIC – called by Microsoft Graph)
# ---------------------------------------------------------------------------

@router.post("/api/office365/graph-webhook/notifications")
async def handle_graph_notifications(
    request: Request,
    validationToken: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Receive Microsoft Graph change notification callbacks.

    **Validation challenge** (subscription creation handshake):
    Microsoft sends a POST with ``?validationToken=<token>`` and expects the
    token echoed back as ``text/plain`` with HTTP 200 within 10 seconds.

    **Change notification** (new mail event):
    Microsoft sends a JSON body with one or more notification objects.  Each
    notification's ``clientState`` is validated against the stored secret
    (constant-time comparison) before the inbox sync is triggered.

    Microsoft requires a 202 response within 3 seconds; heavy work is
    handed off to background tasks.
    """
    # --- Validation handshake ---
    if validationToken:
        log.info("Graph webhook validation challenge received")
        return PlainTextResponse(content=validationToken, status_code=200)

    # --- Change notification ---
    try:
        body = await request.json()
    except Exception:
        log.warning("Graph notification: failed to parse JSON body")
        raise HTTPException(400, "Invalid JSON body")

    items = body.get("value", [])
    if not isinstance(items, list):
        # Non-standard body; acknowledge and move on to avoid Microsoft retries.
        return JSONResponse(content={"ok": True}, status_code=202)

    inbox_ids_to_sync: set[int] = set()

    for item in items:
        subscription_id = str(item.get("subscriptionId", ""))
        incoming_client_state = str(item.get("clientState", ""))

        # Look up the subscription
        sub_res = await db.execute(
            select(Office365GraphSubscription).where(
                Office365GraphSubscription.subscription_id == subscription_id
            )
        )
        sub = sub_res.scalar_one_or_none()

        if sub is None:
            log.warning("Graph notification for unknown subscriptionId=%s – ignoring", subscription_id)
            continue

        # Constant-time comparison protects against timing attacks on the secret.
        if not hmac.compare_digest(sub.client_state, incoming_client_state):
            log.warning(
                "Graph notification clientState mismatch for subscriptionId=%s – ignoring",
                subscription_id,
            )
            continue

        inbox_ids_to_sync.add(sub.inbox_id)

    # Trigger an incremental sync and broadcast an SSE event for each affected inbox.
    if inbox_ids_to_sync:
        from app.unibox import queue_sync_for_inbox, unibox_events  # local import avoids circular dep

        for inbox_id in inbox_ids_to_sync:
            await queue_sync_for_inbox(inbox_id, reason="graph-webhook")
            await unibox_events.publish(
                {
                    "type": "unibox.sync.triggered",
                    "reason": "graph-webhook",
                    "inbox_id": inbox_id,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                }
            )
            log.info("Graph notification triggered sync for inbox_id=%s", inbox_id)

    # Always respond 202 so Microsoft does not retry.
    return JSONResponse(content={"ok": True}, status_code=202)


# ---------------------------------------------------------------------------
# Scheduled renewal job
# ---------------------------------------------------------------------------

async def renew_expiring_subscriptions() -> None:
    """Background job: renew Microsoft Graph subscriptions approaching expiry.

    Should be called on a regular schedule (e.g. every 6 hours) to prevent
    subscriptions from lapsing and falling back to polling-only mode.
    """
    from app.database import AsyncSessionLocal
    from app.unibox import _ensure_o365_access_token

    threshold = datetime.utcnow() + timedelta(minutes=RENEWAL_THRESHOLD_MINUTES)
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Office365GraphSubscription).where(Office365GraphSubscription.expiry <= threshold)
        )
        subs = result.scalars().all()

        for sub in subs:
            try:
                account_res = await db.execute(
                    select(Office365Account).where(Office365Account.inbox_id == sub.inbox_id)
                )
                account = account_res.scalar_one_or_none()
                if account is None:
                    log.warning("Renewal: no Office365Account for inbox_id=%s", sub.inbox_id)
                    continue

                access_token = await _ensure_o365_access_token(db, account)
                new_expiry = datetime.utcnow() + timedelta(minutes=SUBSCRIPTION_LIFETIME_MINUTES)
                expiry_str = new_expiry.strftime("%Y-%m-%dT%H:%M:%S.000Z")

                await asyncio.to_thread(
                    _graph_request,
                    "PATCH",
                    f"{GRAPH_SUBSCRIPTIONS_URL}/{sub.subscription_id}",
                    access_token,
                    {"expirationDateTime": expiry_str},
                )
                sub.expiry = new_expiry
                sub.updated_at = datetime.utcnow()
                await db.commit()
                log.info(
                    "Auto-renewed Graph subscription %s for inbox_id=%s (new expiry: %s)",
                    sub.subscription_id,
                    sub.inbox_id,
                    expiry_str,
                )
            except Exception:
                log.exception(
                    "Failed to auto-renew Graph subscription %s for inbox_id=%s",
                    sub.subscription_id,
                    sub.inbox_id,
                )
