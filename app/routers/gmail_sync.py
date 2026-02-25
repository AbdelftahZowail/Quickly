"""Gmail sync routes: push webhook, manual sync, status."""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.app_settings import get_gmail_sync_config
from app.database import AsyncSessionLocal, get_db
from app.gmail_sync import (
    parse_gmail_push_payload,
    renew_gmail_watch_for_all,
    sync_all_gmail_inboxes,
    sync_gmail_inbox_by_email,
)
from app.models import GmailSyncState, Inbox

log = logging.getLogger("campaign_engine.gmail_sync_router")

router = APIRouter(tags=["gmail-sync"])


async def _run_single_sync_task(email: str, history_id: str) -> None:
    async with AsyncSessionLocal() as session:
        try:
            result = await sync_gmail_inbox_by_email(session, email, history_id)
            await session.commit()
            log.info(
                "gmail_push_sync: email=%s history_id=%s replies_added=%s ok=%s",
                email,
                history_id,
                result.get("replies_added"),
                result.get("ok"),
            )
        except Exception as exc:
            await session.rollback()
            log.error("gmail_push_sync failed for %s: %s", email, exc)


@router.post("/api/gmail/push")
async def gmail_push_webhook(
    request: Request,
    token: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    payload = await request.json()
    email_address, history_id = parse_gmail_push_payload(payload)
    if not email_address or not history_id:
        # Accept and acknowledge to avoid retries for malformed test payloads.
        return {"ok": True, "accepted": False, "reason": "invalid_payload"}

    sync_config = await get_gmail_sync_config(db)
    expected_token = str(sync_config.get("webhook_token") or "").strip()
    header_token = (request.headers.get("X-Gmail-Webhook-Token") or "").strip()
    provided_token = (token or header_token or "").strip()

    # token is optional; if one is configured we log when it's missing but
    # still accept the request.  this prevents failed deliveries when the
    # push subscription was created without embedding the token (common during
    # initial setup).  an explicit non-matching token is still rejected.
    if expected_token:
        if not provided_token:
            log.warning("gmail_push_webhook: request without token while one is configured")
        elif provided_token != expected_token:
            raise HTTPException(403, "Invalid webhook token")

    asyncio.create_task(_run_single_sync_task(email_address, history_id))
    return {"ok": True, "accepted": True}


@router.post("/api/gmail/sync-now")
async def gmail_sync_now(
    inbox_email: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    if inbox_email:
        result = await sync_gmail_inbox_by_email(db, inbox_email)
        return result
    return await sync_all_gmail_inboxes(db)


@router.post("/api/gmail/watch/renew")
async def gmail_watch_renew_now(db: AsyncSession = Depends(get_db)):
    return await renew_gmail_watch_for_all(db)


# clients (including webhook test tools) can poll this to check sync status and watch expiration for each inbox
@router.get("/api/gmail/sync-status")
async def gmail_sync_status(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(GmailSyncState, Inbox)
        .join(Inbox, GmailSyncState.inbox_id == Inbox.id)
        .order_by(Inbox.id)
    )
    rows = result.all()
    return {
        "items": [
            {
                "inbox_id": inbox.id,
                "inbox_email": inbox.email,
                "last_history_id": state.last_history_id or "",
                "watch_expiration": state.watch_expiration.isoformat() if state.watch_expiration else None,
                "last_sync_at": state.last_sync_at.isoformat() if state.last_sync_at else None,
            }
            for state, inbox in rows
        ]
    }
