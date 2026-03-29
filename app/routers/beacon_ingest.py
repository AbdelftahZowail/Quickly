"""Inbound webhook from Quickly Beacon (signed HMAC)."""
from __future__ import annotations

# // BEACON SYNC: This file handles tracking logic that Beacon mirrors.
# // Any changes to tracking behavior, event types, metadata collected, or URL structure
# // must also be reflected in the Beacon service (/beacon or the separate Beacon repo).
# // If you are an AI agent, add a TODO at the end of your response listing what needs to be updated in Beacon.

import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Inbox, TrackedLink
from app.tracking_events import record_email_click, record_email_open, process_unsubscribe

log = logging.getLogger("quickly.beacon_ingest")

router = APIRouter(tags=["beacon-ingest"])


def _verify_signature(body: bytes, secret: str, sig_header: str) -> bool:
    if not sig_header.startswith("sha256="):
        return False
    want = sig_header[7:].strip().lower()
    got = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, got)


@router.post("/api/beacon/ingest")
@router.post("/api/beacon/ingest/")
async def beacon_ingest(request: Request, db: AsyncSession = Depends(get_db)):
    body = await request.body()
    try:
        data = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(400, "invalid JSON body")

    inbox_id = data.get("inbox_id")
    kind = data.get("kind")
    if not isinstance(inbox_id, int) or kind not in ("open", "click", "unsubscribe"):
        raise HTTPException(422, "invalid payload")

    res = await db.execute(select(Inbox).where(Inbox.id == inbox_id))
    inbox = res.scalar_one_or_none()
    if not inbox or not inbox.beacon_webhook_secret:
        raise HTTPException(401, "unknown inbox or beacon not configured")

    sig = request.headers.get("X-Beacon-Signature") or ""
    if not _verify_signature(body, inbox.beacon_webhook_secret, sig):
        raise HTTPException(401, "invalid signature")

    path_token = data.get("path_token")
    if not isinstance(path_token, str) or not path_token:
        raise HTTPException(422, "missing path_token")

    ip = data.get("ip_address")
    if ip is not None and not isinstance(ip, str):
        raise HTTPException(422, "invalid ip_address")

    if kind == "open":
        await record_email_open(db, path_token, ip)
        return {"ok": True}

    if kind == "click":
        original_url = data.get("original_url")
        if not isinstance(original_url, str) or not original_url:
            raise HTTPException(422, "click requires original_url")
        tr = await db.execute(select(TrackedLink).where(TrackedLink.token == path_token))
        link = tr.scalar_one_or_none()
        if not link or link.original_url != original_url:
            log.warning("beacon click token/url mismatch token=%s", path_token[:8])
            return {"ok": True, "skipped": True}
        await record_email_click(db, path_token, ip)
        return {"ok": True}

    await process_unsubscribe(db, path_token)
    return {"ok": True}
