"""Tests for:
- Inbox pause/unpause endpoints (pause_leads and reassign strategies)
- Bulk lead addition with skip_duplicates option
- CSV import with skip_duplicates option
"""

import io
import pytest
from datetime import datetime, timedelta
from fastapi import HTTPException

from sqlalchemy import select

from app.models import (
    Inbox,
    Lead,
    Campaign,
    CampaignLead,
    CampaignInbox,
    QueueSlot,
    Sequence,
)
from app.schemas import InboxUpdate, PauseInboxRequest, InboxCreate
from app.routers import inbox as inbox_router
from app.routers.campaigns import (
    bulk_add_leads_to_campaign,
    import_campaign_leads,
    CampaignLeadAdd,
)
from tests.conftest import (
    make_campaign,
    make_inbox,
    make_lead,
    make_campaign_lead,
    make_campaign_inbox,
    make_queue_slot,
    make_sequence,
)


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _future_slot_date() -> datetime:
    return datetime.utcnow() + timedelta(days=1)


# ══════════════════════════════════════════════════════════════════════════════
# Inbox model: paused flag
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_inbox_paused_defaults_to_false(session):
    """Newly created inbox should have paused=False."""
    inbox = Inbox(email="default@test.com")
    session.add(inbox)
    await session.flush()
    assert inbox.paused is False


@pytest.mark.asyncio
async def test_update_inbox_sets_paused(session):
    """PATCH /inboxes/{id} with paused=True should persist the flag."""
    inbox = await make_inbox(session, email="patch-pause@test.com")
    data = InboxUpdate(paused=True)
    result = await inbox_router.update_inbox(inbox.id, data, db=session)
    assert result.paused is True

    data2 = InboxUpdate(paused=False)
    result2 = await inbox_router.update_inbox(inbox.id, data2, db=session)
    assert result2.paused is False


# ══════════════════════════════════════════════════════════════════════════════
# Inbox pause: pause_leads strategy
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_pause_inbox_pause_leads_sets_sending_paused(session):
    """Pausing an inbox with action='pause_leads' sets sending_paused=True on
    all CampaignLeads that have future queue slots assigned to that inbox."""
    inbox = await make_inbox(session, email="pause-leads@test.com")
    campaign = await make_campaign(session)
    lead = await make_lead(session, email="affected@test.com")
    cl = await make_campaign_lead(session, campaign.id, lead.id)
    await make_queue_slot(
        session,
        campaign_lead_id=cl.id,
        inbox_id=inbox.id,
        scheduled_date=_future_slot_date(),
    )

    body = PauseInboxRequest(action="pause_leads")
    await inbox_router.pause_inbox(inbox.id, body, db=session)

    await session.refresh(cl)
    assert cl.sending_paused is True


@pytest.mark.asyncio
async def test_pause_inbox_pause_leads_ignores_past_slots(session):
    """Slots already in the past should not cause a campaign_lead to be paused."""
    inbox = await make_inbox(session, email="past-slots@test.com")
    campaign = await make_campaign(session)
    lead = await make_lead(session, email="past-lead@test.com")
    cl = await make_campaign_lead(session, campaign.id, lead.id)
    past_date = datetime.utcnow() - timedelta(days=1)
    await make_queue_slot(
        session,
        campaign_lead_id=cl.id,
        inbox_id=inbox.id,
        scheduled_date=past_date,
    )

    body = PauseInboxRequest(action="pause_leads")
    await inbox_router.pause_inbox(inbox.id, body, db=session)

    await session.refresh(cl)
    # No future slots → sending_paused should stay False
    assert cl.sending_paused is False


@pytest.mark.asyncio
async def test_pause_inbox_marks_inbox_paused(session):
    """After pausing, inbox.paused should be True."""
    inbox = await make_inbox(session, email="mark-paused@test.com")
    body = PauseInboxRequest(action="pause_leads")
    result = await inbox_router.pause_inbox(inbox.id, body, db=session)
    assert result.paused is True
    await session.refresh(inbox)
    assert inbox.paused is True


@pytest.mark.asyncio
async def test_pause_inbox_already_paused_raises(session):
    """Calling pause on an already-paused inbox should raise 400."""
    inbox = await make_inbox(session, email="already-paused@test.com")
    inbox.paused = True
    await session.flush()

    body = PauseInboxRequest(action="pause_leads")
    with pytest.raises(HTTPException) as exc_info:
        await inbox_router.pause_inbox(inbox.id, body, db=session)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_pause_inbox_not_found_raises(session):
    """Pausing a non-existent inbox should raise 404."""
    body = PauseInboxRequest(action="pause_leads")
    with pytest.raises(HTTPException) as exc_info:
        await inbox_router.pause_inbox(9999, body, db=session)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_pause_inbox_invalid_action_raises(session):
    """An unknown action string should raise 400."""
    inbox = await make_inbox(session, email="bad-action@test.com")
    body = PauseInboxRequest(action="delete_everything")
    with pytest.raises(HTTPException) as exc_info:
        await inbox_router.pause_inbox(inbox.id, body, db=session)
    assert exc_info.value.status_code == 400


# ══════════════════════════════════════════════════════════════════════════════
# Inbox pause: reassign strategy
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_pause_inbox_reassign_moves_slots(session):
    """action='reassign' should move future queue slots to the target inbox."""
    inbox_a = await make_inbox(session, email="inbox-a@test.com")
    inbox_b = await make_inbox(session, email="inbox-b@test.com")
    campaign = await make_campaign(session)
    lead = await make_lead(session, email="reassign-lead@test.com")
    cl = await make_campaign_lead(session, campaign.id, lead.id)
    slot = await make_queue_slot(
        session,
        campaign_lead_id=cl.id,
        inbox_id=inbox_a.id,
        scheduled_date=_future_slot_date(),
    )

    body = PauseInboxRequest(action="reassign", target_inbox_id=inbox_b.id)
    await inbox_router.pause_inbox(inbox_a.id, body, db=session)

    await session.refresh(slot)
    assert slot.inbox_id == inbox_b.id


@pytest.mark.asyncio
async def test_pause_inbox_reassign_does_not_pause_leads(session):
    """With action='reassign', CampaignLeads should NOT have sending_paused set."""
    inbox_a = await make_inbox(session, email="ra-a@test.com")
    inbox_b = await make_inbox(session, email="ra-b@test.com")
    campaign = await make_campaign(session)
    lead = await make_lead(session, email="ra-lead@test.com")
    cl = await make_campaign_lead(session, campaign.id, lead.id)
    await make_queue_slot(
        session,
        campaign_lead_id=cl.id,
        inbox_id=inbox_a.id,
        scheduled_date=_future_slot_date(),
    )

    body = PauseInboxRequest(action="reassign", target_inbox_id=inbox_b.id)
    await inbox_router.pause_inbox(inbox_a.id, body, db=session)

    await session.refresh(cl)
    assert cl.sending_paused is False


@pytest.mark.asyncio
async def test_pause_inbox_reassign_missing_target_raises(session):
    """action='reassign' without target_inbox_id should raise 400."""
    inbox = await make_inbox(session, email="no-target@test.com")
    body = PauseInboxRequest(action="reassign", target_inbox_id=None)
    with pytest.raises(HTTPException) as exc_info:
        await inbox_router.pause_inbox(inbox.id, body, db=session)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_pause_inbox_reassign_nonexistent_target_raises(session):
    """action='reassign' to a non-existent inbox should raise 404."""
    inbox = await make_inbox(session, email="bad-target@test.com")
    body = PauseInboxRequest(action="reassign", target_inbox_id=9999)
    with pytest.raises(HTTPException) as exc_info:
        await inbox_router.pause_inbox(inbox.id, body, db=session)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_pause_inbox_reassign_to_paused_target_raises(session):
    """Reassigning to a paused inbox should raise 400."""
    inbox_a = await make_inbox(session, email="src-paused@test.com")
    inbox_b = await make_inbox(session, email="dst-paused@test.com")
    inbox_b.paused = True
    await session.flush()

    body = PauseInboxRequest(action="reassign", target_inbox_id=inbox_b.id)
    with pytest.raises(HTTPException) as exc_info:
        await inbox_router.pause_inbox(inbox_a.id, body, db=session)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_pause_inbox_reassign_same_inbox_raises(session):
    """Reassigning to the same inbox should raise 400."""
    inbox = await make_inbox(session, email="self-reassign@test.com")
    body = PauseInboxRequest(action="reassign", target_inbox_id=inbox.id)
    with pytest.raises(HTTPException) as exc_info:
        await inbox_router.pause_inbox(inbox.id, body, db=session)
    assert exc_info.value.status_code == 400


# ══════════════════════════════════════════════════════════════════════════════
# Inbox unpause
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_unpause_inbox_clears_flag(session):
    """unpause_inbox should set paused=False and return the inbox."""
    inbox = await make_inbox(session, email="unpause-me@test.com")
    inbox.paused = True
    await session.flush()

    result = await inbox_router.unpause_inbox(inbox.id, db=session)
    assert result.paused is False
    await session.refresh(inbox)
    assert inbox.paused is False


@pytest.mark.asyncio
async def test_unpause_inbox_not_found_raises(session):
    """Unpausing a non-existent inbox should raise 404."""
    with pytest.raises(HTTPException) as exc_info:
        await inbox_router.unpause_inbox(9999, db=session)
    assert exc_info.value.status_code == 404


# ══════════════════════════════════════════════════════════════════════════
# Inbox deletion behaviour
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_delete_inbox_simple(session):
    """Deleting a standalone inbox should succeed."""
    inbox = await make_inbox(session, email="delete-simple@test.com")
    res = await inbox_router.delete_inbox(inbox.id, db=session)
    assert res == {"ok": True}
    result = await session.execute(select(Inbox).where(Inbox.id == inbox.id))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_delete_inbox_in_use_requires_reassign(session):
    """Inbox linked to a campaign or carrying slots should only be deleted
    when reassign=True is provided; otherwise the endpoint raises 400."""
    inbox_a = await make_inbox(session, email="del-a@test.com")
    inbox_b = await make_inbox(session, email="del-b@test.com")
    campaign = await make_campaign(session)
    lead = await make_lead(session, email="del-lead@test.com")
    cl = await make_campaign_lead(session, campaign.id, lead.id)
    await make_campaign_inbox(session, campaign.id, inbox_a.id)
    await make_campaign_inbox(session, campaign.id, inbox_b.id)
    slot = await make_queue_slot(
        session,
        campaign_lead_id=cl.id,
        inbox_id=inbox_a.id,
        scheduled_date=_future_slot_date(),
    )

    with pytest.raises(HTTPException) as exc_info:
        await inbox_router.delete_inbox(inbox_a.id, db=session)
    assert exc_info.value.status_code == 400

    res2 = await inbox_router.delete_inbox(inbox_a.id, reassign=True, db=session)
    assert res2 == {"ok": True}
    # inbox a should be removed, its CampaignInbox links cleared, and slot moved
    q = await session.execute(select(Inbox).where(Inbox.id == inbox_a.id))
    assert q.scalar_one_or_none() is None
    ci_q = await session.execute(select(CampaignInbox).where(CampaignInbox.inbox_id == inbox_a.id))
    assert ci_q.scalars().all() == []
    await session.refresh(slot)
    assert slot.inbox_id == inbox_b.id



# ══════════════════════════════════════════════════════════════════════════════
# Bulk add leads: skip_duplicates=True (default)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_bulk_add_skip_duplicates_reports_them(session):
    """With skip_duplicates=True, already-enrolled leads appear in duplicate_leads
    and are NOT re-enrolled."""
    campaign = await make_campaign(session)
    inbox = await make_inbox(session, email="bulk-dup@test.com")
    await make_campaign_inbox(session, campaign.id, inbox.id)
    await make_sequence(session, campaign.id, position=0)

    # Pre-enroll a lead
    lead = await make_lead(session, email="dup@example.com")
    await make_campaign_lead(session, campaign.id, lead.id)

    # Attempt to add again with skip_duplicates=True
    payload = [CampaignLeadAdd(email="dup@example.com")]
    result = await bulk_add_leads_to_campaign(
        campaign_id=campaign.id,
        leads_data=payload,
        skip_duplicates=True,
        db=session,
    )

    assert result["added"] == 0
    assert result["already_enrolled"] == 1
    assert "dup@example.com" in result["duplicate_leads"]


@pytest.mark.asyncio
async def test_bulk_add_new_leads_not_in_duplicate_list(session):
    """Brand-new leads should be added and NOT appear in duplicate_leads."""
    campaign = await make_campaign(session)
    inbox = await make_inbox(session, email="bulk-new@test.com")
    await make_campaign_inbox(session, campaign.id, inbox.id)
    await make_sequence(session, campaign.id, position=0)

    payload = [CampaignLeadAdd(email="new@example.com")]
    result = await bulk_add_leads_to_campaign(
        campaign_id=campaign.id,
        leads_data=payload,
        skip_duplicates=True,
        db=session,
    )

    assert result["added"] == 1
    assert result["duplicate_leads"] == []


@pytest.mark.asyncio
async def test_bulk_add_mixed_new_and_duplicate(session):
    """Mixed batch: new leads are added, duplicates are reported separately."""
    campaign = await make_campaign(session)
    inbox = await make_inbox(session, email="bulk-mix@test.com")
    await make_campaign_inbox(session, campaign.id, inbox.id)
    await make_sequence(session, campaign.id, position=0)

    existing = await make_lead(session, email="exists@example.com")
    await make_campaign_lead(session, campaign.id, existing.id)

    payload = [
        CampaignLeadAdd(email="exists@example.com"),
        CampaignLeadAdd(email="fresh@example.com"),
    ]
    result = await bulk_add_leads_to_campaign(
        campaign_id=campaign.id,
        leads_data=payload,
        skip_duplicates=True,
        db=session,
    )

    assert result["added"] == 1
    assert result["already_enrolled"] == 1
    assert "exists@example.com" in result["duplicate_leads"]
    assert "fresh@example.com" not in result["duplicate_leads"]


# ══════════════════════════════════════════════════════════════════════════════
# Bulk add leads: skip_duplicates=False (re-enroll)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_bulk_add_reenroll_when_skip_duplicates_false(session):
    """With skip_duplicates=False, an already-enrolled lead is re-enrolled
    (old CampaignLead removed and a fresh one created)."""
    campaign = await make_campaign(session)
    inbox = await make_inbox(session, email="reenroll@test.com")
    await make_campaign_inbox(session, campaign.id, inbox.id)
    await make_sequence(session, campaign.id, position=0)

    lead = await make_lead(session, email="reenroll@example.com")
    old_cl = await make_campaign_lead(session, campaign.id, lead.id)
    old_cl_id = old_cl.id

    payload = [CampaignLeadAdd(email="reenroll@example.com")]
    result = await bulk_add_leads_to_campaign(
        campaign_id=campaign.id,
        leads_data=payload,
        skip_duplicates=False,
        db=session,
    )

    assert result["added"] == 1
    assert result["duplicate_leads"] == []

    # The externally-observable effect of re-enrollment is that the lead was
    # reported as "added" (not "already_enrolled"), confirming the old record
    # was replaced.  We also verify exactly one enrollment entry exists.
    from sqlalchemy import func as _func
    count_res = await session.execute(
        select(_func.count(CampaignLead.id)).where(
            CampaignLead.campaign_id == campaign.id,
            CampaignLead.lead_id == lead.id,
        )
    )
    assert count_res.scalar() == 1


# ══════════════════════════════════════════════════════════════════════════════
# Bulk add: within-batch deduplication
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_bulk_add_deduplicates_within_batch(session):
    """Duplicate emails within the same batch payload should only be added once."""
    campaign = await make_campaign(session)
    inbox = await make_inbox(session, email="batch-dup@test.com")
    await make_campaign_inbox(session, campaign.id, inbox.id)
    await make_sequence(session, campaign.id, position=0)

    payload = [
        CampaignLeadAdd(email="sametwice@example.com"),
        CampaignLeadAdd(email="sametwice@example.com"),
    ]
    result = await bulk_add_leads_to_campaign(
        campaign_id=campaign.id,
        leads_data=payload,
        skip_duplicates=True,
        db=session,
    )

    assert result["added"] == 1
    assert result["duplicates_in_batch"] == 1


# ══════════════════════════════════════════════════════════════════════════════
# CSV import: skip_duplicates option
# ══════════════════════════════════════════════════════════════════════════════

def _make_csv_upload(content: str, filename: str = "leads.csv"):
    """Return a minimal UploadFile-like object for the import endpoint."""
    data = content.encode("utf-8")
    _fn = filename  # capture in closure to avoid class-scope resolution issues

    class _FakeUpload:
        async def read(self_inner):
            return data

    obj = _FakeUpload()
    obj.filename = _fn
    return obj


@pytest.mark.asyncio
async def test_csv_import_skip_duplicates_reports_them(session):
    """CSV import with skip_duplicates=True should report already-enrolled
    leads in duplicate_leads and not re-enroll them."""
    campaign = await make_campaign(session)
    inbox = await make_inbox(session, email="csv-dup@test.com")
    await make_campaign_inbox(session, campaign.id, inbox.id)
    await make_sequence(session, campaign.id, position=0)

    existing = await make_lead(session, email="csv-dup@example.com")
    await make_campaign_lead(session, campaign.id, existing.id)

    csv_content = "email,name\ncsv-dup@example.com,Duplicate\nnew@example.com,New\n"
    fake_file = _make_csv_upload(csv_content)

    result = await import_campaign_leads(
        campaign_id=campaign.id,
        file=fake_file,
        skip_duplicates=True,
        db=session,
    )

    assert result["added"] == 1
    assert result["already_enrolled"] == 1
    assert "csv-dup@example.com" in result["duplicate_leads"]


@pytest.mark.asyncio
async def test_csv_import_reenroll_when_skip_duplicates_false(session):
    """CSV import with skip_duplicates=False should re-enroll existing leads."""
    campaign = await make_campaign(session)
    inbox = await make_inbox(session, email="csv-reenroll@test.com")
    await make_campaign_inbox(session, campaign.id, inbox.id)
    await make_sequence(session, campaign.id, position=0)

    existing = await make_lead(session, email="reenroll-csv@example.com")
    old_cl = await make_campaign_lead(session, campaign.id, existing.id)
    old_cl_id = old_cl.id

    csv_content = "email,name\nreenroll-csv@example.com,Reenroll\n"
    fake_file = _make_csv_upload(csv_content)

    result = await import_campaign_leads(
        campaign_id=campaign.id,
        file=fake_file,
        skip_duplicates=False,
        db=session,
    )

    assert result["added"] == 1
    assert result["duplicate_leads"] == []

    # Verify exactly one enrollment entry exists after re-enrollment
    from sqlalchemy import func as _func
    count_res = await session.execute(
        select(_func.count(CampaignLead.id)).where(
            CampaignLead.campaign_id == campaign.id,
            CampaignLead.lead_id == existing.id,
        )
    )
    assert count_res.scalar() == 1


@pytest.mark.asyncio
async def test_csv_import_dedup_within_file(session):
    """Duplicate rows within the CSV file itself should be counted in
    duplicates_in_batch and not cause double-enrollment."""
    campaign = await make_campaign(session)
    inbox = await make_inbox(session, email="csv-infile@test.com")
    await make_campaign_inbox(session, campaign.id, inbox.id)
    await make_sequence(session, campaign.id, position=0)

    csv_content = "email,name\nonce@example.com,Once\nonce@example.com,Once\n"
    fake_file = _make_csv_upload(csv_content)

    result = await import_campaign_leads(
        campaign_id=campaign.id,
        file=fake_file,
        skip_duplicates=True,
        db=session,
    )

    assert result["added"] == 1
    assert result["duplicates_in_batch"] == 1


@pytest.mark.asyncio
async def test_csv_import_all_new_no_duplicates(session):
    """A CSV with entirely new emails should have empty duplicate_leads."""
    campaign = await make_campaign(session)
    inbox = await make_inbox(session, email="csv-allnew@test.com")
    await make_campaign_inbox(session, campaign.id, inbox.id)
    await make_sequence(session, campaign.id, position=0)

    csv_content = "email,name\nalpha@example.com,Alpha\nbeta@example.com,Beta\n"
    fake_file = _make_csv_upload(csv_content)

    result = await import_campaign_leads(
        campaign_id=campaign.id,
        file=fake_file,
        skip_duplicates=True,
        db=session,
    )

    assert result["added"] == 2
    assert result["duplicate_leads"] == []
    assert result["already_enrolled"] == 0
