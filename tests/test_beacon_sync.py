"""Tests for rebuilding Beacon items from Quickly DB."""
from __future__ import annotations

import pytest

from app.beacon_sync import collect_inbox_beacon_items
from app.models import TrackedLink

from tests.conftest import (
    make_campaign,
    make_campaign_lead,
    make_email_log,
    make_inbox,
    make_lead,
    make_unsubscribe_token,
)


@pytest.mark.asyncio
async def test_collect_inbox_beacon_items_open_click_unsub(session):
    inbox = await make_inbox(session, email="sync@test.com")
    campaign = await make_campaign(session)
    lead = await make_lead(session)
    await make_campaign_lead(session, campaign.id, lead.id)
    await make_unsubscribe_token(session, lead.id, campaign.id, token="unsub-one")

    log = await make_email_log(session, lead.id, campaign.id, inbox_id=inbox.id)
    log.open_token = "open-abc"
    session.add(
        TrackedLink(email_log_id=log.id, token="click-xyz", original_url="https://example.com/p")
    )
    await session.flush()

    items = await collect_inbox_beacon_items(session, inbox.id)
    kinds = {(i["kind"], i.get("token")) for i in items}
    assert ("open", "open-abc") in kinds
    assert ("click", "click-xyz") in kinds
    assert ("unsubscribe", "unsub-one") in kinds
    click = next(i for i in items if i["kind"] == "click")
    assert click["original_url"] == "https://example.com/p"
    for i in items:
        assert i["inbox_id"] == inbox.id


@pytest.mark.asyncio
async def test_collect_dedupes_unsub_across_two_sends(session):
    inbox = await make_inbox(session, email="sync2@test.com")
    campaign = await make_campaign(session)
    lead = await make_lead(session)
    await make_campaign_lead(session, campaign.id, lead.id)
    await make_unsubscribe_token(session, lead.id, campaign.id, token="same-unsub")

    for _ in range(2):
        log = await make_email_log(session, lead.id, campaign.id, inbox_id=inbox.id)
        log.open_token = f"open-{log.id}"
        await session.flush()

    items = await collect_inbox_beacon_items(session, inbox.id)
    assert sum(1 for i in items if i["kind"] == "unsubscribe") == 1
    assert sum(1 for i in items if i["kind"] == "open") == 2
