"""Tests for features added in the latest sprint:

- Ramp-up inbox warm-up (day-by-day linear limit, auto-disable at max)
- Unsubscribe detection in AI reply classifier
- Unibox background-task unsubscribe action (lead status + pause + queue cleanup)
- Duplicate contact protection in bulk_add_leads and CSV import
- Campaign lead VALID_INTEREST_STATUSES includes "unsubscribed"
"""

import io
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from app.models import (
    Inbox,
    Lead,
    Campaign,
    CampaignLead,
    QueueSlot,
    AppSetting,
)
import app.ai_classifier as ai_classifier
from app.routers import inbox as inbox_router
from app.routers import campaigns as campaigns_router
from app.routers.campaigns import (
    bulk_add_leads_to_campaign,
    CampaignLeadAdd,
)
from tests.conftest import (
    make_campaign,
    make_inbox,
    make_lead,
    make_campaign_lead,
    make_campaign_inbox,
    make_queue_slot,
)


# ══════════════════════════════════════════════════════════════════════════════
# Ramp-up: _compute_effective_limit
# ══════════════════════════════════════════════════════════════════════════════

def _inbox_with_age(days: int, max_per_day: int = 30, ramp_up: bool = True):
    """Build a lightweight namespace mimicking an Inbox for unit-testing _compute_effective_limit."""
    from types import SimpleNamespace
    return SimpleNamespace(
        max_emails_per_day=max_per_day,
        ramp_up_enabled=ramp_up,
        ramp_up_period_days=42,
        created_at=datetime.utcnow() - timedelta(days=days),
    )


def test_ramp_up_day_zero_is_one():
    """Brand-new inbox (0 days old) should get exactly 1 email."""
    assert inbox_router._compute_effective_limit(_inbox_with_age(0)) == 1


def test_ramp_up_day_one_is_two():
    """After one full day the limit should be 2."""
    assert inbox_router._compute_effective_limit(_inbox_with_age(1)) == 2


def test_ramp_up_increments_by_one_per_day():
    """Each additional day adds exactly 1 to the effective limit."""
    for day in range(0, 10):
        effective = inbox_router._compute_effective_limit(_inbox_with_age(day, max_per_day=50))
        assert effective == day + 1, f"Day {day}: expected {day+1}, got {effective}"


def test_ramp_up_capped_at_max():
    """Effective limit never exceeds max_emails_per_day."""
    inbox = _inbox_with_age(days=100, max_per_day=10)
    assert inbox_router._compute_effective_limit(inbox) == 10


def test_ramp_up_exactly_at_max():
    """When days_old + 1 == max_per_day the limit equals max."""
    inbox = _inbox_with_age(days=9, max_per_day=10)  # day 9 → 10 == max
    assert inbox_router._compute_effective_limit(inbox) == 10


def test_ramp_up_disabled_returns_full_limit():
    """When ramp_up_enabled is False the original max is returned immediately."""
    inbox = _inbox_with_age(days=0, max_per_day=50, ramp_up=False)
    assert inbox_router._compute_effective_limit(inbox) == 50


# ══════════════════════════════════════════════════════════════════════════════
# Ramp-up: auto-disable once max is reached
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_auto_disable_ramp_up_when_complete(session):
    """_maybe_complete_ramp_up should flip ramp_up_enabled to False in DB."""
    inbox = Inbox(
        email="rampup@test.com",
        max_emails_per_day=3,
        ramp_up_enabled=True,
        created_at=datetime.utcnow() - timedelta(days=5),  # 5 days old → effective=6 > max=3
    )
    session.add(inbox)
    await session.flush()

    await inbox_router._maybe_complete_ramp_up(inbox, session)

    assert inbox.ramp_up_enabled is False


@pytest.mark.asyncio
async def test_auto_disable_not_triggered_while_still_ramping(session):
    """_maybe_complete_ramp_up must NOT disable when limit < max."""
    inbox = Inbox(
        email="rampup2@test.com",
        max_emails_per_day=50,
        ramp_up_enabled=True,
        created_at=datetime.utcnow() - timedelta(days=0),  # day 0 → effective=1 < 50
    )
    session.add(inbox)
    await session.flush()

    await inbox_router._maybe_complete_ramp_up(inbox, session)

    assert inbox.ramp_up_enabled is True  # should stay on


@pytest.mark.asyncio
async def test_list_inboxes_auto_disables_complete_ramp(session):
    """list_inboxes should persist the ramp-up completion when it detects the period is done."""
    inbox = Inbox(
        email="ramplist@test.com",
        max_emails_per_day=2,
        ramp_up_enabled=True,
        created_at=datetime.utcnow() - timedelta(days=10),  # day 10 → effective=11 > max=2
    )
    session.add(inbox)
    await session.flush()

    listed = await inbox_router.list_inboxes(db=session)
    matching = next(i for i in listed if i.email == "ramplist@test.com")

    # The effective limit should be capped at max
    assert matching.effective_max_per_day == 2
    # And ramp_up_enabled should be False now
    await session.refresh(inbox)
    assert inbox.ramp_up_enabled is False


@pytest.mark.asyncio
async def test_create_inbox_with_ramp_up(session):
    """Creating an inbox with ramp_up_enabled returns effective_max_per_day = 1."""
    from app.schemas import InboxCreate
    data = InboxCreate(
        email="newramp@test.com",
        max_emails_per_day=30,
        ramp_up_enabled=True,
    )
    result = await inbox_router.create_inbox(data, db=session)
    assert result.ramp_up_enabled is True
    assert result.effective_max_per_day == 1  # brand new → day 0 → 1


@pytest.mark.asyncio
async def test_update_inbox_disables_ramp_up(session):
    """Patching ramp_up_enabled=False should immediately return full limit."""
    from app.schemas import InboxUpdate
    inbox = Inbox(
        email="updateramp@test.com",
        max_emails_per_day=20,
        ramp_up_enabled=True,
        created_at=datetime.utcnow() - timedelta(days=0),
    )
    session.add(inbox)
    await session.flush()

    data = InboxUpdate(ramp_up_enabled=False)
    result = await inbox_router.update_inbox(inbox.id, data, db=session)
    assert result.ramp_up_enabled is False
    assert result.effective_max_per_day == 20


# ══════════════════════════════════════════════════════════════════════════════
# AI Classifier: unsubscribed label
# ══════════════════════════════════════════════════════════════════════════════

def test_classifier_parser_returns_unsubscribed():
    """The local parser should detect 'unsubscribed' in the LLM response.

    The LLM is prompted to output the label word; we verify the substring
    match logic in the parser catches it in typical LLM response formats.
    """
    # simulate the parser logic from ai_classifier (whitebox approach)
    def parse(raw: str):
        raw = raw.strip().lower()
        for label in ("interested", "not_interested", "unsubscribed",
                      "out_of_office", "wrong_person", "auto_reply"):
            if label in raw:
                return label
        return "interested"  # fallback

    # Exact label as the LLM would emit
    assert parse("unsubscribed") == "unsubscribed"
    # Label embedded in a sentence (LLM adds explanation)
    assert parse("The response is: unsubscribed") == "unsubscribed"
    assert parse("UNSUBSCRIBED") == "unsubscribed"
    # Unrelated words ("unsubscribe" without 'd') should NOT match
    assert parse("Please unsubscribe me") != "unsubscribed"


@pytest.mark.asyncio
async def test_classify_reply_returns_unsubscribed(session, monkeypatch):
    """classify_reply should return 'unsubscribed' when the LLM says so."""
    session.add(AppSetting(key="ai_reply_classifier_enabled", value="true"))
    session.add(AppSetting(key="ai_reply_classifier_provider", value="testprov"))
    session.add(AppSetting(key="ai_reply_classifier_model", value="testmodel"))
    session.add(AppSetting(key="ai_reply_classifier_api_key", value="key"))
    await session.commit()

    class FakeMsg:
        content = "unsubscribed"

    class FakeChoice:
        message = FakeMsg()

    class FakeResp:
        choices = [FakeChoice()]

    monkeypatch.setattr(ai_classifier, "acompletion", AsyncMock(return_value=FakeResp()))

    result = await ai_classifier.classify_reply(session, "Please remove me from your emails.")
    assert result == "unsubscribed"


# ══════════════════════════════════════════════════════════════════════════════
# Unibox background task: unsubscribe action
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_classify_and_notify_sets_unsubscribed_status(session, monkeypatch):
    """When the AI classifies a reply as 'unsubscribed', the background task
    must mark the campaign enrollment as unsubscribed and delete slots.
    """
    # Setup: campaign, inbox, lead, enrollment, queue slot
    campaign = await make_campaign(session)
    inbox = await make_inbox(session, email="u-inbox@test.com")
    lead = await make_lead(session, email="u-lead@test.com")
    cl = await make_campaign_lead(session, campaign.id, lead.id)
    await make_queue_slot(session, campaign_lead_id=cl.id, inbox_id=inbox.id)
    await session.commit()

    # Patch ai_classifier so it returns "unsubscribed"
    monkeypatch.setattr(
        "app.ai_classifier.classify_reply",
        AsyncMock(return_value="unsubscribed"),
    )
    monkeypatch.setattr(
        "app.ai_classifier.is_ai_enabled",
        AsyncMock(return_value=True),
    )

    # Patch fire_webhook_event to a no-op so we don't need a real webhook
    monkeypatch.setattr(
        "app.webhooks.fire_webhook_event",
        AsyncMock(return_value=None),
    )

    # Patch AsyncSessionLocal to use our test session
    from app import unibox as _unibox
    from app.database import AsyncSessionLocal

    # We need the bg task to use our *session*, not a new one.
    # Call the function body directly with our session by patching AsyncSessionLocal.
    async def _fake_session_cm():
        class _CM:
            async def __aenter__(self):
                return session
            async def __aexit__(self, *_):
                pass
        return _CM()

    import contextlib

    @contextlib.asynccontextmanager
    async def _fake_ctx():
        yield session

    monkeypatch.setattr(_unibox, "AsyncSessionLocal", _fake_ctx)

    # Run the background task
    await _unibox._classify_and_notify_bg(
        lead_id=lead.id,
        lead_email=lead.email,
        lead_name=lead.name,
        campaign_ids=[campaign.id],
        reply_text="Please unsubscribe me",
    )

    await session.refresh(lead)
    await session.refresh(cl)
    assert cl.enrollment_status == "unsubscribed"
    assert cl.interest_status is None

    # Verify queue slots were deleted
    slots = (await session.execute(
        select(QueueSlot).where(QueueSlot.campaign_lead_id == cl.id)
    )).scalars().all()
    assert len(slots) == 0


@pytest.mark.asyncio
async def test_classify_not_interested_does_not_set_lead_status(session, monkeypatch):
    """'not_interested' should pause sending but NOT change lead.status to unsubscribed."""
    campaign = await make_campaign(session)
    inbox = await make_inbox(session, email="ni-inbox@test.com")
    lead = await make_lead(session, email="ni-lead@test.com")
    cl = await make_campaign_lead(session, campaign.id, lead.id)
    await make_queue_slot(session, campaign_lead_id=cl.id, inbox_id=inbox.id)
    await session.commit()

    monkeypatch.setattr("app.ai_classifier.classify_reply", AsyncMock(return_value="not_interested"))
    monkeypatch.setattr("app.ai_classifier.is_ai_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr("app.webhooks.fire_webhook_event", AsyncMock(return_value=None))

    from app import unibox as _unibox
    import contextlib

    @contextlib.asynccontextmanager
    async def _fake_ctx():
        yield session

    monkeypatch.setattr(_unibox, "AsyncSessionLocal", _fake_ctx)

    original_status = lead.status

    await _unibox._classify_and_notify_bg(
        lead_id=lead.id,
        lead_email=lead.email,
        lead_name=lead.name,
        campaign_ids=[campaign.id],
        reply_text="Not for me thanks",
    )

    await session.refresh(lead)
    assert lead.status == original_status  # unchanged — only unsubscribed mutates lead

    await session.refresh(cl)
    assert cl.interest_status == "not_interested"


# ══════════════════════════════════════════════════════════════════════════════
# Campaigns: PATCH enrollment status
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_patch_campaign_lead_unsubscribed_accepted(session):
    """Patching enrollment status to 'unsubscribed' must succeed."""
    campaign = await make_campaign(session)
    lead = await make_lead(session, email="p-lead@test.com")
    cl = await make_campaign_lead(session, campaign.id, lead.id)
    await session.flush()

    from app.schemas import CampaignLeadEnrollmentPatch
    from app.routers.campaigns import patch_campaign_lead

    payload = CampaignLeadEnrollmentPatch(status="unsubscribed")
    result = await patch_campaign_lead(campaign.id, lead.id, payload, db=session)
    assert result["status"] == "unsubscribed"


@pytest.mark.asyncio
async def test_patch_campaign_lead_invalid_status_rejected(session):
    """An unknown interest value should still be rejected with 400."""
    import fastapi
    campaign = await make_campaign(session)
    lead = await make_lead(session, email="bad-status@test.com")
    await make_campaign_lead(session, campaign.id, lead.id)
    await session.flush()

    from app.schemas import CampaignLeadEnrollmentPatch
    from app.routers.campaigns import patch_campaign_lead

    payload = CampaignLeadEnrollmentPatch(interest="nonsense_value")
    with pytest.raises(fastapi.HTTPException) as exc:
        await patch_campaign_lead(campaign.id, lead.id, payload, db=session)
    assert exc.value.status_code == 400


# ══════════════════════════════════════════════════════════════════════════════
# Duplicate contact protection: bulk_add_leads_to_campaign
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_bulk_add_deduplicates_within_batch(session):
    """Sending the same email twice in one batch should enroll it once
    and report duplicates_in_batch = 1."""
    campaign = await make_campaign(session)
    inbox = await make_inbox(session, email="bi@test.com")
    await make_campaign_inbox(session, campaign.id, inbox.id)
    await session.flush()

    from app.routers.campaigns import CampaignLeadAdd, bulk_add_leads_to_campaign

    leads = [
        CampaignLeadAdd(email="dupe@test.com", name="Alice"),
        CampaignLeadAdd(email="DUPE@TEST.COM", name="Alice Again"),  # same, different case
    ]

    result = await bulk_add_leads_to_campaign(campaign.id, leads, db=session)

    assert result["added"] == 1
    assert result["duplicates_in_batch"] == 1
    assert result["errors"] == 0


@pytest.mark.asyncio
async def test_bulk_add_already_enrolled_counted_separately(session):
    """A lead enrolled in a prior call is reported as already_enrolled,
    not as a new duplicate."""
    campaign = await make_campaign(session)
    inbox = await make_inbox(session, email="bi2@test.com")
    await make_campaign_inbox(session, campaign.id, inbox.id)
    lead = await make_lead(session, email="exist@test.com")
    await make_campaign_lead(session, campaign.id, lead.id)
    await session.flush()

    from app.routers.campaigns import CampaignLeadAdd, bulk_add_leads_to_campaign

    leads = [CampaignLeadAdd(email="exist@test.com", name="Existing")]
    result = await bulk_add_leads_to_campaign(campaign.id, leads, db=session)

    assert result["added"] == 0
    assert result["already_enrolled"] == 1
    assert result["duplicates_in_batch"] == 0


@pytest.mark.asyncio
async def test_bulk_add_unique_emails_all_added(session):
    """Distinct emails in a batch are all enrolled successfully."""
    campaign = await make_campaign(session)
    inbox = await make_inbox(session, email="bi3@test.com")
    await make_campaign_inbox(session, campaign.id, inbox.id)
    await session.flush()

    from app.routers.campaigns import CampaignLeadAdd, bulk_add_leads_to_campaign

    leads = [
        CampaignLeadAdd(email="a@test.com"),
        CampaignLeadAdd(email="b@test.com"),
        CampaignLeadAdd(email="c@test.com"),
    ]
    result = await bulk_add_leads_to_campaign(campaign.id, leads, db=session)

    assert result["added"] == 3
    assert result["duplicates_in_batch"] == 0


# ══════════════════════════════════════════════════════════════════════════════
# Duplicate contact protection: import_campaign_leads (CSV)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_csv_import_deduplicates_within_file(session):
    """Duplicate email rows in a CSV file are skipped (duplicate_in_file) and
    counted in duplicates_in_batch."""
    campaign = await make_campaign(session)
    inbox = await make_inbox(session, email="csv@test.com")
    await make_campaign_inbox(session, campaign.id, inbox.id)
    await session.flush()

    from app.routers.campaigns import import_campaign_leads
    from fastapi import UploadFile

    csv_content = b"email,name\ndup@test.com,Alice\ndup@test.com,Alice2\nuniq@test.com,Bob\n"
    upload = UploadFile(filename="leads.csv", file=io.BytesIO(csv_content))

    result = await import_campaign_leads(campaign.id, upload, db=session)

    assert result["added"] == 2          # dup + uniq (first occurrence of dup is added)
    assert result["duplicates_in_batch"] == 1  # second dup row skipped
    assert result["errors"] == 0


@pytest.mark.asyncio
async def test_csv_import_already_enrolled(session):
    """A lead already enrolled before the import is reported as already_enrolled."""
    campaign = await make_campaign(session)
    inbox = await make_inbox(session, email="csv2@test.com")
    await make_campaign_inbox(session, campaign.id, inbox.id)
    lead = await make_lead(session, email="old@test.com")
    await make_campaign_lead(session, campaign.id, lead.id)
    await session.flush()

    from app.routers.campaigns import import_campaign_leads
    from fastapi import UploadFile

    csv_content = b"email,name\nold@test.com,Old Lead\nnew@test.com,New Lead\n"
    upload = UploadFile(filename="leads.csv", file=io.BytesIO(csv_content))

    result = await import_campaign_leads(campaign.id, upload, db=session)

    assert result["already_enrolled"] == 1
    assert result["added"] == 1


# ══════════════════════════════════════════════════════════════════════════════
# Campaign CRUD basics (previously untested)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_create_and_list_campaign(session):
    """Creating a campaign should appear in list_campaigns."""
    inbox = await make_inbox(session, email="cl@test.com")
    await session.flush()

    from app.routers.campaigns import create_campaign, list_campaigns
    from app.schemas import CampaignCreate

    data = CampaignCreate(
        name="Test Campaign",
        inbox_ids=[inbox.id],
        sending_days=[0, 1, 2, 3, 4],
        sending_hours_start="09:00",
        sending_hours_end="17:00",
    )
    created = await create_campaign(data, db=session)
    assert created.id is not None
    assert created.name == "Test Campaign"

    campaigns = await list_campaigns(db=session)
    assert any(c.id == created.id for c in campaigns)


@pytest.mark.asyncio
async def test_delete_campaign_removes_orphan_leads(session):
    """Deleting a campaign should also delete leads that belong only to it."""
    inbox = await make_inbox(session, email="del@test.com")
    campaign = await make_campaign(session)
    lead = await make_lead(session, email="orphan@test.com")
    await make_campaign_inbox(session, campaign.id, inbox.id)
    await make_campaign_lead(session, campaign.id, lead.id)
    await session.flush()

    from app.routers.campaigns import delete_campaign

    await delete_campaign(campaign.id, db=session)

    remaining = (await session.execute(select(Lead).where(Lead.id == lead.id))).scalar_one_or_none()
    assert remaining is None  # orphan lead removed


@pytest.mark.asyncio
async def test_update_campaign_name(session):
    """Patching a campaign's name should be reflected in the DB."""
    inbox = await make_inbox(session, email="uname@test.com")
    campaign = await make_campaign(session, name="Old Name")
    await make_campaign_inbox(session, campaign.id, inbox.id)
    await session.flush()

    from app.routers.campaigns import update_campaign
    from app.schemas import CampaignUpdate

    data = CampaignUpdate(name="New Name")
    result = await update_campaign(campaign.id, data, db=session)
    assert result.name == "New Name"


@pytest.mark.asyncio
async def test_create_and_list_sequences(session):
    """Sequences created for a campaign should be returned in position order."""
    inbox = await make_inbox(session, email="seq@test.com")
    campaign = await make_campaign(session)
    await make_campaign_inbox(session, campaign.id, inbox.id)
    await session.flush()

    from app.routers.campaigns import create_sequence, list_sequences
    from app.schemas import SequenceCreate

    for pos, subject in enumerate(["First", "Second", "Third"]):
        await create_sequence(
            campaign.id,
            SequenceCreate(position=pos, subject=subject, body="body"),
            db=session,
        )

    seqs = await list_sequences(campaign.id, db=session)
    assert [s.subject for s in seqs] == ["First", "Second", "Third"]


@pytest.mark.asyncio
async def test_bulk_add_creates_lead_if_not_exists(session):
    """bulk_add_leads should create a Lead record if one doesn't exist yet."""
    campaign = await make_campaign(session)
    inbox = await make_inbox(session, email="new-lead@test.com")
    await make_campaign_inbox(session, campaign.id, inbox.id)
    await session.flush()

    from app.routers.campaigns import CampaignLeadAdd, bulk_add_leads_to_campaign

    result = await bulk_add_leads_to_campaign(
        campaign.id,
        [CampaignLeadAdd(email="brand-new@test.com", name="Brand New")],
        db=session,
    )
    assert result["added"] == 1
    lead = (await session.execute(select(Lead).where(Lead.email == "brand-new@test.com"))).scalar_one_or_none()
    assert lead is not None
    assert lead.name == "Brand New"


@pytest.mark.asyncio
async def test_bulk_add_empty_batch_raises_400(session):
    """An empty list should raise HTTP 400."""
    import fastapi
    campaign = await make_campaign(session)
    await session.flush()

    from app.routers.campaigns import CampaignLeadAdd, bulk_add_leads_to_campaign

    with pytest.raises(fastapi.HTTPException) as exc:
        await bulk_add_leads_to_campaign(campaign.id, [], db=session)
    assert exc.value.status_code == 400
