"""Unibox API routes."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import GmailAccount, GmailMessage, GmailSyncState, Inbox, Office365Account, Office365Message, SmtpAccount, SmtpMessage
from app.app_settings import get_office365_oauth_credentials
from app.sender import SendResult, send_email
from app.time import utcnow
from app.unibox import (
    BACKFILL_WINDOW_DAYS,
    decode_push_message_data,
    get_notification_count,
    get_unibox_sync_status,
    get_thread_messages,
    hydrate_thread_on_demand,
    list_unibox_conversations,
    mark_thread_read,
    queue_backfill_for_all_inboxes,
    queue_backfill_for_inbox,
    queue_sync_for_all_inboxes,
    queue_sync_for_inbox,
    unibox_events,
    upsert_sent_message,
    upsert_sent_o365_message,
    upsert_sent_smtp_message,
)

log = logging.getLogger("quickly.unibox.router")

router = APIRouter(prefix="/api/unibox", tags=["unibox"])


class UniboxSendRequest(BaseModel):
    inbox_id: int = Field(..., gt=0)
    to_email: str = Field(min_length=3, max_length=320)
    subject: str = Field(default="", max_length=998)
    body: str = Field(default="")
    thread_id: str | None = None
    in_reply_to: str | None = None
    references: str | None = None
    is_html: bool = False


class UniboxSyncRequest(BaseModel):
    inbox_id: int | None = Field(default=None, gt=0)


class UniboxLoadMoreRequest(BaseModel):
    inbox_id: int | None = Field(default=None, gt=0)
    window_days: int = Field(default=BACKFILL_WINDOW_DAYS, ge=1, le=60)


def _extract_message_id_from_headers(headers_json: str) -> str | None:
    try:
        headers = json.loads(headers_json or "[]")
    except json.JSONDecodeError:
        return None
    if not isinstance(headers, list):
        return None
    for row in headers:
        if not isinstance(row, dict):
            continue
        if str(row.get("name", "")).lower() == "message-id":
            value = str(row.get("value", "")).strip()
            return value or None
    return None


@router.get("")
async def get_unibox(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    leads_only: bool = Query(default=True),
    lead_status: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    return await list_unibox_conversations(db, page=page, page_size=page_size, leads_only=leads_only, lead_status=lead_status)


@router.get("/status")
async def get_unibox_status(inbox_id: int | None = Query(default=None, ge=1)):
    return await get_unibox_sync_status(inbox_id=inbox_id)


@router.get("/notifications")
async def get_unibox_notifications(db: AsyncSession = Depends(get_db)):
    """Return the count of threads with an unread lead reply."""
    count = await get_notification_count(db)
    return {"count": count}


@router.post("/threads/{thread_id}/mark-read")
async def mark_unibox_thread_read(
    thread_id: str,
    inbox_id: int = Query(..., ge=1),
    db: AsyncSession = Depends(get_db),
):
    """Mark a thread's unread-lead-reply flag as cleared."""
    found = await mark_thread_read(db, thread_id=thread_id, inbox_id=inbox_id)
    if not found:
        raise HTTPException(status_code=404, detail="Thread not found")
    await db.commit()
    return {"ok": True, "thread_id": thread_id}


_SYNC_PROVIDERS = ("gmail", "office365", "smtp")


@router.post("/sync")
async def trigger_unibox_sync(data: UniboxSyncRequest, db: AsyncSession = Depends(get_db)):
    if data.inbox_id:
        inbox_row = await db.execute(
            select(Inbox.id).where(Inbox.id == data.inbox_id, Inbox.provider.in_(_SYNC_PROVIDERS))
        )
        inbox_id = inbox_row.scalar_one_or_none()
        if not inbox_id:
            raise HTTPException(status_code=404, detail="Inbox not found")
        await queue_sync_for_inbox(inbox_id, reason="manual")
        return {"ok": True, "queued": 1, "inbox_ids": [inbox_id]}

    inbox_rows = await db.execute(select(Inbox.id).where(Inbox.provider.in_(_SYNC_PROVIDERS)))
    inbox_ids = [int(row[0]) for row in inbox_rows.all()]
    if not inbox_ids:
        return {"ok": True, "queued": 0, "inbox_ids": []}

    await queue_sync_for_all_inboxes(reason="manual")
    return {"ok": True, "queued": len(inbox_ids), "inbox_ids": inbox_ids}


@router.post("/load-more")
async def trigger_unibox_load_more(data: UniboxLoadMoreRequest, db: AsyncSession = Depends(get_db)):
    window_days = max(1, int(data.window_days))
    if data.inbox_id:
        inbox_row = await db.execute(
            select(Inbox.id).where(Inbox.id == data.inbox_id, Inbox.provider.in_(_SYNC_PROVIDERS))
        )
        inbox_id = inbox_row.scalar_one_or_none()
        if not inbox_id:
            raise HTTPException(status_code=404, detail="Inbox not found")
        await queue_backfill_for_inbox(
            inbox_id,
            window_days=window_days,
            reason="manual-backfill",
        )
        return {"ok": True, "queued": 1, "inbox_ids": [inbox_id], "window_days": window_days}

    inbox_rows = await db.execute(select(Inbox.id).where(Inbox.provider.in_(_SYNC_PROVIDERS)))
    inbox_ids = [int(row[0]) for row in inbox_rows.all()]
    if not inbox_ids:
        return {"ok": True, "queued": 0, "inbox_ids": [], "window_days": window_days}

    await queue_backfill_for_all_inboxes(
        window_days=window_days,
        reason="manual-backfill",
    )
    return {"ok": True, "queued": len(inbox_ids), "inbox_ids": inbox_ids, "window_days": window_days}


@router.get("/threads/{thread_id}")
async def get_unibox_thread(
    thread_id: str,
    inbox_id: int | None = Query(default=None, ge=1),
    db: AsyncSession = Depends(get_db),
):
    try:
        payload = await get_thread_messages(db, thread_id=thread_id, inbox_id=inbox_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    hydrated = await hydrate_thread_on_demand(
        db,
        thread_id=payload["thread_id"],
        inbox_id=int(payload["inbox_id"]),
    )
    if hydrated:
        await db.commit()
        payload = await get_thread_messages(
            db,
            thread_id=payload["thread_id"],
            inbox_id=int(payload["inbox_id"]),
        )
        if payload is None:
            raise HTTPException(status_code=404, detail="Thread not found")
    return payload


@router.post("/send")
async def send_unibox_email(data: UniboxSendRequest, db: AsyncSession = Depends(get_db)):
    inbox_row = await db.execute(select(Inbox).where(Inbox.id == data.inbox_id))
    inbox = inbox_row.scalar_one_or_none()
    if not inbox:
        raise HTTPException(status_code=404, detail="Inbox not found")

    provider = inbox.provider or "gmail"
    gmail_account = None
    o365_account = None
    smtp_account = None
    o365_client_id = o365_client_secret = o365_tenant_id = ""
    reply_to = data.in_reply_to
    references = data.references

    if provider == "smtp":
        smtp_row = await db.execute(select(SmtpAccount).where(SmtpAccount.inbox_id == inbox.id))
        smtp_account = smtp_row.scalar_one_or_none()
        if smtp_account is None:
            raise HTTPException(status_code=400, detail="SMTP account is not configured for this inbox")
        reply_graph_message_id = None
        if data.thread_id and not reply_to:
            latest_row = await db.execute(
                select(SmtpMessage)
                .where(SmtpMessage.inbox_id == inbox.id, SmtpMessage.thread_key == data.thread_id)
                .order_by(SmtpMessage.received_at.desc(), SmtpMessage.created_at.desc())
                .limit(1)
            )
            latest_msg = latest_row.scalar_one_or_none()
            if latest_msg and latest_msg.rfc_message_id:
                reply_to = latest_msg.rfc_message_id
                if not references:
                    references = reply_to
    elif provider == "office365":
        from app.routers.office365_oauth import refresh_access_token as _refresh_o365
        o365_row = await db.execute(select(Office365Account).where(Office365Account.inbox_id == inbox.id))
        o365_account = o365_row.scalar_one_or_none()
        if o365_account is None:
            raise HTTPException(status_code=400, detail="Office 365 account is not connected for this inbox")
        o365_client_id, o365_client_secret, o365_tenant_id = await get_office365_oauth_credentials(db)
        refreshed = _refresh_o365(o365_account, o365_client_id, o365_client_secret, o365_tenant_id)
        if not refreshed:
            raise HTTPException(status_code=502, detail="Could not refresh Office 365 access token")
        await db.flush()

        # Auto-detect the Graph message ID to reply to so we can use the Graph
        # Reply API (createReply → patch → send).  This is the only way to
        # have Microsoft set conversationIndex correctly and keep the reply in
        # the same Outlook thread.  We prefer the most-recently-received real
        # Graph message (not a local-surrogate we created at send time).
        reply_graph_message_id: str | None = None
        if data.thread_id:
            # Prefer the latest REAL Graph message (received from outside).
            real_o365_row = await db.execute(
                select(Office365Message)
                .where(
                    Office365Message.inbox_id == inbox.id,
                    Office365Message.conversation_id == data.thread_id,
                    ~Office365Message.message_id.startswith("local-"),
                )
                .order_by(Office365Message.received_at.desc(), Office365Message.created_at.desc())
                .limit(1)
            )
            real_o365_msg = real_o365_row.scalar_one_or_none()
            if real_o365_msg:
                reply_graph_message_id = real_o365_msg.message_id
                if not reply_to and real_o365_msg.internet_message_id:
                    reply_to = real_o365_msg.internet_message_id
            else:
                # Fall back: any message in the thread (may be a local surrogate)
                any_o365_row = await db.execute(
                    select(Office365Message)
                    .where(
                        Office365Message.inbox_id == inbox.id,
                        Office365Message.conversation_id == data.thread_id,
                    )
                    .order_by(Office365Message.received_at.desc())
                    .limit(1)
                )
                any_o365_msg = any_o365_row.scalar_one_or_none()
                if any_o365_msg and any_o365_msg.internet_message_id and not reply_to:
                    reply_to = any_o365_msg.internet_message_id
    else:
        account_row = await db.execute(select(GmailAccount).where(GmailAccount.inbox_id == inbox.id))
        gmail_account = account_row.scalar_one_or_none()
        if gmail_account is None:
            raise HTTPException(status_code=400, detail="Gmail account is not connected for this inbox")
        if data.thread_id and not reply_to:
            latest_row = await db.execute(
                select(GmailMessage)
                .where(GmailMessage.inbox_id == inbox.id, GmailMessage.thread_id == data.thread_id)
                .order_by(GmailMessage.internal_date.desc(), GmailMessage.created_at.desc())
                .limit(1)
            )
            latest_msg = latest_row.scalar_one_or_none()
            if latest_msg:
                reply_to = _extract_message_id_from_headers(latest_msg.headers_json or "")
                if reply_to and not references:
                    references = reply_to

    send_result = send_email(
        to_email=data.to_email,
        subject=data.subject,
        body=data.body,
        from_email=inbox.email,
        from_name=inbox.display_name or "",
        reply_to_msg_id=reply_to,
        references=references,
        is_html=data.is_html,
        provider=provider,
        gmail_account=gmail_account,
        office365_account=o365_account,
        office365_client_id=o365_client_id,
        office365_client_secret=o365_client_secret,
        office365_tenant_id=o365_tenant_id,
        thread_id=data.thread_id,
        conversation_id=data.thread_id if provider == "office365" else None,
        reply_graph_message_id=reply_graph_message_id if provider == "office365" else None,
        smtp_account=smtp_account,
    )
    if not send_result:
        raise HTTPException(status_code=502, detail="Send failed; message was not stored")

    if not isinstance(send_result, SendResult):
        raise HTTPException(status_code=502, detail="Unexpected send response")

    thread_id = send_result.thread_id or data.thread_id
    if not thread_id:
        raise HTTPException(status_code=502, detail="Send succeeded but did not return thread id")

    # Store the sent message in the appropriate provider's local mirror.
    stored_message_id: str = send_result.message_id
    if provider == "smtp":
        smtp_msg = await upsert_sent_smtp_message(
            db,
            inbox_id=inbox.id,
            thread_key=thread_id,
            internet_message_id=send_result.message_id,
            subject=data.subject,
            to_email=str(data.to_email),
            from_email=inbox.email,
            body=data.body,
            is_html=data.is_html,
        )
        if smtp_msg:
            stored_message_id = smtp_msg.message_id
    elif provider == "office365":
        o365_msg = await upsert_sent_o365_message(
            db,
            inbox_id=inbox.id,
            conversation_id=thread_id,
            internet_message_id=send_result.message_id,
            subject=data.subject,
            to_email=str(data.to_email),
            from_email=inbox.email,
            body=data.body,
            is_html=data.is_html,
        )
        if o365_msg:
            stored_message_id = o365_msg.message_id
    else:
        stored_message = await upsert_sent_message(
            db,
            inbox_id=inbox.id,
            thread_id=thread_id,
            gmail_message_id=send_result.gmail_message_id,
            rfc_message_id=send_result.message_id,
            subject=data.subject,
            to_email=str(data.to_email),
            from_email=inbox.email,
            body=data.body,
            is_html=data.is_html,
        )
        stored_message_id = stored_message.message_id

    # Update sync state timestamp for whichever provider this inbox uses.
    if provider == "smtp":
        from app.models import SmtpSyncState
        smtp_ss_row = await db.execute(
            select(SmtpSyncState).where(SmtpSyncState.inbox_id == inbox.id)
        )
        smtp_ss = smtp_ss_row.scalar_one_or_none()
        if smtp_ss:
            smtp_ss.last_sync_at = utcnow()
    elif provider == "office365":
        from app.models import Office365SyncState
        o365_ss_row = await db.execute(
            select(Office365SyncState).where(Office365SyncState.inbox_id == inbox.id)
        )
        o365_ss = o365_ss_row.scalar_one_or_none()
        if o365_ss:
            o365_ss.last_sync_at = utcnow()
    else:
        sync_state_row = await db.execute(select(GmailSyncState).where(GmailSyncState.inbox_id == inbox.id))
        sync_state = sync_state_row.scalar_one_or_none()
        if sync_state:
            sync_state.last_sync_at = utcnow()

    # Commit before confirming success, so UI gets backend-confirmed state only.
    await db.commit()

    await unibox_events.publish(
        {
            "type": "unibox.thread.updated",
            "reason": "send",
            "inbox_id": inbox.id,
            "thread_id": thread_id,
            "timestamp": utcnow().isoformat() + "Z",
        }
    )

    return {
        "ok": True,
        "status": "sent",
        "inbox_id": inbox.id,
        "thread_id": thread_id,
        "message_id": stored_message_id,
        "rfc_message_id": send_result.message_id,
        "timestamp": utcnow().isoformat() + "Z",
    }


@router.get("/events")
async def unibox_events_sse(request: Request):
    queue = await unibox_events.subscribe()

    async def stream():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue

                event_type = str(event.get("type", "unibox.update"))
                data = json.dumps(event, default=str)
                yield f"event: {event_type}\ndata: {data}\n\n"
        finally:
            await unibox_events.unsubscribe(queue)

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(stream(), media_type="text/event-stream", headers=headers)


@router.post("/gmail/push")
async def gmail_push_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    try:
        envelope = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    message = envelope.get("message", {}) if isinstance(envelope, dict) else {}
    raw_data = message.get("data") if isinstance(message, dict) else None
    if not raw_data:
        return {"ok": True, "ignored": True}

    payload = decode_push_message_data(str(raw_data))
    gmail_email = str(payload.get("emailAddress", "")).strip().lower()
    if not gmail_email:
        return {"ok": True, "ignored": True}

    account_row = await db.execute(
        select(GmailAccount).where(GmailAccount.google_email == gmail_email)
    )
    account = account_row.scalar_one_or_none()
    if not account:
        # fallback: some accounts may use inbox email while Google returns normalized email.
        inbox_row = await db.execute(select(Inbox).where(Inbox.email == gmail_email))
        inbox = inbox_row.scalar_one_or_none()
        if not inbox:
            return {"ok": True, "ignored": True}
        account_row = await db.execute(select(GmailAccount).where(GmailAccount.inbox_id == inbox.id))
        account = account_row.scalar_one_or_none()
        if not account:
            return {"ok": True, "ignored": True}

    # Do not advance sync checkpoints from push payload historyId.
    # The payload historyId represents "latest known" state at notification time;
    # if we overwrite our own checkpoint first, the subsequent history delta query
    # can skip the very changes that triggered this push.
    await db.commit()
    await queue_sync_for_inbox(account.inbox_id, reason="push")
    return {"ok": True}
