"""End-to-end API tests for custom_sequence_mode campaign setting.

These tests exercise the full stack from campaign → sequence → leads → custom-email → queue,
verifying that ASAP vs wait_for_all modes behave correctly end-to-end.
"""

import pytest
from fastapi import BackgroundTasks

from app.routers.campaigns import (
    bulk_add_leads_to_campaign,
    write_custom_email,
    reconcile_personalized_status,
)
from app.routers.schedule import recalculate_all_campaigns
from app.schemas import CampaignLeadAdd, CustomEmailWrite
from app.models import CampaignLead, QueueSlot
from sqlalchemy import select


@pytest.mark.asyncio
async def test_e2e_asap_mode_lead_active_and_queue_created(session):
    """Full flow: ASAP campaign → personalized sequence → lead added → active status → custom email written → queue slot exists."""
    from tests.conftest import make_inbox, make_campaign, make_campaign_inbox, make_sequence, make_lead

    inbox = await make_inbox(session, email="e2e-asap@test.com")
    campaign = await make_campaign(session, custom_sequence_mode="asap")
    await make_campaign_inbox(session, campaign.id, inbox.id)
    seq = await make_sequence(session, campaign.id, position=0, body="Hi", sequence_type="personalized", fallback_subject="Hello", fallback_body="Hi there")
    lead = await make_lead(session, email="asap-lead@example.com")
    await session.commit()

    # 1. Verify campaign is ASAP
    assert campaign.custom_sequence_mode == "asap"

    # 2. Add lead via bulk (simulating API call) — let it create the enrollment
    result = await bulk_add_leads_to_campaign(
        campaign.id,
        [CampaignLeadAdd(email="asap-lead@example.com", name="ASAP Lead")],
        db=session,
    )
    assert result["added"] == 1

    # 3. Verify lead is active (not needs_custom_email) in ASAP mode
    cl_result = await session.execute(
        select(CampaignLead).where(CampaignLead.campaign_id == campaign.id)
    )
    cl = cl_result.scalar_one()
    assert cl.enrollment_status == "active"

    # 4. Write custom email
    bg = BackgroundTasks()
    write_result = await write_custom_email(
        campaign.id, lead.id, seq.id,
        CustomEmailWrite(subject="Custom Subject", body="Custom body", is_html=False),
        background_tasks=bg, db=session,
    )
    # Already active, so no transition
    assert write_result["lead_transitioned_to_active"] is False

    # 5. Trigger recalc
    await recalculate_all_campaigns(session)

    # 6. Verify queue slot was created
    slots_result = await session.execute(
        select(QueueSlot)
        .join(CampaignLead, QueueSlot.campaign_lead_id == CampaignLead.id)
        .where(CampaignLead.campaign_id == campaign.id)
    )
    slots = slots_result.scalars().all()
    assert len(slots) >= 1, "Expected at least one queue slot after recalc in ASAP mode"


@pytest.mark.asyncio
async def test_e2e_wait_for_all_blocks_until_all_written(session):
    """Full flow: wait_for_all campaign → personalized sequence → lead added → needs_custom_email → no queue until all written."""
    from tests.conftest import make_inbox, make_campaign, make_campaign_inbox, make_sequence, make_lead

    inbox = await make_inbox(session, email="e2e-wait@test.com")
    campaign = await make_campaign(session, custom_sequence_mode="wait_for_all")
    await make_campaign_inbox(session, campaign.id, inbox.id)
    seq = await make_sequence(session, campaign.id, position=0, body="Hi", sequence_type="personalized", fallback_subject="Hello", fallback_body="Hi there")
    lead = await make_lead(session, email="wait-lead@example.com")
    await session.commit()

    # 1. Verify campaign is wait_for_all
    assert campaign.custom_sequence_mode == "wait_for_all"

    # 2. Add lead via bulk — let it create the enrollment
    result = await bulk_add_leads_to_campaign(
        campaign.id,
        [CampaignLeadAdd(email="wait-lead@example.com", name="Wait Lead")],
        db=session,
    )
    assert result["added"] == 1

    # 3. Verify lead is needs_custom_email in wait_for_all mode
    cl_result = await session.execute(
        select(CampaignLead).where(CampaignLead.campaign_id == campaign.id)
    )
    cl = cl_result.scalar_one()
    assert cl.enrollment_status == "needs_custom_email"

    # 4. Trigger recalc — should NOT create queue slots
    await recalculate_all_campaigns(session)
    slots_result = await session.execute(
        select(QueueSlot)
        .join(CampaignLead, QueueSlot.campaign_lead_id == CampaignLead.id)
        .where(CampaignLead.campaign_id == campaign.id)
    )
    slots = slots_result.scalars().all()
    assert len(slots) == 0, "No queue slots should exist while lead is needs_custom_email"

    # 5. Write custom email
    bg = BackgroundTasks()
    write_result = await write_custom_email(
        campaign.id, lead.id, seq.id,
        CustomEmailWrite(subject="Custom Subject", body="Custom body", is_html=False),
        background_tasks=bg, db=session,
    )
    assert write_result["lead_transitioned_to_active"] is True

    # 6. Trigger recalc again — now queue slot should be created
    await recalculate_all_campaigns(session)
    slots_result = await session.execute(
        select(QueueSlot)
        .join(CampaignLead, QueueSlot.campaign_lead_id == CampaignLead.id)
        .where(CampaignLead.campaign_id == campaign.id)
    )
    slots = slots_result.scalars().all()
    assert len(slots) >= 1, "Queue slot should exist after lead transitions to active"


@pytest.mark.asyncio
async def test_e2e_switch_campaign_mode_from_wait_to_asap(session):
    """Switching an existing campaign from wait_for_all to asap should unblock leads."""
    from tests.conftest import make_inbox, make_campaign, make_campaign_inbox, make_sequence, make_lead

    inbox = await make_inbox(session, email="e2e-switch@test.com")
    campaign = await make_campaign(session, custom_sequence_mode="wait_for_all")
    await make_campaign_inbox(session, campaign.id, inbox.id)
    await make_sequence(session, campaign.id, position=0, body="Hi", sequence_type="personalized", fallback_subject="Hello", fallback_body="Hi there")
    lead = await make_lead(session, email="switch-lead@example.com")
    await session.commit()

    # Add lead via bulk in wait_for_all mode
    result = await bulk_add_leads_to_campaign(
        campaign.id,
        [CampaignLeadAdd(email="switch-lead@example.com", name="Switch Lead")],
        db=session,
    )
    assert result["added"] == 1

    cl_result = await session.execute(
        select(CampaignLead).where(CampaignLead.campaign_id == campaign.id)
    )
    cl = cl_result.scalar_one()
    assert cl.enrollment_status == "needs_custom_email"

    # Switch campaign to ASAP
    campaign.custom_sequence_mode = "asap"
    await session.commit()

    # Trigger reconcile
    bg = BackgroundTasks()
    await reconcile_personalized_status(session, campaign.id, bg)

    # Verify lead is now active
    await session.refresh(cl)
    assert cl.enrollment_status == "active"
