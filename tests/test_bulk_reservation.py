import pytest
from sqlalchemy import select, func
from datetime import date

from app.queue_logic import reserve_slots_for_new_leads_bulk
from app.models import QueueSlot
from tests.conftest import make_inbox, make_campaign, make_sequence, make_campaign_inbox, make_lead, make_campaign_lead


async def test_reserve_slots_for_new_leads_bulk_creates_slots(session):
    inbox = await make_inbox(session, email="bulk-inbox@test.com")
    campaign = await make_campaign(session)
    # single sequence -> one slot per lead
    await make_sequence(session, campaign.id, position=0, body="Hello")
    await make_campaign_inbox(session, campaign.id, inbox.id, position=0)

    cl_ids = []
    for e in ("a1@example.com", "b2@example.com", "c3@example.com"):
        lead = await make_lead(session, email=e)
        cl = await make_campaign_lead(session, campaign.id, lead.id)
        cl_ids.append(cl.id)

    # Call the bulk reservation function
    await reserve_slots_for_new_leads_bulk(session, cl_ids, campaign.id, start_date=date(2026, 3, 2))
    await session.flush()

    # Each campaign_lead should now have one QueueSlot
    for cl_id in cl_ids:
        q = await session.execute(select(func.count(QueueSlot.id)).where(QueueSlot.campaign_lead_id == cl_id))
        assert q.scalar() == 1


async def test_reserve_slots_for_new_leads_bulk_handles_empty_list(session):
    # should be a no-op and not raise
    await reserve_slots_for_new_leads_bulk(session, [], 1)
    # nothing to assert beyond no exception


@pytest.mark.asyncio
async def test_reserve_slots_for_new_leads_bulk_skips_paused_campaign(session):
    inbox = await make_inbox(session, email="paused-camp@test.com")
    campaign = await make_campaign(session, paused=True)
    await make_sequence(session, campaign.id, position=0, body="Hello")
    await make_campaign_inbox(session, campaign.id, inbox.id, position=0)
    lead = await make_lead(session, email="puser@example.com")
    cl = await make_campaign_lead(session, campaign.id, lead.id)
    await session.flush()

    await reserve_slots_for_new_leads_bulk(session, [cl.id], campaign.id, start_date=date(2026, 3, 2))
    await session.flush()

    q = await session.execute(select(func.count(QueueSlot.id)).where(QueueSlot.campaign_lead_id == cl.id))
    assert q.scalar() == 0
