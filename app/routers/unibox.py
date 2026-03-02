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
from app.models import GmailAccount, GmailMessage, GmailSyncState, Inbox
from app.sender import SendResult, send_email
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
    db: AsyncSession = Depends(get_db),
):
    return await list_unibox_conversations(db, page=page, page_size=page_size, leads_only=leads_only)


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


@router.post("/sync")
async def trigger_unibox_sync(data: UniboxSyncRequest, db: AsyncSession = Depends(get_db)):
    if data.inbox_id:
        inbox_row = await db.execute(
            select(Inbox.id).where(Inbox.id == data.inbox_id, Inbox.provider == "gmail")
        )
        inbox_id = inbox_row.scalar_one_or_none()
        if not inbox_id:
            raise HTTPException(status_code=404, detail="Gmail inbox not found")
        await queue_sync_for_inbox(inbox_id, reason="manual")
        return {"ok": True, "queued": 1, "inbox_ids": [inbox_id]}

    inbox_rows = await db.execute(select(Inbox.id).where(Inbox.provider == "gmail"))
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
            select(Inbox.id).where(Inbox.id == data.inbox_id, Inbox.provider == "gmail")
        )
        inbox_id = inbox_row.scalar_one_or_none()
        if not inbox_id:
            raise HTTPException(status_code=404, detail="Gmail inbox not found")
        await queue_backfill_for_inbox(
            inbox_id,
            window_days=window_days,
            reason="manual-backfill",
        )
        return {"ok": True, "queued": 1, "inbox_ids": [inbox_id], "window_days": window_days}

    inbox_rows = await db.execute(select(Inbox.id).where(Inbox.provider == "gmail"))
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
    if inbox.provider != "gmail":
        raise HTTPException(status_code=400, detail="Unibox send currently supports Gmail inboxes only")

    account_row = await db.execute(select(GmailAccount).where(GmailAccount.inbox_id == inbox.id))
    account = account_row.scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=400, detail="Gmail account is not connected for this inbox")

    reply_to = data.in_reply_to
    references = data.references
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
        provider="gmail",
        gmail_account=account,
        thread_id=data.thread_id,
    )
    if not send_result:
        raise HTTPException(status_code=502, detail="Gmail send failed; message was not stored")

    if not isinstance(send_result, SendResult):
        raise HTTPException(status_code=502, detail="Unexpected Gmail send response")

    thread_id = send_result.thread_id or data.thread_id
    if not thread_id:
        raise HTTPException(status_code=502, detail="Gmail send succeeded but did not return thread id")

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

    sync_state_row = await db.execute(select(GmailSyncState).where(GmailSyncState.inbox_id == inbox.id))
    sync_state = sync_state_row.scalar_one_or_none()
    if sync_state:
        sync_state.last_sync_at = datetime.utcnow()

    # Commit before confirming success, so UI gets backend-confirmed state only.
    await db.commit()

    await unibox_events.publish(
        {
            "type": "unibox.thread.updated",
            "reason": "send",
            "inbox_id": inbox.id,
            "thread_id": thread_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
    )

    return {
        "ok": True,
        "status": "sent",
        "inbox_id": inbox.id,
        "thread_id": thread_id,
        "message_id": stored_message.message_id,
        "rfc_message_id": send_result.message_id,
        "timestamp": datetime.utcnow().isoformat() + "Z",
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
