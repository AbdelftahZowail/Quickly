"""Tests for schedule body resolution — both sent and scheduled emails.

Covers variable substitution, A/B variants, personalized sequence overrides,
fallback content, subject resolution, is_html detection, and edge cases."""

import pytest
import pytest_asyncio
from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models import (
    Sequence, SequenceVariant, CustomEmailOverride, QueueSlot, EmailLog,
)
from app.routers.schedule import (
    _resolve_content,
    _resolve_scheduled_content,
    _resolve_sent_content,
    _serialize_scheduled,
    _serialize_sent,
    _assign_variants_to_slots,
)
from tests.conftest import (
    make_inbox, make_campaign, make_sequence, make_lead,
    make_campaign_lead, make_campaign_inbox, make_queue_slot, make_email_log,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

async def make_variant(session, sequence_id, label="A", subject="Variant Subject",
                       body="<p>Variant body</p>", is_html=True, enabled=True):
    v = SequenceVariant(
        sequence_id=sequence_id, label=label, subject=subject,
        body=body, is_html=is_html, enabled=enabled,
    )
    session.add(v)
    await session.flush()
    return v


async def make_override(session, campaign_lead_id, sequence_id,
                        subject="Custom Subject", body="<p>Custom body {{name}}</p>",
                        is_html=True):
    ov = CustomEmailOverride(
        campaign_lead_id=campaign_lead_id, sequence_id=sequence_id,
        subject=subject, body=body, is_html=is_html,
    )
    session.add(ov)
    await session.flush()
    return ov


async def reload_seq_with_variants(session, seq):
    """Re-fetch sequence with variants eagerly loaded to avoid lazy-load issues in tests."""
    result = await session.execute(
        select(Sequence).options(selectinload(Sequence.variants)).where(Sequence.id == seq.id)
    )
    return result.scalar_one()


# ── Tests: _resolve_content (core resolver) ──────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_null_sequence(session):
    """Null sequence → default placeholder subject and empty body."""
    lead = await make_lead(session)
    result = await _resolve_content(session, None, lead, None, None, None)
    assert result["subject"] == "(reply in thread)"
    assert result["body"] == ""
    assert result["is_html"] is False
    assert result["variant_id"] is None
    assert result["has_variants"] is False


@pytest.mark.asyncio
async def test_resolve_renders_variables(session):
    """{{name}} and {{email}} should be replaced with lead data."""
    campaign = await make_campaign(session)
    lead = await make_lead(session, email="john@test.com", name="John Doe")
    seq = await make_sequence(session, campaign.id, subject="Hey {{name}}",
                              body="<p>Hello {{name}} ({{email}})</p>")

    result = await _resolve_content(session, seq, lead, None, campaign, None)
    assert result["subject"] == "Hey John Doe"
    assert "Hello John Doe" in result["body"]
    assert "(john@test.com)" in result["body"]
    assert result["is_html"] is True
    assert result["has_variants"] is False


@pytest.mark.asyncio
async def test_resolve_custom_data_variables(session):
    """Variables from lead.custom_data (e.g. company) should be rendered."""
    campaign = await make_campaign(session)
    lead = await make_lead(session, email="jane@corp.com", name="Jane")
    lead.custom_data = {"company": "Acme Inc", "title": "CEO"}
    await session.flush()
    seq = await make_sequence(session, campaign.id,
                              subject="Welcome {{name}}",
                              body="<p>{{company}} - {{title}}</p>")

    result = await _resolve_content(session, seq, lead, None, campaign, None)
    assert "Acme Inc" in result["body"]
    assert "CEO" in result["body"]


@pytest.mark.asyncio
async def test_resolve_variant_preassigned(session):
    """When variant_id is passed, variant content should be used."""
    campaign = await make_campaign(session)
    lead = await make_lead(session, name="Test")
    seq = await make_sequence(session, campaign.id,
                              subject="Default Subject",
                              body="<p>Default body</p>")
    var = await make_variant(session, seq.id, label="B",
                             subject="Variant Subject",
                             body="<p>Variant body {{name}}</p>")
    seq = await reload_seq_with_variants(session, seq)

    result = await _resolve_content(session, seq, lead, None, campaign, var.id)
    assert result["subject"] == "Variant Subject"
    assert "Variant body Test" in result["body"]
    assert result["variant_id"] == var.id
    assert result["has_variants"] is True


@pytest.mark.asyncio
async def test_resolve_variant_not_preassigned(session):
    """Without variant_id, default content is shown but has_variants is True."""
    campaign = await make_campaign(session)
    lead = await make_lead(session, name="Test")
    seq = await make_sequence(session, campaign.id,
                              subject="Default Subject",
                              body="<p>Default body {{name}}</p>")
    await make_variant(session, seq.id, label="A", subject="A Subject", body="A body")
    seq = await reload_seq_with_variants(session, seq)

    result = await _resolve_content(session, seq, lead, None, campaign, None)
    assert result["subject"] == "Default Subject"
    assert "Default body Test" in result["body"]
    assert result["variant_id"] is None
    assert result["has_variants"] is True


@pytest.mark.asyncio
async def test_resolve_variant_subject_null_uses_default(session):
    """Variant with subject=None should fall back to sequence subject."""
    campaign = await make_campaign(session)
    lead = await make_lead(session, name="Test")
    seq = await make_sequence(session, campaign.id,
                              subject="Default Subject",
                              body="Default body")
    var = await make_variant(session, seq.id, subject=None,
                             body="<p>Var body</p>")
    seq = await reload_seq_with_variants(session, seq)

    result = await _resolve_content(session, seq, lead, None, campaign, var.id)
    assert result["subject"] == "Default Subject"
    assert "Var body" in result["body"]


@pytest.mark.asyncio
async def test_resolve_disabled_variant_not_used(session):
    """Disabled variant should be ignored, even if variant_id matches."""
    campaign = await make_campaign(session)
    lead = await make_lead(session, name="Test")
    seq = await make_sequence(session, campaign.id,
                              subject="Default Subject",
                              body="<p>Default body {{name}}</p>")
    disabled_var = await make_variant(session, seq.id, label="Disabled",
                                      body="Disabled body", enabled=False)
    seq = await reload_seq_with_variants(session, seq)

    result = await _resolve_content(session, seq, lead, None, campaign, disabled_var.id)
    assert "Default body Test" in result["body"]
    assert result["variant_id"] is None


@pytest.mark.asyncio
async def test_resolve_personalized_with_override(session):
    """Personalized sequence with CustomEmailOverride uses override content."""
    campaign = await make_campaign(session)
    lead = await make_lead(session, name="Alice")
    cl = await make_campaign_lead(session, campaign.id, lead.id)
    seq = await make_sequence(session, campaign.id,
                              subject="Standard Subject",
                              body="Standard body")
    seq.sequence_type = "personalized"
    seq.fallback_subject = "Fallback Subject"
    seq.fallback_body = "Fallback body"
    await session.flush()
    await make_override(session, cl.id, seq.id,
                        subject="Custom {{name}}",
                        body="<p>Custom body {{name}}</p>")

    result = await _resolve_content(session, seq, lead, cl, campaign, None)
    assert result["subject"] == "Custom Alice"
    assert "Custom body Alice" in result["body"]
    assert result["has_variants"] is False


@pytest.mark.asyncio
async def test_resolve_personalized_fallback_no_override(session):
    """Personalized sequence without override uses fallback content."""
    campaign = await make_campaign(session)
    lead = await make_lead(session, name="Bob")
    cl = await make_campaign_lead(session, campaign.id, lead.id)
    seq = await make_sequence(session, campaign.id,
                              subject="Standard Subject",
                              body="Standard body")
    seq.sequence_type = "personalized"
    seq.fallback_subject = "Fallback {{name}}"
    seq.fallback_body = "Fallback body {{name}}"
    await session.flush()

    result = await _resolve_content(session, seq, lead, cl, campaign, None)
    assert result["subject"] == "Fallback Bob"
    assert "Fallback body Bob" in result["body"]


@pytest.mark.asyncio
async def test_resolve_personalized_no_override_no_fallback(session):
    """Personalized without override & without fallback uses sequence defaults."""
    campaign = await make_campaign(session)
    lead = await make_lead(session, name="Carol")
    cl = await make_campaign_lead(session, campaign.id, lead.id)
    seq = await make_sequence(session, campaign.id,
                              subject="Seq {{name}}",
                              body="Seq body {{name}}")
    seq.sequence_type = "personalized"
    await session.flush()

    result = await _resolve_content(session, seq, lead, cl, campaign, None)
    assert result["subject"] == "Seq Carol"
    assert "Seq body Carol" in result["body"]


@pytest.mark.asyncio
async def test_resolve_empty_subject_renders_reply_in_thread(session):
    """Null or empty subject → (reply in thread)."""
    campaign = await make_campaign(session)
    lead = await make_lead(session, name="Test")
    seq = await make_sequence(session, campaign.id, subject=None,
                              body="Some body")

    result = await _resolve_content(session, seq, lead, None, campaign, None)
    assert result["subject"] == "(reply in thread)"


@pytest.mark.asyncio
async def test_resolve_is_html_auto_detect(session):
    """When is_html is None, auto-detect from body content."""
    campaign = await make_campaign(session)
    lead = await make_lead(session)
    seq = await make_sequence(session, campaign.id,
                              subject="Test",
                              body="<html><body>Hello</body></html>")
    seq.is_html = None
    await session.flush()

    result = await _resolve_content(session, seq, lead, None, campaign, None)
    assert result["is_html"] is True

    seq.body = "Plain text body"
    await session.flush()
    result2 = await _resolve_content(session, seq, lead, None, campaign, None)
    assert result2["is_html"] is False


@pytest.mark.asyncio
async def test_resolve_missing_variables_left_as_is(session):
    """Unknown template variables should be left unchanged."""
    campaign = await make_campaign(session)
    lead = await make_lead(session, email="x@y.com", name="X")
    seq = await make_sequence(session, campaign.id,
                              subject="Hello {{unknown_var}}",
                              body="<p>{{nonexistent}}</p>")

    result = await _resolve_content(session, seq, lead, None, campaign, None)
    assert "{{unknown_var}}" in result["subject"]
    assert "{{nonexistent}}" in result["body"]


# ── Tests: _serialize_scheduled ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_serialize_scheduled_with_resolved_content(session):
    """_serialize_scheduled should use resolved content when provided."""
    inbox = await make_inbox(session)
    campaign = await make_campaign(session)
    lead = await make_lead(session, name="Test")
    cl = await make_campaign_lead(session, campaign.id, lead.id)
    seq = await make_sequence(session, campaign.id)
    slot = await make_queue_slot(session, cl.id, inbox.id)

    resolved = {
        "subject": "Rendered Subject",
        "body": "<p>Rendered body</p>",
        "is_html": True,
        "variant_id": 42,
        "has_variants": True,
    }
    result = _serialize_scheduled(slot, cl, lead, campaign, inbox, seq,
                                  include_body=True, resolved_content=resolved)
    assert result["type"] == "scheduled"
    assert result["sequence_body"] == "<p>Rendered body</p>"
    assert result["sequence_is_html"] is True
    assert result["variant_id"] == 42
    assert result["has_variants"] is True


@pytest.mark.asyncio
async def test_serialize_scheduled_without_resolved(session):
    """_serialize_scheduled without resolved_content uses raw sequence data."""
    inbox = await make_inbox(session)
    campaign = await make_campaign(session)
    lead = await make_lead(session, name="Test")
    cl = await make_campaign_lead(session, campaign.id, lead.id)
    seq = await make_sequence(session, campaign.id,
                              subject="Raw Subject",
                              body="<p>Raw body</p>")
    slot = await make_queue_slot(session, cl.id, inbox.id)

    result = _serialize_scheduled(slot, cl, lead, campaign, inbox, seq,
                                  include_body=True, resolved_content=None)
    assert result["sequence_body"] == "<p>Raw body</p>"
    assert result["subject"] == "Raw Subject"
    assert result["variant_id"] is None
    assert result["has_variants"] is False


@pytest.mark.asyncio
async def test_serialize_scheduled_include_body_false(session):
    """When include_body=False, body should be empty even with resolved content."""
    inbox = await make_inbox(session)
    campaign = await make_campaign(session)
    lead = await make_lead(session, name="Test")
    cl = await make_campaign_lead(session, campaign.id, lead.id)
    seq = await make_sequence(session, campaign.id)
    slot = await make_queue_slot(session, cl.id, inbox.id)

    resolved = {"subject": "S", "body": "B", "is_html": True,
                "variant_id": None, "has_variants": False}
    result = _serialize_scheduled(slot, cl, lead, campaign, inbox, seq,
                                  include_body=False, resolved_content=resolved)
    assert result["sequence_body"] == ""


# ── Tests: _serialize_sent ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_serialize_sent_with_resolved_content(session):
    """_serialize_sent should use resolved content for body when provided."""
    inbox = await make_inbox(session)
    campaign = await make_campaign(session)
    lead = await make_lead(session, name="SentUser")
    cl = await make_campaign_lead(session, campaign.id, lead.id)
    seq = await make_sequence(session, campaign.id)
    email_log = await make_email_log(session, lead.id, campaign.id,
                                     inbox_id=inbox.id,
                                     subject="Already rendered subject")

    resolved = {
        "subject": "Already rendered subject",
        "body": "<p>Reconstructed body for SentUser</p>",
        "is_html": True,
        "variant_id": 7,
        "has_variants": True,
    }
    result = _serialize_sent(email_log, lead, campaign, seq, inbox, cl,
                             include_body=True, include_events=False,
                             resolved_content=resolved)
    assert result["type"] == "sent"
    assert result["sequence_body"] == "<p>Reconstructed body for SentUser</p>"
    assert result["subject"] == "Already rendered subject"
    assert result["variant_id"] == 7
    assert result["has_variants"] is True


@pytest.mark.asyncio
async def test_serialize_sent_without_resolved(session):
    """_serialize_sent without resolved_content uses raw seq.body."""
    inbox = await make_inbox(session)
    campaign = await make_campaign(session)
    lead = await make_lead(session, name="Test")
    seq = await make_sequence(session, campaign.id,
                              body="Raw seq body", subject="Raw Seq Subject")
    email_log = await make_email_log(session, lead.id, campaign.id,
                                     inbox_id=inbox.id,
                                     subject="Logged Subject")

    result = _serialize_sent(email_log, lead, campaign, seq, inbox, None,
                             include_body=True, include_events=False,
                             resolved_content=None)
    assert result["sequence_body"] == "Raw seq body"
    assert result["subject"] == "Logged Subject"


# ── Tests: _resolve_scheduled_content (wrapper) ─────────────────────────────

@pytest.mark.asyncio
async def test_resolve_scheduled_content_passes_variant_id(session):
    """_resolve_scheduled_content reads variant_id from slot."""
    inbox = await make_inbox(session)
    campaign = await make_campaign(session)
    lead = await make_lead(session, name="Test")
    cl = await make_campaign_lead(session, campaign.id, lead.id)
    seq = await make_sequence(session, campaign.id, subject="Default",
                              body="<p>Default</p>")
    var = await make_variant(session, seq.id, body="<p>Variant</p>")
    seq = await reload_seq_with_variants(session, seq)
    slot = await make_queue_slot(session, cl.id, inbox.id)
    slot.variant_id = var.id
    await session.flush()

    result = await _resolve_scheduled_content(session, slot, seq, lead, cl,
                                              campaign, inbox)
    assert "Variant" in result["body"]
    assert result["variant_id"] == var.id


# ── Tests: _resolve_sent_content (wrapper) ──────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_sent_content_uses_log_subject(session):
    """_resolve_sent_content overrides resolved subject with el.subject."""
    inbox = await make_inbox(session)
    campaign = await make_campaign(session)
    lead = await make_lead(session, name="SentUser")
    cl = await make_campaign_lead(session, campaign.id, lead.id)
    seq = await make_sequence(session, campaign.id,
                              subject="Template {{name}}",
                              body="<p>Body {{name}}</p>")
    email_log = await make_email_log(session, lead.id, campaign.id,
                                     inbox_id=inbox.id,
                                     subject="Final Sent Subject")

    result = await _resolve_sent_content(session, email_log, seq, lead, cl,
                                         campaign)
    assert result["subject"] == "Final Sent Subject"
    assert "Body SentUser" in result["body"]


@pytest.mark.asyncio
async def test_resolve_sent_content_uses_variant_id(session):
    """_resolve_sent_content uses email_log.variant_id."""
    inbox = await make_inbox(session)
    campaign = await make_campaign(session)
    lead = await make_lead(session, name="VariantUser")
    seq = await make_sequence(session, campaign.id, subject="Default",
                              body="<p>Default</p>")
    var = await make_variant(session, seq.id, body="<p>Variant {{name}}</p>")
    seq = await reload_seq_with_variants(session, seq)
    email_log = await make_email_log(session, lead.id, campaign.id,
                                     inbox_id=inbox.id,
                                     subject="Sent Subj")
    email_log.variant_id = var.id
    await session.flush()

    result = await _resolve_sent_content(session, email_log, seq, lead, None,
                                         campaign)
    assert "Variant VariantUser" in result["body"]
    assert result["variant_id"] == var.id


# ── Tests: _assign_variants_to_slots ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_assign_variants_to_empty_queue(session):
    """Assigning variants with no slots should be a no-op."""
    await _assign_variants_to_slots(session)


@pytest.mark.asyncio
async def test_assign_variants_no_variants(session):
    """Slots with no enabled variants should have variant_id=NULL."""
    inbox = await make_inbox(session)
    campaign = await make_campaign(session)
    lead = await make_lead(session)
    cl = await make_campaign_lead(session, campaign.id, lead.id)
    seq = await make_sequence(session, campaign.id)
    slot = await make_queue_slot(session, cl.id, inbox.id)
    await make_campaign_inbox(session, campaign.id, inbox.id)
    slot.variant_id = 999  # pre-set, should be cleared
    await session.flush()

    await _assign_variants_to_slots(session)
    await session.refresh(slot)
    assert slot.variant_id is None


@pytest.mark.asyncio
async def test_assign_variants_with_enabled_variants(session):
    """Slots with enabled variants should get a variant (or None)."""
    inbox = await make_inbox(session)
    campaign = await make_campaign(session)
    lead = await make_lead(session)
    cl = await make_campaign_lead(session, campaign.id, lead.id)
    seq = await make_sequence(session, campaign.id)
    va = await make_variant(session, seq.id, label="A")
    vb = await make_variant(session, seq.id, label="B")
    slot = await make_queue_slot(session, cl.id, inbox.id)
    await make_campaign_inbox(session, campaign.id, inbox.id)
    await session.flush()

    await _assign_variants_to_slots(session)
    await session.refresh(slot)
    variant_ids = {None, va.id, vb.id}
    assert slot.variant_id in variant_ids


# ── Edge case: Empty body ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_empty_body(session):
    """Empty body should not crash."""
    campaign = await make_campaign(session)
    lead = await make_lead(session, name="Test")
    seq = await make_sequence(session, campaign.id, body="")

    result = await _resolve_content(session, seq, lead, None, campaign, None)
    assert result["body"] == ""


# ── Edge case: Missing campaign_lead ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_missing_campaign_lead_standard(session):
    """Standard sequence without campaign_lead should still resolve correctly."""
    campaign = await make_campaign(session)
    lead = await make_lead(session, name="NoCL")
    seq = await make_sequence(session, campaign.id, subject="Subj",
                              body="<p>Body {{name}}</p>")

    result = await _resolve_content(session, seq, lead, None, campaign, None)
    assert result["subject"] == "Subj"
    assert "Body NoCL" in result["body"]


@pytest.mark.asyncio
async def test_resolve_missing_campaign_lead_personalized(session):
    """Personalized sequence without campaign_lead falls back to sequence defaults."""
    campaign = await make_campaign(session)
    lead = await make_lead(session, name="NoCL2")
    seq = await make_sequence(session, campaign.id, subject="Seq Subj",
                              body="<p>Seq body {{name}}</p>")
    seq.sequence_type = "personalized"
    await session.flush()

    result = await _resolve_content(session, seq, lead, None, campaign, None)
    assert result["subject"] == "Seq Subj"
    assert "Seq body NoCL2" in result["body"]


# ── Edge case: Personalized ignores variants ────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_personalized_ignores_variants(session):
    """Personalized sequences should not look at variant_id (they use overrides)."""
    campaign = await make_campaign(session)
    lead = await make_lead(session, name="PersUser")
    cl = await make_campaign_lead(session, campaign.id, lead.id)
    seq = await make_sequence(session, campaign.id,
                              subject="Default Subject",
                              body="Default body")
    seq.sequence_type = "personalized"
    seq.fallback_body = "Fallback body {{name}}"
    await session.flush()
    var = await make_variant(session, seq.id, body="Variant body")
    seq = await reload_seq_with_variants(session, seq)

    result = await _resolve_content(session, seq, lead, cl, campaign, var.id)
    assert "Fallback body PersUser" in result["body"]
    assert result["variant_id"] is None


# ── Edge case: Preserve HTML tags after variable substitution ───────────────

@pytest.mark.asyncio
async def test_resolve_preserves_html_structure(session):
    """Variable substitution should preserve HTML tags around variables."""
    campaign = await make_campaign(session)
    lead = await make_lead(session, name="Safe")
    seq = await make_sequence(session, campaign.id,
                              subject="Hello {{name}}",
                              body="<p>Hi {{name}}, <strong class='bold'>welcome</strong></p>")

    result = await _resolve_content(session, seq, lead, None, campaign, None)
    assert "Hi Safe" in result["body"]
    assert "<strong class='bold'>" in result["body"]


# ── Edge case: Subject with leading/trailing whitespace ─────────────────────

@pytest.mark.asyncio
async def test_resolve_subject_trims_whitespace(session):
    """Subject with whitespace should be trimmed."""
    campaign = await make_campaign(session)
    lead = await make_lead(session, name="Trim")
    seq = await make_sequence(session, campaign.id, subject="   Spaces   ",
                              body="Body")

    result = await _resolve_content(session, seq, lead, None, campaign, None)
    assert result["subject"] == "Spaces"
