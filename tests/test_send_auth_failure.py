"""Auth-failure handling in the per-slot send job (issue #1).

A broken SMTP/IMAP credential used to leave the queue slot in place and retry
forever, each attempt holding a DB transaction open across a blocking SMTP
call.  These tests pin the fixed behaviour: the inbox is paused, the
in-memory circuit breaker skips the remaining due slots, and the event carries
enough context for a correctly-labelled notification.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select

import app.jobs as jobs_mod
from app.models import EmailLog, QueueSlot, SmtpAccount
from app.sender import SendFailure, SendResult
from tests.conftest import (
    make_campaign,
    make_campaign_inbox,
    make_campaign_lead,
    make_inbox,
    make_lead,
    make_queue_slot,
    make_sequence,
)


class _SessionCtx:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return None


async def _make_smtp_inbox(session, email: str = "relay@example.com"):
    inbox = await make_inbox(session, email=email, provider="smtp")
    session.add(
        SmtpAccount(
            inbox_id=inbox.id,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_username="user",
            smtp_password="pass",
            smtp_use_tls=True,
        )
    )
    await session.flush()
    return inbox


async def _make_due_slot(session, inbox, campaign=None):
    if campaign is None:
        campaign = await make_campaign(
            session,
            sending_days=[0, 1, 2, 3, 4, 5, 6],
            sending_hours_start="00:00",
            sending_hours_end="23:59",
        )
    await make_sequence(session, campaign.id)
    lead = await make_lead(session)
    cl = await make_campaign_lead(session, campaign.id, lead.id)
    await make_campaign_inbox(session, campaign.id, inbox.id)
    slot = await make_queue_slot(
        session, cl.id, inbox.id, scheduled_date=datetime.utcnow() - timedelta(minutes=1)
    )
    await session.flush()
    return slot


@pytest.mark.asyncio
async def test_smtp_auth_failure_pauses_inbox_and_labels_event(session, monkeypatch):
    inbox = await _make_smtp_inbox(session)
    slot = await _make_due_slot(session, inbox)

    events = []

    async def fake_webhook(db, event, data):
        events.append((event, data))

    monkeypatch.setattr("app.jobs.fire_webhook_event", fake_webhook)
    monkeypatch.setattr(
        "app.jobs.send_email",
        lambda **kwargs: SendFailure(
            error_type="auth_failed", message="SMTP authentication failed: 535"
        ),
    )
    monkeypatch.setattr(jobs_mod, "AsyncSessionLocal", lambda: _SessionCtx(session))
    jobs_mod._inbox_auth_cooldown_until.clear()

    await jobs_mod.send_slot_job(slot.id)

    # The inbox is paused so the next scan does not retry the broken credential.
    await session.refresh(inbox)
    assert inbox.paused is True

    # Pre-created log removed; slot retained for when credentials are fixed.
    assert (
        await session.execute(select(func.count(EmailLog.id)).where(EmailLog.inbox_id == inbox.id))
    ).scalar() == 0
    assert (
        await session.execute(select(func.count(QueueSlot.id)).where(QueueSlot.inbox_id == inbox.id))
    ).scalar() == 1

    ev = next((e for e in events if e[0] == "token_expired"), None)
    assert ev is not None, "auth failure must fire a token_expired event"
    assert ev[1]["provider"] == "smtp"
    assert ev[1]["error_type"] == "auth_failed"
    assert ev[1]["inbox_email"] == inbox.email


@pytest.mark.asyncio
async def test_auth_failure_circuit_breaker_skips_remaining_slots(session, monkeypatch):
    inbox = await _make_smtp_inbox(session, email="relay2@example.com")
    slot1 = await _make_due_slot(session, inbox)
    slot2 = await _make_due_slot(session, inbox)

    calls: list[str | None] = []

    def fake_send(**kwargs):
        calls.append(kwargs.get("to_email"))
        return SendFailure(error_type="auth_failed", message="535")

    async def fake_webhook(db, event, data):
        return None

    monkeypatch.setattr("app.jobs.fire_webhook_event", fake_webhook)
    monkeypatch.setattr("app.jobs.send_email", fake_send)
    monkeypatch.setattr(jobs_mod, "AsyncSessionLocal", lambda: _SessionCtx(session))
    jobs_mod._inbox_auth_cooldown_until.clear()

    await jobs_mod.send_slot_job(slot1.id)
    assert len(calls) == 1

    # Unpause to prove the in-memory cooldown (not just inbox.paused) is what
    # stops the second slot already dispatched in the same scan tick.
    inbox.paused = False
    await session.flush()
    await jobs_mod.send_slot_job(slot2.id)

    assert len(calls) == 1, "second slot must be skipped while the inbox is in auth cooldown"


@pytest.mark.asyncio
async def test_sender_display_name_renders_lead_variables(session, monkeypatch):
    """The From: name must support the same {{variables}} as subject/body."""
    inbox = await _make_smtp_inbox(session, email="brand@example.com")
    inbox.display_name = "{{name}} at Acme"
    await session.flush()
    slot = await _make_due_slot(session, inbox)

    captured: dict = {}

    def fake_send(**kwargs):
        captured.update(kwargs)
        return SendResult(message_id="<x>", thread_id="t")

    async def fake_webhook(db, event, data):
        return None

    monkeypatch.setattr("app.jobs.fire_webhook_event", fake_webhook)
    monkeypatch.setattr("app.jobs.send_email", fake_send)
    monkeypatch.setattr(jobs_mod, "AsyncSessionLocal", lambda: _SessionCtx(session))
    jobs_mod._inbox_auth_cooldown_until.clear()

    await jobs_mod.send_slot_job(slot.id)

    # make_lead() default name is "Test Lead"
    assert captured["from_name"] == "Test Lead at Acme"
    # one-click unsubscribe is on by default
    assert captured["list_unsubscribe_one_click"] is True
    assert captured["list_unsubscribe_url"]


@pytest.mark.asyncio
async def test_send_email_raising_removes_precreated_email_log(session, monkeypatch):
    """An unexpected exception from send_email must not leave an orphan EmailLog.

    The row is committed *before* the network call (so tracking tokens exist),
    so without cleanup a crash would consume the inbox's daily quota, inflate
    campaign ``emails_sent`` and make queue recalculation think the step had
    already been sent.
    """
    inbox = await _make_smtp_inbox(session, email="boom@example.com")
    slot = await _make_due_slot(session, inbox)

    def exploding_send(**kwargs):
        raise RuntimeError("transport exploded")

    async def fake_webhook(db, event, data):
        return None

    monkeypatch.setattr("app.jobs.fire_webhook_event", fake_webhook)
    monkeypatch.setattr("app.jobs.send_email", exploding_send)
    monkeypatch.setattr(jobs_mod, "AsyncSessionLocal", lambda: _SessionCtx(session))
    jobs_mod._inbox_auth_cooldown_until.clear()

    before = (
        await session.execute(select(func.count(EmailLog.id)).where(EmailLog.inbox_id == inbox.id))
    ).scalar()
    assert before == 0

    # The exception must propagate so _dispatch_slot can log it and the slot is
    # retried by the next scan.
    with pytest.raises(RuntimeError, match="transport exploded"):
        await jobs_mod.send_slot_job(slot.id)

    remaining = (
        await session.execute(select(func.count(EmailLog.id)).where(EmailLog.inbox_id == inbox.id))
    ).scalar()
    assert remaining == 0, "pre-created EmailLog row must be rolled back"

    # The slot itself is retained so the send can be retried.
    slots = (
        await session.execute(select(func.count(QueueSlot.id)).where(QueueSlot.inbox_id == inbox.id))
    ).scalar()
    assert slots == 1


@pytest.mark.asyncio
async def test_unpause_inbox_clears_auth_cooldown(session):
    """A manual unpause must drop the 15-minute in-memory send cooldown."""
    from fastapi import BackgroundTasks

    from app.routers import inbox as inbox_router

    inbox = await _make_smtp_inbox(session, email="resume-me@example.com")
    inbox.paused = True
    await session.flush()

    jobs_mod._mark_inbox_auth_failure(inbox.id, jobs_mod.time_provider.now())
    assert jobs_mod._inbox_auth_cooldown_active(inbox.id, jobs_mod.time_provider.now()) is True

    await inbox_router.unpause_inbox(inbox.id, BackgroundTasks(), db=session)

    assert jobs_mod._inbox_auth_cooldown_active(inbox.id, jobs_mod.time_provider.now()) is False


@pytest.mark.asyncio
async def test_successful_smtp_test_clears_auth_cooldown(session, monkeypatch):
    """A passing SMTP connection test must drop the in-memory send cooldown."""
    from app.routers import smtp as smtp_router

    inbox = await _make_smtp_inbox(session, email="test-ok@example.com")
    acct = (
        await session.execute(select(SmtpAccount).where(SmtpAccount.inbox_id == inbox.id))
    ).scalar_one()

    class _R:
        def __init__(self, ok, error="", detail=""):
            self.ok = ok
            self.error = error
            self.detail = detail

    monkeypatch.setattr(
        smtp_router,
        "test_account_connections",
        lambda account: (_R(True, "", "SMTP ok"), _R(True, "", "skipped")),
    )
    jobs_mod._mark_inbox_auth_failure(inbox.id, jobs_mod.time_provider.now())
    assert jobs_mod._inbox_auth_cooldown_active(inbox.id, jobs_mod.time_provider.now()) is True

    result = await smtp_router.test_smtp_account(inbox.id, db=session, _user=object())

    assert result["ok"] is True
    assert acct.last_test_ok is True
    assert jobs_mod._inbox_auth_cooldown_active(inbox.id, jobs_mod.time_provider.now()) is False


@pytest.mark.asyncio
async def test_failed_smtp_test_keeps_auth_cooldown(session, monkeypatch):
    """A failing connection test must NOT clear the cooldown."""
    from app.routers import smtp as smtp_router

    inbox = await _make_smtp_inbox(session, email="test-bad@example.com")

    class _R:
        def __init__(self, ok, error="", detail=""):
            self.ok = ok
            self.error = error
            self.detail = detail

    monkeypatch.setattr(
        smtp_router,
        "test_account_connections",
        lambda account: (_R(False, "auth failed"), _R(True, "", "skipped")),
    )
    jobs_mod._mark_inbox_auth_failure(inbox.id, jobs_mod.time_provider.now())

    await smtp_router.test_smtp_account(inbox.id, db=session, _user=object())

    assert jobs_mod._inbox_auth_cooldown_active(inbox.id, jobs_mod.time_provider.now()) is True


@pytest.mark.asyncio
async def test_one_click_unsubscribe_flag_comes_from_campaign(session, monkeypatch):
    """campaign.add_one_click_unsubscribe=False keeps List-Unsubscribe but drops -Post."""
    inbox = await _make_smtp_inbox(session, email="brand2@example.com")
    campaign = await make_campaign(
        session,
        sending_days=[0, 1, 2, 3, 4, 5, 6],
        sending_hours_start="00:00",
        sending_hours_end="23:59",
    )
    campaign.add_one_click_unsubscribe = False
    await session.flush()
    slot = await _make_due_slot(session, inbox, campaign=campaign)

    captured: dict = {}

    def fake_send(**kwargs):
        captured.update(kwargs)
        return SendResult(message_id="<x>", thread_id="t")

    async def fake_webhook(db, event, data):
        return None

    monkeypatch.setattr("app.jobs.fire_webhook_event", fake_webhook)
    monkeypatch.setattr("app.jobs.send_email", fake_send)
    monkeypatch.setattr(jobs_mod, "AsyncSessionLocal", lambda: _SessionCtx(session))
    jobs_mod._inbox_auth_cooldown_until.clear()

    await jobs_mod.send_slot_job(slot.id)

    assert captured["list_unsubscribe_one_click"] is False
    assert captured["list_unsubscribe_url"]
