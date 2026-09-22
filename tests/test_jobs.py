import pytest
import os
from app.database import engine

# SQLite with aiosqlite exhibits threading issues during flush/commit that
# lead to "no such table" errors in the worker thread.  These tests rely on
# the database and the send job; they are skipped when the dialect is
# sqlite so that developers using the default in-memory URL are not blocked.
pytestmark = pytest.mark.skipif(
    engine.dialect.name == "sqlite",
    reason="SQLite aiosqlite backend cannot reliably run these integration tests",
)
from datetime import datetime, timedelta
from sqlalchemy import select, func

from app.jobs import run_send_job
from app.sender import SendFailure, SendResult
from app.models import Inbox, EmailLog, GmailAccount
from app.unibox import GmailAPIError
from tests.conftest import (
    make_inbox,
    make_campaign,
    make_sequence,
    make_lead,
    make_campaign_lead,
    make_campaign_inbox,
    make_queue_slot,
    make_email_log,
)


class _SessionCtx:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return None


async def _attach_gmail_account(session, inbox):
    """run_send_job skips inboxes that have no provider credentials."""
    session.add(GmailAccount(
        inbox_id=inbox.id,
        google_email=inbox.email,
        access_token="token",
        refresh_token="refresh",
    ))
    await session.flush()


@pytest.mark.asyncio
async def test_daily_limit_prevents_extra_sends_and_fires_webhook(session, monkeypatch):
    inbox = await make_inbox(session, max_emails_per_day=1)
    await _attach_gmail_account(session, inbox)
    campaign = await make_campaign(session)
    seq = await make_sequence(session, campaign.id)
    # the second slot joins sequence position 1, so it needs its own sequence row
    await make_sequence(session, campaign.id, position=1)
    lead = await make_lead(session)
    cl = await make_campaign_lead(session, campaign.id, lead.id)
    await make_campaign_inbox(session, campaign.id, inbox.id)

    now = datetime.utcnow()
    # two slots scheduled in the past so they would be due (use distinct sequence_index
    # values; the test is agnostic to index semantics as long as two rows exist)
    await make_queue_slot(session, cl.id, inbox.id, sequence_index=0, scheduled_date=now - timedelta(hours=1))
    await make_queue_slot(session, cl.id, inbox.id, sequence_index=1, scheduled_date=now - timedelta(minutes=30), position_in_day=2)
    await session.flush()

    events = []
    async def fake_webhook(db, event, data):
        events.append((event, data))

    monkeypatch.setattr("app.jobs.fire_webhook_event", fake_webhook)
    monkeypatch.setattr("app.jobs.send_email", lambda **kwargs: SendResult(message_id="<x>"))
    monkeypatch.setattr("app.jobs.AsyncSessionLocal", lambda: _SessionCtx(session))

    await run_send_job()

    # only one email should have been logged
    res = await session.execute(select(func.count(EmailLog.id)).where(EmailLog.inbox_id == inbox.id))
    assert res.scalar() == 1

    # webhook should have been called to indicate the daily limit was hit
    assert any(ev[0] == "daily_limit" for ev in events)
    assert any(ev[0] == "daily_limit" and ev[1].get("inbox_id") == inbox.id for ev in events)

    # the second slot should still be in the queue (unsent)
    from app.models import QueueSlot
    res2 = await session.execute(select(func.count(QueueSlot.id)).where(QueueSlot.inbox_id == inbox.id))
    assert res2.scalar() == 1


@pytest.mark.asyncio
async def test_daily_limit_does_not_fire_without_due_queue_rows(session, monkeypatch):
    inbox = await make_inbox(session, max_emails_per_day=1)
    campaign = await make_campaign(session, sending_hours_start="00:00", sending_hours_end="23:59")
    lead = await make_lead(session)
    await make_campaign_lead(session, campaign.id, lead.id)
    await make_campaign_inbox(session, campaign.id, inbox.id)
    await make_email_log(session, lead.id, campaign.id, inbox_id=inbox.id, sent_at=datetime.utcnow())
    await session.flush()

    events = []

    async def fake_webhook(db, event, data):
        events.append((event, data))

    monkeypatch.setattr("app.jobs.fire_webhook_event", fake_webhook)
    monkeypatch.setattr("app.jobs.AsyncSessionLocal", lambda: _SessionCtx(session))

    await run_send_job()

    assert not any(ev[0] == "daily_limit" and ev[1].get("inbox_id") == inbox.id for ev in events)


@pytest.mark.asyncio
async def test_rate_limit_triggers_webhook_and_skips_send(session, monkeypatch):
    inbox = await make_inbox(session, wait_minutes_between=60)
    await _attach_gmail_account(session, inbox)
    campaign = await make_campaign(session)
    seq = await make_sequence(session, campaign.id)
    lead = await make_lead(session)
    cl = await make_campaign_lead(session, campaign.id, lead.id)
    await make_campaign_inbox(session, campaign.id, inbox.id)

    now = datetime.utcnow()
    # create an email log less than wait_minutes_between ago
    await make_email_log(session, lead.id, campaign.id, inbox_id=inbox.id, sent_at=now - timedelta(minutes=30))
    await make_queue_slot(session, cl.id, inbox.id, scheduled_date=now - timedelta(minutes=1))
    await session.flush()

    events = []
    async def fake_webhook(db, event, data):
        events.append((event, data))

    monkeypatch.setattr("app.jobs.fire_webhook_event", fake_webhook)
    monkeypatch.setattr("app.jobs.send_email", lambda **kwargs: SendResult(message_id="<x>"))
    monkeypatch.setattr("app.jobs.AsyncSessionLocal", lambda: _SessionCtx(session))

    await run_send_job()

    # no new email logs should have been created
    res = await session.execute(select(func.count(EmailLog.id)).where(EmailLog.inbox_id == inbox.id))
    assert res.scalar() == 1

    assert any(ev[0] == "rate_limit" for ev in events)
    assert any(ev[0] == "rate_limit" and ev[1].get("inbox_id") == inbox.id for ev in events)

    # The rate-limited slot is not sent; recalculation may reschedule it to a
    # later time, so assert on delivery rather than the slot row.
    res2 = await session.execute(select(func.count(EmailLog.id)).where(EmailLog.inbox_id == inbox.id))
    assert res2.scalar() == 1


@pytest.mark.asyncio
async def test_rate_limit_allows_small_slack(session, monkeypatch):
    """A send less than one second inside the wait period should still go
    through thanks to the small wiggle-room we grant."""
    inbox = await make_inbox(session, wait_minutes_between=5)
    await _attach_gmail_account(session, inbox)
    campaign = await make_campaign(session)
    seq = await make_sequence(session, campaign.id)
    lead = await make_lead(session)
    cl = await make_campaign_lead(session, campaign.id, lead.id)
    await make_campaign_inbox(session, campaign.id, inbox.id)

    now = datetime.utcnow()
    # last_sent occurs 4m59.5s ago (0.5s inside the 5-minute window)
    last_sent = now - timedelta(minutes=5) + timedelta(seconds=0.5)
    await make_email_log(session, lead.id, campaign.id, inbox_id=inbox.id, sent_at=last_sent)
    await make_queue_slot(session, cl.id, inbox.id, scheduled_date=now - timedelta(seconds=1))
    await session.flush()

    events = []
    async def fake_webhook(db, event, data):
        events.append((event, data))

    monkeypatch.setattr("app.jobs.fire_webhook_event", fake_webhook)
    monkeypatch.setattr("app.jobs.send_email", lambda **kwargs: SendResult(message_id="<x>"))
    monkeypatch.setattr("app.jobs.AsyncSessionLocal", lambda: _SessionCtx(session))

    await run_send_job()

    # a second log should have been added
    res = await session.execute(select(func.count(EmailLog.id)).where(EmailLog.inbox_id == inbox.id))
    assert res.scalar() == 2
    assert not any(ev[0] == "rate_limit" for ev in events)
    from app.models import QueueSlot
    res2 = await session.execute(select(func.count(QueueSlot.id)).where(QueueSlot.inbox_id == inbox.id))
    assert res2.scalar() == 0


@pytest.mark.asyncio
async def test_format_override_allows_long_values(session, monkeypatch):
    """Ensure the format_override column can hold more than 8 characters.

    Historically the database migration created the column as varchar(8),
    which caused a truncation error when longer reasons like
    ``tracking_upgraded_to_html`` were stored.  The send job should still
    work and preserve the full string.
    """
    inbox = await make_inbox(session)
    await _attach_gmail_account(session, inbox)
    campaign = await make_campaign(session)
    # force the "text_forced_tracking_disabled" override which is long
    campaign.send_all_as_text = True
    campaign.track_opens = True
    await session.flush()

    seq = await make_sequence(session, campaign.id)
    lead = await make_lead(session)
    cl = await make_campaign_lead(session, campaign.id, lead.id)
    await make_campaign_inbox(session, campaign.id, inbox.id)

    now = datetime.utcnow()
    await make_queue_slot(session, cl.id, inbox.id, scheduled_date=now - timedelta(minutes=1))
    await session.flush()

    monkeypatch.setattr("app.jobs.send_email", lambda **kwargs: SendResult(message_id="<x>"))
    monkeypatch.setattr("app.jobs.AsyncSessionLocal", lambda: _SessionCtx(session))

    await run_send_job()

    # row should exist with the full override string
    res = await session.execute(select(EmailLog.format_override))
    assert res.scalar() == "text_forced_tracking_disabled"

@pytest.mark.asyncio
async def test_gmail_auth_failure_pauses_inbox_and_fires_webhook(session, monkeypatch):
    """A permanent auth failure must pause the inbox (no infinite retries) and
    fire a correctly-labelled token_expired event."""
    inbox = await make_inbox(session, provider="gmail")
    campaign = await make_campaign(session)
    seq = await make_sequence(session, campaign.id)
    lead = await make_lead(session)
    cl = await make_campaign_lead(session, campaign.id, lead.id)
    await make_campaign_inbox(session, campaign.id, inbox.id)

    # attach a GmailAccount with an expired token
    ga = GmailAccount(inbox_id=inbox.id, google_email=inbox.email,
                      access_token="foo", refresh_token="bar",
                      token_expiry=datetime.utcnow() - timedelta(days=1))
    session.add(ga)
    await session.flush()

    now = datetime.utcnow()
    await make_queue_slot(session, cl.id, inbox.id, scheduled_date=now - timedelta(minutes=1))
    await session.flush()

    events = []
    async def fake_webhook(db, event, data):
        events.append((event, data))

    monkeypatch.setattr("app.jobs.fire_webhook_event", fake_webhook)
    # Token refresh now happens inside send_email; a failed refresh surfaces as
    # a permanent SendFailure, which is what the job must react to.
    monkeypatch.setattr("app.jobs.send_email", lambda **kwargs: SendFailure(
        error_type="auth_failed", message="Gmail auth/permission error (401)"))

    import app.jobs as jobs_mod
    monkeypatch.setattr(jobs_mod, "AsyncSessionLocal", lambda: _SessionCtx(session))
    jobs_mod._inbox_auth_cooldown_until.clear()

    await run_send_job()

    # no email should be sent and the pre-created log must be removed
    res = await session.execute(select(func.count(EmailLog.id)).where(EmailLog.inbox_id == inbox.id))
    assert res.scalar() == 0

    # the inbox is paused so subsequent scans do not retry the broken credential
    await session.refresh(inbox)
    assert inbox.paused is True

    ev = next((e for e in events if e[0] == "token_expired"), None)
    assert ev is not None, "webhook not called for token_expired"
    assert ev[1]["provider"] == "gmail"
    assert ev[1]["error_type"] == "auth_failed"
    assert ev[1]["inbox_email"] == inbox.email

    from app.models import QueueSlot
    res2 = await session.execute(select(func.count(QueueSlot.id)).where(QueueSlot.inbox_id == inbox.id))
    # the slot is retained so sending resumes once credentials are fixed
    assert res2.scalar() == 1


@pytest.mark.asyncio
async def test_unibox_sync_failure_triggers_webhook(session, monkeypatch):
    # an expired gmail token during a sync should fire the same webhook event
    inbox = await make_inbox(session, provider="gmail")
    ga = GmailAccount(inbox_id=inbox.id, google_email=inbox.email,
                      access_token="foo", refresh_token="bar",
                      token_expiry=datetime.utcnow() - timedelta(days=1))
    session.add(ga)
    await session.flush()
    await session.commit()

    events = []
    async def fake_webhook(db, event, data):
        events.append((event, data))

    monkeypatch.setattr("app.unibox.maybe_fire_email_event", fake_webhook)
    monkeypatch.setattr("app.unibox.refresh_access_token", lambda *args, **kwargs: False)

    from app.unibox import sync_single_inbox
    success = await sync_single_inbox(inbox.id)
    assert not success
    assert any(ev[0] == "token_expired" for ev in events)


@pytest.mark.asyncio
async def test_unibox_sync_skips_not_found_messages(session, monkeypatch):
    """A 404 from Gmail during message fetch should be ignored and not abort sync."""
    inbox = await make_inbox(session, provider="gmail")
    ga = GmailAccount(
        inbox_id=inbox.id,
        google_email=inbox.email,
        access_token="token",
        refresh_token="refresh",
    )
    session.add(ga)
    await session.flush()
    await session.commit()

    # simulate a normal profile/history response so that the sync enters full-sync path
    monkeypatch.setattr("app.unibox._gmail_get_profile", lambda token: {"historyId": "h1"})
    monkeypatch.setattr(
        "app.unibox._gmail_list_message_ids_in_window",
        lambda access_token, *, start_dt, end_dt, max_messages=None: ["msg-1"],
    )

    def fake_get_message(access_token: str, message_id: str, *, payload_format: str = "full"):
        raise GmailAPIError(404, "not found")

    monkeypatch.setattr("app.unibox._gmail_get_message", fake_get_message)

    from app.unibox import sync_single_inbox
    success = await sync_single_inbox(inbox.id, reason="manual")
    assert success, "sync should return True even when a message is missing"

    # no messages should have been inserted
    from sqlalchemy import select, func
    from app.models import GmailMessage
    res = await session.execute(select(func.count()).select_from(GmailMessage).where(GmailMessage.inbox_id == inbox.id))
    assert res.scalar() == 0
