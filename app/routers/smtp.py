"""Generic SMTP / IMAP inbox provider routes (per-inbox credentials)."""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import Inbox, SmtpAccount
from app.smtp_utils import (
    sanitize_connection_error,
    test_account_connections,
    validate_smtp_account_payload,
)
from app.time import utcnow

log = logging.getLogger("quickly.smtp_router")

router = APIRouter(prefix="/api/smtp", tags=["smtp"])


class SmtpAccountUpsert(BaseModel):
    smtp_host: str = Field(..., max_length=255)
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str = Field(..., max_length=255)
    # Empty string on update means "keep the stored secret" (create requires one).
    smtp_password: str = Field(default="", max_length=1024)
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    imap_host: str = Field(default="", max_length=255)
    imap_port: int = Field(default=993, ge=1, le=65535)
    imap_username: str = Field(default="", max_length=255)
    imap_password: str = Field(default="", max_length=1024)
    imap_use_ssl: bool = True


class SmtpAccountResponse(BaseModel):
    id: int
    inbox_id: int
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_use_tls: bool
    smtp_use_ssl: bool
    imap_host: str
    imap_port: int
    imap_username: str
    imap_use_ssl: bool
    has_smtp_password: bool = False
    has_imap_password: bool = False
    last_tested_at: str | None = None
    last_test_ok: bool = False
    last_test_error: str = ""

    class Config:
        from_attributes = True


def _to_response(acct: SmtpAccount) -> dict:
    return {
        "id": acct.id,
        "inbox_id": acct.inbox_id,
        "smtp_host": acct.smtp_host,
        "smtp_port": acct.smtp_port,
        "smtp_username": acct.smtp_username,
        "smtp_use_tls": bool(acct.smtp_use_tls),
        "smtp_use_ssl": bool(acct.smtp_use_ssl),
        "imap_host": acct.imap_host or "",
        "imap_port": acct.imap_port or 993,
        "imap_username": acct.imap_username or "",
        "imap_use_ssl": bool(acct.imap_use_ssl),
        "has_smtp_password": bool(acct.smtp_password),
        "has_imap_password": bool(acct.imap_password),
        "last_tested_at": acct.last_tested_at.isoformat() if acct.last_tested_at else None,
        "last_test_ok": bool(acct.last_test_ok),
        "last_test_error": acct.last_test_error or "",
    }


async def _get_smtp_inbox(db: AsyncSession, inbox_id: int) -> Inbox:
    result = await db.execute(select(Inbox).where(Inbox.id == inbox_id))
    inbox = result.scalar_one_or_none()
    if not inbox:
        raise HTTPException(404, "Inbox not found")
    if (inbox.provider or "") != "smtp":
        raise HTTPException(400, "Inbox is not an SMTP inbox (provider must be 'smtp')")
    return inbox


@router.get("/accounts")
async def list_smtp_accounts(
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    """List all SMTP accounts with their parent inbox info."""
    result = await db.execute(
        select(SmtpAccount, Inbox)
        .join(Inbox, SmtpAccount.inbox_id == Inbox.id)
        .order_by(SmtpAccount.created_at.desc())
    )
    rows = result.all()
    return [
        {
            **_to_response(acct),
            "inbox_email": inbox.email,
            "inbox_display_name": inbox.display_name,
            "max_emails_per_day": inbox.max_emails_per_day,
        }
        for acct, inbox in rows
    ]


@router.get("/inboxes/{inbox_id}", response_model=SmtpAccountResponse)
async def get_smtp_account(
    inbox_id: int,
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    await _get_smtp_inbox(db, inbox_id)
    result = await db.execute(select(SmtpAccount).where(SmtpAccount.inbox_id == inbox_id))
    acct = result.scalar_one_or_none()
    if not acct:
        raise HTTPException(404, "SMTP account not configured for this inbox")
    return _to_response(acct)


@router.put("/inboxes/{inbox_id}", response_model=SmtpAccountResponse)
async def upsert_smtp_account(
    inbox_id: int,
    data: SmtpAccountUpsert,
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    """Create or replace the SMTP/IMAP credentials for an SMTP inbox.

    Does NOT test the connection (use ``POST .../test`` for that) so that
    bulk edits stay fast; the UI calls test explicitly.
    """
    await _get_smtp_inbox(db, inbox_id)
    payload = data.model_dump()

    result = await db.execute(select(SmtpAccount).where(SmtpAccount.inbox_id == inbox_id))
    acct = result.scalar_one_or_none()
    is_create = acct is None
    # For validation on update, fall back to stored secrets when the caller
    # left password fields empty (meaning "keep").
    effective = dict(payload)
    if not is_create:
        if not effective.get("smtp_password"):
            effective["smtp_password"] = acct.smtp_password or ""
        if not effective.get("imap_password"):
            effective["imap_password"] = acct.imap_password or ""
    err = validate_smtp_account_payload(effective, require_password=is_create)
    if err:
        raise HTTPException(400, err)
    if is_create:
        if not (payload.get("smtp_password") or ""):
            raise HTTPException(400, "smtp_password is required")
        acct = SmtpAccount(inbox_id=inbox_id)
        db.add(acct)
    acct.smtp_host = payload["smtp_host"].strip()
    acct.smtp_port = int(payload["smtp_port"])
    acct.smtp_username = payload["smtp_username"].strip()
    # Empty password on update keeps the stored secret.
    if payload.get("smtp_password"):
        acct.smtp_password = payload["smtp_password"]
    elif is_create:
        acct.smtp_password = ""
    acct.smtp_use_tls = bool(payload["smtp_use_tls"])
    acct.smtp_use_ssl = bool(payload["smtp_use_ssl"])
    acct.imap_host = (payload.get("imap_host") or "").strip()
    acct.imap_port = int(payload.get("imap_port") or 993)
    acct.imap_username = (payload.get("imap_username") or "").strip()
    # Only overwrite the IMAP password when the caller sent one (empty string
    # from the UI means "keep the stored secret").
    if payload.get("imap_password"):
        acct.imap_password = payload["imap_password"]
    elif not acct.imap_host:
        acct.imap_password = ""
    acct.imap_use_ssl = bool(payload.get("imap_use_ssl", True))
    acct.updated_at = utcnow()
    await db.flush()
    log.info("SMTP account saved: inbox_id=%s host=%s", inbox_id, acct.smtp_host)
    return _to_response(acct)


@router.post("/inboxes/{inbox_id}/test")
async def test_smtp_account(
    inbox_id: int,
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    """Test the stored SMTP (+ IMAP when configured) connection and persist the result."""
    await _get_smtp_inbox(db, inbox_id)
    result = await db.execute(select(SmtpAccount).where(SmtpAccount.inbox_id == inbox_id))
    acct = result.scalar_one_or_none()
    if not acct:
        raise HTTPException(404, "SMTP account not configured for this inbox")

    smtp_res, imap_res = await asyncio.to_thread(test_account_connections, acct)
    ok = bool(smtp_res.ok and imap_res.ok)
    err_parts = [p for p in (smtp_res.error, imap_res.error) if p]
    acct.last_tested_at = utcnow()
    acct.last_test_ok = ok
    # Persist a sanitised category message — raw exception text can leak
    # internal hostnames/ports and act as a network-probing oracle. Full
    # detail goes to the application log only.
    last_err = sanitize_connection_error("; ".join(err_parts))
    acct.last_test_error = last_err[:2000]
    acct.updated_at = utcnow()
    await db.flush()
    log.info("SMTP test: inbox_id=%s ok=%s errors=%r", inbox_id, ok, err_parts)
    return {
        "ok": ok,
        "smtp": {
            "ok": smtp_res.ok,
            "error": smtp_res.error if smtp_res.ok else sanitize_connection_error(smtp_res.error),
            "detail": smtp_res.detail,
        },
        "imap": {
            "ok": imap_res.ok,
            "error": imap_res.error if imap_res.ok else sanitize_connection_error(imap_res.error),
            "detail": imap_res.detail,
        },
        "last_tested_at": acct.last_tested_at.isoformat(),
    }


@router.delete("/inboxes/{inbox_id}")
async def disconnect_smtp(
    inbox_id: int,
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    """Remove SMTP credentials (inbox row itself is kept for history)."""
    await _get_smtp_inbox(db, inbox_id)
    result = await db.execute(select(SmtpAccount).where(SmtpAccount.inbox_id == inbox_id))
    acct = result.scalar_one_or_none()
    if not acct:
        raise HTTPException(404, "SMTP account not found")
    inbox = await db.get(Inbox, inbox_id)
    email = inbox.email if inbox else ""
    await db.delete(acct)
    await db.flush()
    log.info("SMTP disconnected: inbox_id=%s", inbox_id)
    return {"ok": True, "inbox_id": inbox_id, "email": email}
