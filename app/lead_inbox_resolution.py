"""Resolve sending inbox per (lead_id, campaign_id) for UI display.

Uses the inbox that last sent in the campaign when available; otherwise the
inbox assigned to the next scheduled queue slot for that enrollment.
"""
from __future__ import annotations

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CampaignLead, EmailLog, Inbox, QueueSlot


async def from_inbox_email_by_lead_campaign(
    db: AsyncSession,
    pairs: set[tuple[int, int]],
) -> dict[tuple[int, int], str]:
    """Map (lead_id, campaign_id) -> inbox email for last send or next scheduled send."""
    if not pairs:
        return {}

    lead_ids = {p[0] for p in pairs}
    campaign_ids = {p[1] for p in pairs}

    last_by_pair: dict[tuple[int, int], str] = {}

    mx_ids = (
        select(
            EmailLog.lead_id,
            EmailLog.campaign_id,
            func.max(EmailLog.id).label("max_id"),
        )
        .where(
            EmailLog.lead_id.in_(lead_ids),
            EmailLog.campaign_id.in_(campaign_ids),
        )
        .group_by(EmailLog.lead_id, EmailLog.campaign_id)
    ).subquery()

    last_res = await db.execute(
        select(EmailLog.lead_id, EmailLog.campaign_id, Inbox.email)
        .join(mx_ids, EmailLog.id == mx_ids.c.max_id)
        .outerjoin(Inbox, EmailLog.inbox_id == Inbox.id)
    )
    for lid, cid, em in last_res.all():
        pair = (lid, cid)
        if pair in pairs and em:
            last_by_pair[pair] = em

    next_by_pair: dict[tuple[int, int], str] = {}
    mn = (
        select(
            QueueSlot.campaign_lead_id,
            func.min(QueueSlot.sequence_index).label("mn_seq"),
        )
        .join(CampaignLead, QueueSlot.campaign_lead_id == CampaignLead.id)
        .where(
            CampaignLead.lead_id.in_(lead_ids),
            CampaignLead.campaign_id.in_(campaign_ids),
        )
        .group_by(QueueSlot.campaign_lead_id)
    ).subquery()

    next_res = await db.execute(
        select(CampaignLead.lead_id, CampaignLead.campaign_id, Inbox.email)
        .select_from(mn)
        .join(
            QueueSlot,
            and_(
                QueueSlot.campaign_lead_id == mn.c.campaign_lead_id,
                QueueSlot.sequence_index == mn.c.mn_seq,
            ),
        )
        .join(CampaignLead, CampaignLead.id == mn.c.campaign_lead_id)
        .join(Inbox, QueueSlot.inbox_id == Inbox.id)
    )
    for lid, cid, em in next_res.all():
        pair = (lid, cid)
        if pair in pairs and em:
            next_by_pair[pair] = em

    out: dict[tuple[int, int], str] = {}
    for pair in pairs:
        em = last_by_pair.get(pair) or next_by_pair.get(pair)
        if em:
            out[pair] = em
    return out
