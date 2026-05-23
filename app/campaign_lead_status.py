"""Per–campaign-lead enrollment status, interest, and send-eligibility rules."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import and_, exists, or_, select

if TYPE_CHECKING:
    from app.models import CampaignLead, Lead

# Enrollment (per CampaignLead) — pipeline position / outcome for this campaign.
ENROLLMENT_STATUSES = frozenset(
    {"active", "contacted", "completed", "bounced", "unsubscribed", "wrong_person", "needs_custom_email"}
)

# Reply / intent classification (per CampaignLead). Unsubscribe & wrong person live in enrollment.
LEAD_INTERESTS = frozenset({"interested", "not_interested", "out_of_office", "auto_reply"})

VERIFICATION_BLOCKS_SEND = frozenset({"invalid", "risky", "pending"})


def enrollment_blocks_sends(status: str | None) -> bool:
    if not status:
        return False
    return status in ("bounced", "unsubscribed", "wrong_person", "completed")


def interest_blocks_sends(interest: str | None) -> bool:
    return interest in ("not_interested", "out_of_office")


def campaign_lead_may_receive_sends(cl: "CampaignLead", lead: "Lead") -> bool:
    """True if this enrollment should be considered for outbound scheduling."""
    ev = getattr(lead, "email_verification_status", None)
    if ev in VERIFICATION_BLOCKS_SEND:
        return False
    if getattr(cl, "sending_paused", False):
        return False
    st = getattr(cl, "enrollment_status", None) or "active"
    if enrollment_blocks_sends(st):
        return False
    if interest_blocks_sends(getattr(cl, "interest_status", None)):
        return False
    return True


def campaign_lead_schedule_eligibility_clause():
    """SQLAlchemy predicate for rows that should receive queue slots.

    Use with ``CampaignLead``, ``Lead``, and ``Campaign`` joined on their
    foreign keys (same shape as ``recalculate_all_campaigns``). Matches
    ``campaign_lead_may_receive_sends`` plus the send job's ``stop_on_reply``
    rule so the schedule mirrors what the sender will actually deliver.
    """
    from app.models import Campaign, CampaignLead, Lead, LeadReply

    _ver = tuple(VERIFICATION_BLOCKS_SEND)
    has_reply = exists(
        select(1).where(
            LeadReply.lead_id == CampaignLead.lead_id,
            LeadReply.campaign_id == CampaignLead.campaign_id,
        )
    )
    return and_(
        CampaignLead.sending_paused.is_(False),
        CampaignLead.enrollment_status.in_(("active", "contacted")),
        or_(
            CampaignLead.interest_status.is_(None),
            CampaignLead.interest_status.notin_(("not_interested", "out_of_office")),
        ),
        or_(
            Lead.email_verification_status.is_(None),
            Lead.email_verification_status.notin_(_ver),
        ),
        or_(Campaign.stop_on_reply.is_(False), ~has_reply),
    )


def normalize_enrollment_status(raw: str | None, default: str = "active") -> str:
    s = (raw or "").strip().lower()
    if s in ENROLLMENT_STATUSES:
        return s
    return default


def normalize_interest(raw: str | None) -> str | None:
    s = (raw or "").strip().lower()
    if not s or s in ("null", "none", ""):
        return None
    if s in LEAD_INTERESTS:
        return s
    return None
