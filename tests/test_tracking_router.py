"""Integration tests for app.routers.tracking — pixel, click, unsubscribe, caddy/ask.

All router functions are called directly (no HTTP layer) so the `session`
fixture from conftest is sufficient.  `fire_webhook_event` is monkeypatched
to avoid real HTTP calls.
"""
from __future__ import annotations

from types import SimpleNamespace
from datetime import datetime

import pytest
from sqlalchemy import select, func

from app.models import (
    EmailLog, EmailOpen, EmailClick,
    TrackedLink, Lead, Campaign, CampaignLead, CampaignInbox,
    Inbox, QueueSlot, LeadUnsubscribeToken,
)
from app.routers.tracking import (
    open_pixel, click_redirect, unsubscribe, caddy_ask, tracking_probe,
)
from tests.conftest import (
    make_inbox, make_campaign, make_sequence, make_lead,
    make_campaign_lead, make_campaign_inbox, make_queue_slot, make_email_log,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _req(x_real_ip: str | None = None, x_forwarded_for: str | None = None):
    """Build a minimal fake Starlette Request-like object for the router functions."""
    raw_headers: dict[str, str] = {}
    if x_real_ip:
        raw_headers["X-Real-IP"] = x_real_ip
    if x_forwarded_for:
        raw_headers["X-Forwarded-For"] = x_forwarded_for

    class _FakeHeaders:
        def get(self, key: str, default=None):
            return raw_headers.get(key, default)

    return SimpleNamespace(headers=_FakeHeaders(), client=None)


async def _noop_webhook(*args, **kwargs):
    """Drop-in replacement that discards webhook calls."""


# ---------------------------------------------------------------------------
# GET /o/{log_id} — open-tracking pixel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_pixel_returns_gif(session, monkeypatch):
    """Endpoint always returns a GIF regardless of whether the log exists."""
    monkeypatch.setattr("app.routers.tracking.fire_webhook_event", _noop_webhook)

    resp = await open_pixel(token="9999", request=_req(), db=session)

    assert resp.media_type == "image/gif"
    assert resp.body[:4] == b"GIF8"  # GIF89a header


@pytest.mark.asyncio
async def test_open_pixel_no_cache_header(session, monkeypatch):
    monkeypatch.setattr("app.routers.tracking.fire_webhook_event", _noop_webhook)

    resp = await open_pixel(token="9999", request=_req(), db=session)

    assert "no-store" in resp.headers.get("cache-control", "").lower()


@pytest.mark.asyncio
async def test_open_pixel_records_email_open(session, monkeypatch):
    monkeypatch.setattr("app.routers.tracking.fire_webhook_event", _noop_webhook)

    inbox = await make_inbox(session)
    campaign = await make_campaign(session)
    lead = await make_lead(session)
    cl = await make_campaign_lead(session, campaign.id, lead.id)
    email_log = await make_email_log(session, lead.id, campaign.id, inbox_id=inbox.id)

    await open_pixel(token=email_log.open_token or str(email_log.id), request=_req(x_real_ip="1.2.3.4"), db=session)

    result = await session.execute(
        select(func.count(EmailOpen.id)).where(EmailOpen.email_log_id == email_log.id)
    )
    assert result.scalar() == 1


@pytest.mark.asyncio
async def test_open_pixel_stores_ip_address(session, monkeypatch):
    monkeypatch.setattr("app.routers.tracking.fire_webhook_event", _noop_webhook)

    inbox = await make_inbox(session)
    campaign = await make_campaign(session)
    lead = await make_lead(session)
    await make_campaign_lead(session, campaign.id, lead.id)
    email_log = await make_email_log(session, lead.id, campaign.id, inbox_id=inbox.id)

    await open_pixel(token=email_log.open_token or str(email_log.id), request=_req(x_real_ip="5.6.7.8"), db=session)

    result = await session.execute(
        select(EmailOpen).where(EmailOpen.email_log_id == email_log.id)
    )
    open_row = result.scalar_one()
    assert open_row.ip_address == "5.6.7.8"


@pytest.mark.asyncio
async def test_open_pixel_sets_opened_flag(session, monkeypatch):
    monkeypatch.setattr("app.routers.tracking.fire_webhook_event", _noop_webhook)

    inbox = await make_inbox(session)
    campaign = await make_campaign(session)
    lead = await make_lead(session)
    await make_campaign_lead(session, campaign.id, lead.id)
    email_log = await make_email_log(session, lead.id, campaign.id, inbox_id=inbox.id)
    assert not email_log.opened

    await open_pixel(token=email_log.open_token or str(email_log.id), request=_req(), db=session)

    await session.refresh(email_log)
    assert email_log.opened is True


@pytest.mark.asyncio
async def test_open_pixel_unknown_log_id_still_returns_gif(session, monkeypatch):
    """If the log row does not exist the pixel must still be served (avoids 500s)."""
    monkeypatch.setattr("app.routers.tracking.fire_webhook_event", _noop_webhook)

    resp = await open_pixel(token="99999", request=_req(), db=session)

    assert resp.media_type == "image/gif"


@pytest.mark.asyncio
async def test_open_pixel_fires_webhook(session, monkeypatch):
    events: list[str] = []

    async def capture(db, event_type, data):
        events.append(event_type)

    monkeypatch.setattr("app.routers.tracking.fire_webhook_event", capture)

    inbox = await make_inbox(session)
    campaign = await make_campaign(session)
    lead = await make_lead(session)
    await make_campaign_lead(session, campaign.id, lead.id)
    email_log = await make_email_log(session, lead.id, campaign.id, inbox_id=inbox.id)

    await open_pixel(token=email_log.open_token or str(email_log.id), request=_req(), db=session)

    assert "email.opened" in events


# ---------------------------------------------------------------------------
# GET /c/{token} — click redirect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_click_redirect_unknown_token_returns_404(session, monkeypatch):
    monkeypatch.setattr("app.routers.tracking.fire_webhook_event", _noop_webhook)

    resp = await click_redirect(token="nonexistent", request=_req(), db=session)

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_click_redirect_redirects_to_original_url(session, monkeypatch):
    monkeypatch.setattr("app.routers.tracking.fire_webhook_event", _noop_webhook)

    inbox = await make_inbox(session)
    campaign = await make_campaign(session)
    lead = await make_lead(session)
    await make_campaign_lead(session, campaign.id, lead.id)
    email_log = await make_email_log(session, lead.id, campaign.id, inbox_id=inbox.id)

    tracked = TrackedLink(
        email_log_id=email_log.id,
        token="test-token-123",
        original_url="https://destination.example.com/page",
    )
    session.add(tracked)
    await session.flush()

    resp = await click_redirect(token="test-token-123", request=_req(), db=session)

    assert resp.status_code == 302
    assert resp.headers["location"] == "https://destination.example.com/page"


@pytest.mark.asyncio
async def test_click_redirect_records_email_click(session, monkeypatch):
    monkeypatch.setattr("app.routers.tracking.fire_webhook_event", _noop_webhook)

    inbox = await make_inbox(session)
    campaign = await make_campaign(session)
    lead = await make_lead(session)
    await make_campaign_lead(session, campaign.id, lead.id)
    email_log = await make_email_log(session, lead.id, campaign.id, inbox_id=inbox.id)

    tracked = TrackedLink(
        email_log_id=email_log.id,
        token="click-token-abc",
        original_url="https://example.com",
    )
    session.add(tracked)
    await session.flush()

    await click_redirect(token="click-token-abc", request=_req(x_real_ip="9.9.9.9"), db=session)

    result = await session.execute(
        select(EmailClick).where(EmailClick.email_log_id == email_log.id)
    )
    click = result.scalar_one()
    assert click.ip_address == "9.9.9.9"


@pytest.mark.asyncio
async def test_click_redirect_sets_clicked_flag(session, monkeypatch):
    monkeypatch.setattr("app.routers.tracking.fire_webhook_event", _noop_webhook)

    inbox = await make_inbox(session)
    campaign = await make_campaign(session)
    lead = await make_lead(session)
    await make_campaign_lead(session, campaign.id, lead.id)
    email_log = await make_email_log(session, lead.id, campaign.id, inbox_id=inbox.id)
    assert not email_log.clicked

    tracked = TrackedLink(
        email_log_id=email_log.id,
        token="click-flag-token",
        original_url="https://example.com",
    )
    session.add(tracked)
    await session.flush()

    await click_redirect(token="click-flag-token", request=_req(), db=session)

    await session.refresh(email_log)
    assert email_log.clicked is True


@pytest.mark.asyncio
async def test_click_redirect_fires_webhook(session, monkeypatch):
    events: list[str] = []

    async def capture(db, event_type, data):
        events.append(event_type)

    monkeypatch.setattr("app.routers.tracking.fire_webhook_event", capture)

    inbox = await make_inbox(session)
    campaign = await make_campaign(session)
    lead = await make_lead(session)
    await make_campaign_lead(session, campaign.id, lead.id)
    email_log = await make_email_log(session, lead.id, campaign.id, inbox_id=inbox.id)

    tracked = TrackedLink(
        email_log_id=email_log.id, token="wh-click-token",
        original_url="https://example.com",
    )
    session.add(tracked)
    await session.flush()

    await click_redirect(token="wh-click-token", request=_req(), db=session)

    assert "email.clicked" in events


# ---------------------------------------------------------------------------
# GET /u/{token} — unsubscribe
# ---------------------------------------------------------------------------


async def _setup_unsubscribe(session):
    """Scaffold the minimum DB rows for unsubscribe tests. Returns (lead, campaign, cl, token_str)."""
    inbox = await make_inbox(session)
    campaign = await make_campaign(session)
    lead = await make_lead(session)
    cl = await make_campaign_lead(session, campaign.id, lead.id)

    token_str = "unsub-token-xyz"
    unsub = LeadUnsubscribeToken(
        lead_id=lead.id,
        campaign_id=campaign.id,
        token=token_str,
    )
    session.add(unsub)
    await session.flush()
    return lead, campaign, cl, token_str


@pytest.mark.asyncio
async def test_unsubscribe_invalid_token_returns_404(session, monkeypatch):
    monkeypatch.setattr("app.routers.tracking.fire_webhook_event", _noop_webhook)

    resp = await unsubscribe(token="no-such-token", db=session)

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_unsubscribe_marks_lead_status_unsubscribed(session, monkeypatch):
    monkeypatch.setattr("app.routers.tracking.fire_webhook_event", _noop_webhook)

    lead, campaign, cl, token_str = await _setup_unsubscribe(session)
    assert lead.status != "unsubscribed"

    await unsubscribe(token=token_str, db=session)

    await session.refresh(lead)
    assert lead.status == "unsubscribed"


@pytest.mark.asyncio
async def test_unsubscribe_deletes_queue_slots(session, monkeypatch):
    monkeypatch.setattr("app.routers.tracking.fire_webhook_event", _noop_webhook)

    lead, campaign, cl, token_str = await _setup_unsubscribe(session)

    # add a pending slot
    slot = QueueSlot(
        campaign_lead_id=cl.id,
        inbox_id=(await make_inbox(session, email="slot@test.com")).id,
        sequence_index=0,
        scheduled_date=datetime(2026, 4, 1, 9, 0),
        position_in_day=1,
    )
    session.add(slot)
    await session.flush()

    await unsubscribe(token=token_str, db=session)

    result = await session.execute(
        select(func.count(QueueSlot.id)).where(QueueSlot.campaign_lead_id == cl.id)
    )
    assert result.scalar() == 0


@pytest.mark.asyncio
async def test_unsubscribe_returns_html_page(session, monkeypatch):
    monkeypatch.setattr("app.routers.tracking.fire_webhook_event", _noop_webhook)

    _, _, _, token_str = await _setup_unsubscribe(session)

    resp = await unsubscribe(token=token_str, db=session)

    assert resp.status_code == 200
    assert "text/html" in resp.media_type
    assert b"unsubscribed" in resp.body.lower()


@pytest.mark.asyncio
async def test_unsubscribe_already_unsubscribed_shows_different_page(session, monkeypatch):
    monkeypatch.setattr("app.routers.tracking.fire_webhook_event", _noop_webhook)

    lead, campaign, cl, token_str = await _setup_unsubscribe(session)
    lead.status = "unsubscribed"
    await session.flush()

    resp = await unsubscribe(token=token_str, db=session)

    assert resp.status_code == 200
    assert b"already unsubscribed" in resp.body.lower()


@pytest.mark.asyncio
async def test_unsubscribe_fires_webhooks(session, monkeypatch):
    events: list[str] = []

    async def capture(db, event_type, data):
        events.append(event_type)

    monkeypatch.setattr("app.routers.tracking.fire_webhook_event", capture)

    _, _, _, token_str = await _setup_unsubscribe(session)

    await unsubscribe(token=token_str, db=session)

    assert "lead.unsubscribed" in events
    assert "lead.status_changed" in events


@pytest.mark.asyncio
async def test_unsubscribe_already_done_does_not_fire_webhooks(session, monkeypatch):
    events: list[str] = []

    async def capture(db, event_type, data):
        events.append(event_type)

    monkeypatch.setattr("app.routers.tracking.fire_webhook_event", capture)

    lead, _, _, token_str = await _setup_unsubscribe(session)
    lead.status = "unsubscribed"
    await session.flush()

    await unsubscribe(token=token_str, db=session)

    assert "lead.unsubscribed" not in events


# ---------------------------------------------------------------------------
# GET /api/caddy/ask — on_demand_tls gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_caddy_ask_approves_known_tracking_domain(session):
    inbox = await make_inbox(session)
    inbox.tracking_domain = "mail.client.com"
    await session.flush()

    resp = await caddy_ask(domain="mail.client.com", db=session)

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_caddy_ask_rejects_unknown_domain(session):
    resp = await caddy_ask(domain="unknown.example.com", db=session)

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_caddy_ask_rejects_bare_hostname(session):
    resp = await caddy_ask(domain="localhost", db=session)

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_caddy_ask_rejects_ipv4(session):
    resp = await caddy_ask(domain="1.2.3.4", db=session)

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_caddy_ask_rejects_ipv6(session):
    resp = await caddy_ask(domain="::1", db=session)

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_caddy_ask_rejects_ipv6_full(session):
    resp = await caddy_ask(domain="2001:0db8:85a3:0000:0000:8a2e:0370:7334", db=session)

    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/tracking-probe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tracking_probe_returns_ok():
    result = await tracking_probe()

    assert result == {"ok": True}
