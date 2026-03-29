"""Tests for Beacon ingest (signature + tracking_events integration)."""
from __future__ import annotations

import hashlib
import hmac

import pytest
from sqlalchemy import select

from app.models import EmailOpen
from app.routers.beacon_ingest import _verify_signature
from app.tracking_events import record_email_open
from tests.conftest import make_inbox, make_campaign, make_lead, make_campaign_lead, make_email_log


async def _noop_webhook(*args, **kwargs):
    pass


def test_verify_signature_accepts_hmac_hex():
    body = b'{"inbox_id":1,"kind":"open"}'
    secret = "my-shared-secret-key-1234567890"
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    assert _verify_signature(body, secret, f"sha256={digest}")
    assert not _verify_signature(body, secret, "sha256=deadbeef")
    assert not _verify_signature(body, secret, "invalid")


@pytest.mark.asyncio
async def test_record_email_open_matches_beacon_payload(session, monkeypatch):
    monkeypatch.setattr("app.tracking_events.fire_webhook_event", _noop_webhook)

    inbox = await make_inbox(session)
    campaign = await make_campaign(session)
    lead = await make_lead(session)
    await make_campaign_lead(session, campaign.id, lead.id)
    email_log = await make_email_log(session, lead.id, campaign.id, inbox_id=inbox.id)
    token = email_log.open_token or str(email_log.id)
    await session.commit()

    recorded = await record_email_open(session, token, "9.9.9.9")
    assert recorded is True
    res = await session.execute(select(EmailOpen).where(EmailOpen.email_log_id == email_log.id))
    assert res.scalar_one_or_none() is not None
