"""Tests for:
- Inbox pause/unpause endpoints (pause_leads and reassign strategies)
- Bulk lead addition with skip_duplicates option
- CSV import with skip_duplicates option
"""

import io
import pytest
from datetime import datetime, timedelta
from fastapi import BackgroundTasks, HTTPException

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
    result = await inbox_router.update_inbox(
        inbox.id, data, BackgroundTasks(), db=session
    )
    assert result.paused is True

    data2 = InboxUpdate(paused=False)
    result2 = await inbox_router.update_inbox(
        inbox.id, data2, BackgroundTasks(), db=session
    )
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
    await inbox_router.pause_inbox(
        inbox.id, body, BackgroundTasks(), db=session
    )

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
    await inbox_router.pause_inbox(
        inbox.id, body, BackgroundTasks(), db=session
    )

    await session.refresh(cl)
    # No future slots → sending_paused should stay False
    assert cl.sending_paused is False


@pytest.mark.asyncio
async def test_pause_inbox_marks_inbox_paused(session):
    """After pausing, inbox.paused should be True."""
    inbox = await make_inbox(session, email="mark-paused@test.com")
    body = PauseInboxRequest(action="pause_leads")
    result = await inbox_router.pause_inbox(
        inbox.id, body, BackgroundTasks(), db=session
    )
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
        await inbox_router.pause_inbox(
            inbox.id, body, BackgroundTasks(), db=session
        )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_pause_inbox_not_found_raises(session):
    """Pausing a non-existent inbox should raise 404."""
    body = PauseInboxRequest(action="pause_leads")
    with pytest.raises(HTTPException) as exc_info:
        await inbox_router.pause_inbox(
            9999, body, BackgroundTasks(), db=session
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_pause_inbox_invalid_action_raises(session):
    """An unknown action string should raise 400."""
    inbox = await make_inbox(session, email="bad-action@test.com")
    body = PauseInboxRequest(action="delete_everything")
    with pytest.raises(HTTPException) as exc_info:
        await inbox_router.pause_inbox(
            inbox.id, body, BackgroundTasks(), db=session
        )
    assert exc_info.value.status_code == 400


# ══════════════════════════════════════════════════════════════════════════════
# Inbox pause: reassign strategy
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_pause_inbox_reassign_moves_slots(session):
    """action='reassign' triggers a full recalculation; slots on the paused inbox
    are replaced with slots on the remaining active inbox."""
    inbox_a = await make_inbox(session, email="inbox-a@test.com")
    inbox_b = await make_inbox(session, email="inbox-b@test.com")
    campaign = await make_campaign(session, sending_days=[0, 1, 2, 3, 4])
    # Both inboxes registered with the campaign so recalc can use inbox_b
    await make_campaign_inbox(session, campaign.id, inbox_a.id, position=0)
    await make_campaign_inbox(session, campaign.id, inbox_b.id, position=1)
    await make_sequence(session, campaign.id, position=0)
    lead = await make_lead(session, email="reassign-lead@test.com")
    cl = await make_campaign_lead(session, campaign.id, lead.id)
    await make_queue_slot(
        session,
        campaign_lead_id=cl.id,
        inbox_id=inbox_a.id,
        scheduled_date=_future_slot_date(),
    )

    body = PauseInboxRequest(action="reassign")
    bg = BackgroundTasks()
    await inbox_router.pause_inbox(inbox_a.id, body, background_tasks=bg, db=session)
    await bg()

    # After recalculation, new slots must not use the paused inbox_a
    from sqlalchemy import select as sa_select
    new_slots_res = await session.execute(
        sa_select(QueueSlot).where(QueueSlot.campaign_lead_id == cl.id)
    )
    new_slots = new_slots_res.scalars().all()
    assert len(new_slots) >= 1
    assert all(s.inbox_id != inbox_a.id for s in new_slots)


@pytest.mark.asyncio
async def test_pause_inbox_reassign_does_not_pause_leads(session):
    """With action='reassign', CampaignLeads should NOT have sending_paused set."""
    inbox_a = await make_inbox(session, email="ra-a@test.com")
    campaign = await make_campaign(session)
    lead = await make_lead(session, email="ra-lead@test.com")
    cl = await make_campaign_lead(session, campaign.id, lead.id)
    await make_queue_slot(
        session,
        campaign_lead_id=cl.id,
        inbox_id=inbox_a.id,
        scheduled_date=_future_slot_date(),
    )

    body = PauseInboxRequest(action="reassign")
    bg = BackgroundTasks()
    await inbox_router.pause_inbox(inbox_a.id, body, background_tasks=bg, db=session)
    await bg()

    await session.refresh(cl)
    assert cl.sending_paused is False


@pytest.mark.asyncio
async def test_pause_inbox_reassign_no_target_succeeds(session):
    """action='reassign' without target_inbox_id now triggers global recalc
    (no target required); the inbox should end up paused."""
    inbox = await make_inbox(session, email="no-target@test.com")
    body = PauseInboxRequest(action="reassign", target_inbox_id=None)
    result = await inbox_router.pause_inbox(
        inbox.id, body, BackgroundTasks(), db=session
    )
    assert result.paused is True


@pytest.mark.asyncio
async def test_pause_inbox_reassign_with_nonexistent_target_succeeds(session):
    """action='reassign' ignores target_inbox_id; global recalc runs regardless."""
    inbox = await make_inbox(session, email="bad-target@test.com")
    body = PauseInboxRequest(action="reassign", target_inbox_id=9999)
    result = await inbox_router.pause_inbox(
        inbox.id, body, BackgroundTasks(), db=session
    )
    assert result.paused is True


@pytest.mark.asyncio
async def test_pause_inbox_reassign_target_validation_removed(session):
    """Reassign no longer validates the target inbox; the global recalc
    is run without caring about target_inbox_id."""
    inbox_a = await make_inbox(session, email="src-paused@test.com")
    inbox_b = await make_inbox(session, email="dst-paused@test.com")
    inbox_b.paused = True
    await session.flush()

    body = PauseInboxRequest(action="reassign", target_inbox_id=inbox_b.id)
    result = await inbox_router.pause_inbox(
        inbox_a.id, body, BackgroundTasks(), db=session
    )
    assert result.paused is True


@pytest.mark.asyncio
async def test_pause_inbox_reassign_sets_paused_flag(session):
    """Reassign (regardless of target_inbox_id) should mark the inbox as paused."""
    inbox = await make_inbox(session, email="self-reassign@test.com")
    body = PauseInboxRequest(action="reassign", target_inbox_id=inbox.id)
    result = await inbox_router.pause_inbox(
        inbox.id, body, BackgroundTasks(), db=session
    )
    assert result.paused is True


# ══════════════════════════════════════════════════════════════════════════════
# Inbox unpause
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_unpause_inbox_clears_flag(session):
    """unpause_inbox should set paused=False and return the inbox."""
    inbox = await make_inbox(session, email="unpause-me@test.com")
    inbox.paused = True
    await session.flush()

    result = await inbox_router.unpause_inbox(
        inbox.id, BackgroundTasks(), db=session
    )
    assert result.paused is False
    await session.refresh(inbox)
    assert inbox.paused is False


@pytest.mark.asyncio
async def test_unpause_inbox_not_found_raises(session):
    """Unpausing a non-existent inbox should raise 404."""
    with pytest.raises(HTTPException) as exc_info:
        await inbox_router.unpause_inbox(9999, BackgroundTasks(), db=session)
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
async def test_delete_inbox_in_use_raises_400(session):
    """Inbox linked to a campaign or carrying slots cannot be deleted;
    caller must manually remove it from campaigns first."""
    inbox_a = await make_inbox(session, email="del-a@test.com")
    campaign = await make_campaign(session)
    await make_campaign_inbox(session, campaign.id, inbox_a.id)

    with pytest.raises(HTTPException) as exc_info:
        await inbox_router.delete_inbox(inbox_a.id, db=session)
    assert exc_info.value.status_code == 400



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
async def test_bulk_add_skip_duplicates_false_skips_same_campaign(session):
    """With skip_duplicates=False, a lead already enrolled in THIS campaign is
    still skipped (avoiding a DB constraint violation).  The duplicate_leads
    list is NOT populated (that's only for cross-campaign checks when
    skip_duplicates=True), but already_enrolled is incremented."""
    campaign = await make_campaign(session)
    inbox = await make_inbox(session, email="reenroll@test.com")
    await make_campaign_inbox(session, campaign.id, inbox.id)
    await make_sequence(session, campaign.id, position=0)

    lead = await make_lead(session, email="reenroll@example.com")
    await make_campaign_lead(session, campaign.id, lead.id)

    payload = [CampaignLeadAdd(email="reenroll@example.com")]
    result = await bulk_add_leads_to_campaign(
        campaign_id=campaign.id,
        leads_data=payload,
        skip_duplicates=False,
        db=session,
    )

    # Lead already in this campaign → skipped, not re-enrolled
    assert result["added"] == 0
    assert result["already_enrolled"] == 1
    assert result["duplicate_leads"] == []

    # Exactly one enrollment entry should still exist
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
async def test_csv_import_skip_duplicates_false_skips_same_campaign(session):
    """CSV import with skip_duplicates=False skips leads already enrolled in
    THIS campaign (to avoid DB constraint violations) rather than re-enrolling
    them.  The already_enrolled counter is incremented."""
    campaign = await make_campaign(session)
    inbox = await make_inbox(session, email="csv-reenroll@test.com")
    await make_campaign_inbox(session, campaign.id, inbox.id)
    await make_sequence(session, campaign.id, position=0)

    existing = await make_lead(session, email="reenroll-csv@example.com")
    await make_campaign_lead(session, campaign.id, existing.id)

    csv_content = "email,name\nreenroll-csv@example.com,Reenroll\n"
    fake_file = _make_csv_upload(csv_content)

    result = await import_campaign_leads(
        campaign_id=campaign.id,
        file=fake_file,
        skip_duplicates=False,
        db=session,
    )

    # Lead already in this campaign → skipped, not re-enrolled
    assert result["added"] == 0
    assert result["already_enrolled"] == 1
    assert result["duplicate_leads"] == []

    # Verify exactly one enrollment entry exists
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
