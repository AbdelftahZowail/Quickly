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
    apply_port_tls_inference,
    derive_inbox_health,
    sanitize_connection_error,
    test_account_connections,
    validate_smtp_account_payload,
)
from app.time import utcnow

log = logging.getLogger("quickly.smtp_router")

router = APIRouter(prefix="/api/smtp", tags=["smtp"])

# Cooldown so the diagnostic endpoints cannot be hammered (each one opens real
# sockets to the mail host).  In-memory like the send-job auth cooldown.
DIAGNOSE_COOLDOWN_SECONDS = 20
SEND_TEST_COOLDOWN_SECONDS = 20
_last_diagnose_at: dict[int, float] = {}
_last_send_test_at: dict[int, float] = {}


def _cooldown_remaining(store: dict[int, float], inbox_id: int, window: float) -> float:
    """Seconds left before *inbox_id* may run the action again (0 = allowed)."""
    import time as _time

    last = store.get(inbox_id)
    if not last:
        return 0.0
    return max(0.0, window - (_time.monotonic() - last))


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
    last_send_error: str = ""
    last_send_at: str | None = None
    last_diagnostic_at: str | None = None
    health: str = "unknown"

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
        "last_send_error": acct.last_send_error or "",
        "last_send_at": acct.last_send_at.isoformat() if acct.last_send_at else None,
        "last_diagnostic_at": (
            acct.last_diagnostic_at.isoformat() if acct.last_diagnostic_at else None
        ),
        "health": derive_inbox_health(
            paused=False,
            last_send_error=acct.last_send_error or "",
            last_send_at=acct.last_send_at,
            last_test_ok=bool(acct.last_test_ok),
            last_tested_at=acct.last_tested_at,
        ),
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
    # Infer STARTTLS/SSL from the port so 465+STARTTLS (the broken default combo)
    # cannot be saved as-is.
    use_tls, use_ssl = apply_port_tls_inference(
        acct.smtp_port, bool(payload["smtp_use_tls"]), bool(payload["smtp_use_ssl"])
    )
    acct.smtp_use_tls = use_tls
    acct.smtp_use_ssl = use_ssl
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


@router.post("/inboxes/{inbox_id}/diagnose")
async def diagnose_smtp_account(
    inbox_id: int,
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    """Run the full staged SMTP/IMAP diagnostic and persist the report.

    Unlike ``POST .../test`` (EHLO → TLS → LOGIN → NOOP), this drives a real
    MAIL FROM/RCPT TO/DATA probe and returns a per-stage breakdown with
    concrete fixes.  Results are stored on the account so the inbox UI can
    redraw the last report without re-probing.
    """
    import json as _json

    from app.smtp_diagnose import diagnose_account

    await _get_smtp_inbox(db, inbox_id)
    result = await db.execute(select(SmtpAccount).where(SmtpAccount.inbox_id == inbox_id))
    acct = result.scalar_one_or_none()
    if not acct:
        raise HTTPException(404, "SMTP account not configured for this inbox")

    remaining = _cooldown_remaining(_last_diagnose_at, inbox_id, DIAGNOSE_COOLDOWN_SECONDS)
    if remaining > 0:
        raise HTTPException(
            429,
            {
                "error": "cooldown",
                "message": f"Please wait {int(remaining) + 1}s before diagnosing again.",
                "retry_after": int(remaining) + 1,
            },
        )

    # Run the blocking probe off the event loop; it has its own per-stage (~8s)
    # and overall (~30s) timeouts so the request worker cannot hang.
    report = await asyncio.wait_for(
        asyncio.to_thread(diagnose_account, acct), timeout=45.0
    )
    _last_diagnose_at[inbox_id] = __import__("time").monotonic()

    acct.last_diagnostic_at = utcnow()
    acct.last_diagnostic_json = _json.dumps(report)[:20000]
    # Keep the lightweight test fields in sync so existing UI/health keep working.
    acct.last_tested_at = acct.last_diagnostic_at
    acct.last_test_ok = bool(report.get("ok"))
    if report.get("ok"):
        acct.last_test_error = ""
    else:
        acct.last_test_error = sanitize_connection_error(report.get("verdict") or "")[:2000]
    if report.get("ok"):
        # A passing diagnostic means the credential/transport is healthy again.
        from app.jobs import clear_inbox_auth_failure

        clear_inbox_auth_failure(inbox_id)
    acct.updated_at = utcnow()
    await db.flush()
    log.info("SMTP diagnose: inbox_id=%s ok=%s", inbox_id, report.get("ok"))
    return report


class SendTestRequest(BaseModel):
    to_email: str = Field(..., max_length=320)


@router.post("/inboxes/{inbox_id}/send-test")
async def send_test_email(
    inbox_id: int,
    data: SendTestRequest,
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    """Send a real message through the exact same code path campaigns use.

    Calls ``app.sender._send_via_smtp`` (via ``send_email``) so "auth OK but
    relay rejects the sender" is caught here, unlike the connection test.
    """
    import time as _time

    to_email = (data.to_email or "").strip()
    if "@" not in to_email:
        raise HTTPException(400, "A valid to_email is required")

    await _get_smtp_inbox(db, inbox_id)
    result = await db.execute(select(SmtpAccount).where(SmtpAccount.inbox_id == inbox_id))
    acct = result.scalar_one_or_none()
    if not acct:
        raise HTTPException(404, "SMTP account not configured for this inbox")

    remaining = _cooldown_remaining(_last_send_test_at, inbox_id, SEND_TEST_COOLDOWN_SECONDS)
    if remaining > 0:
        raise HTTPException(
            429,
            {
                "error": "cooldown",
                "message": f"Please wait {int(remaining) + 1}s before sending another test.",
                "retry_after": int(remaining) + 1,
            },
        )

    inbox = await db.get(Inbox, inbox_id)
    from_email = (inbox.email if inbox else "") or acct.smtp_username
    _last_send_test_at[inbox_id] = _time.monotonic()

    from app.sender import SendFailure, SendResult, _send_via_smtp

    def _do_send():
        return _send_via_smtp(
            to_email=to_email,
            subject="Quickly test email",
            body=(
                "This is a test message sent by Quickly's \"Send test email\" action.\n\n"
                "If you can read this, the SMTP inbox is delivering mail end to end."
            ),
            from_email=from_email,
            from_name=(getattr(inbox, "display_name", "") or ""),
            smtp_account=acct,
        )

    try:
        result_obj = await asyncio.wait_for(asyncio.to_thread(_do_send), timeout=60.0)
    except asyncio.TimeoutError:
        acct.last_send_error = "Test send timed out"
        acct.last_send_at = utcnow()
        acct.updated_at = utcnow()
        await db.flush()
        return {
            "ok": False,
            "error": "timeout",
            "message": "The test send timed out — the relay did not answer in time.",
            "last_send_at": acct.last_send_at.isoformat(),
        }

    acct.updated_at = utcnow()
    if isinstance(result_obj, SendResult):
        acct.last_send_error = ""
        acct.last_send_at = utcnow()
        await db.flush()
        log.info("SMTP send-test: inbox_id=%s to=%s ok", inbox_id, to_email)
        return {
            "ok": True,
            "message_id": result_obj.message_id,
            "relay_response": "250 OK (accepted by the relay)",
            "last_send_at": acct.last_send_at.isoformat(),
        }

    if isinstance(result_obj, SendFailure):
        acct.last_send_error = result_obj.message[:2000]
        acct.last_send_at = utcnow()
        await db.flush()
        log.info("SMTP send-test: inbox_id=%s to=%s failed type=%s", inbox_id, to_email, result_obj.error_type)
        return {
            "ok": False,
            "error": result_obj.error_type,
            "message": result_obj.message,
            "last_send_at": acct.last_send_at.isoformat(),
        }

    # Transient (None)
    err = acct.last_send_error or "SMTP transient failure (connection error)"
    acct.last_send_at = utcnow()
    await db.flush()
    log.info("SMTP send-test: inbox_id=%s to=%s transient", inbox_id, to_email)
    return {
        "ok": False,
        "error": "transient",
        "message": err,
        "last_send_at": acct.last_send_at.isoformat(),
    }


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
    if ok:
        # A successful connection test means the credentials work again; clear
        # any send cooldown so queued slots resume immediately.
        from app.jobs import clear_inbox_auth_failure

        clear_inbox_auth_failure(inbox_id)
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
