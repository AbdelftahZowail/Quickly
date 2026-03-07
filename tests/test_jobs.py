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
from app.sender import SendResult
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


@pytest.mark.asyncio
async def test_daily_limit_prevents_extra_sends_and_fires_webhook(session, monkeypatch):
    inbox = await make_inbox(session, max_emails_per_day=1)
    campaign = await make_campaign(session)
    seq = await make_sequence(session, campaign.id)
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

    monkeypatch.setattr("app.jobs.maybe_fire_email_event", fake_webhook)
    monkeypatch.setattr("app.jobs.send_email", lambda **kwargs: SendResult(message_id="<x>"))

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
async def test_rate_limit_triggers_webhook_and_skips_send(session, monkeypatch):
    inbox = await make_inbox(session, wait_minutes_between=60)
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

    monkeypatch.setattr("app.jobs.maybe_fire_email_event", fake_webhook)
    monkeypatch.setattr("app.jobs.send_email", lambda **kwargs: SendResult(message_id="<x>"))

    await run_send_job()

    # no new email logs should have been created
    res = await session.execute(select(func.count(EmailLog.id)).where(EmailLog.inbox_id == inbox.id))
    assert res.scalar() == 1

    assert any(ev[0] == "rate_limit" for ev in events)
    assert any(ev[0] == "rate_limit" and ev[1].get("inbox_id") == inbox.id for ev in events)

    from app.models import QueueSlot
    res2 = await session.execute(select(func.count(QueueSlot.id)).where(QueueSlot.inbox_id == inbox.id))
    assert res2.scalar() == 1


@pytest.mark.asyncio
async def test_rate_limit_allows_small_slack(session, monkeypatch):
    """A send less than one second inside the wait period should still go
    through thanks to the small wiggle-room we grant."""
    inbox = await make_inbox(session, wait_minutes_between=5)
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

    monkeypatch.setattr("app.jobs.maybe_fire_email_event", fake_webhook)
    monkeypatch.setattr("app.jobs.send_email", lambda **kwargs: SendResult(message_id="<x>"))

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
    campaign = await make_campaign(session, track_opens=True)
    # force the "text_forced_tracking_disabled" override which is long
    campaign.send_all_as_text = True
    await session.flush()

    seq = await make_sequence(session, campaign.id)
    lead = await make_lead(session)
    cl = await make_campaign_lead(session, campaign.id, lead.id)
    await make_campaign_inbox(session, campaign.id, inbox.id)

    now = datetime.utcnow()
    await make_queue_slot(session, cl.id, inbox.id, scheduled_date=now - timedelta(minutes=1))
    await session.flush()

    monkeypatch.setattr("app.jobs.send_email", lambda **kwargs: SendResult(message_id="<x>"))

    await run_send_job()

    # row should exist with the full override string
    res = await session.execute(select(EmailLog.format_override))
    assert res.scalar() == "text_forced_tracking_disabled"

@pytest.mark.asyncio
async def test_gmail_token_refresh_failure_fires_webhook(session, monkeypatch):
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

    monkeypatch.setattr("app.jobs.maybe_fire_email_event", fake_webhook)

    # simulate refresh failure
    monkeypatch.setattr("app.jobs.refresh_access_token", lambda *args, **kwargs: False)
    import app.jobs as jobs_mod
    class _Ctx:
        def __init__(self, s):
            self.s = s
        async def __aenter__(self):
            return self.s
        async def __aexit__(self, exc_type, exc, tb):
            pass
    monkeypatch.setattr(jobs_mod, "AsyncSessionLocal", lambda: _Ctx(session))

    await run_send_job()

    # no email should be sent when token refresh fails
    res = await session.execute(select(func.count(EmailLog.id)).where(EmailLog.inbox_id == inbox.id))
    assert res.scalar() == 0

    assert any(ev[0] == "token_expired" for ev in events), "webhook not called for token_expired"
    from app.models import QueueSlot
    res2 = await session.execute(select(func.count(QueueSlot.id)).where(QueueSlot.inbox_id == inbox.id))
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
