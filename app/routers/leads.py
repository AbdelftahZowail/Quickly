"""Leads API routes."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from app.database import get_db
from app.models import Lead, CampaignLead, EmailLog, LeadReply
from app.schemas import LeadCreate, LeadUpdate, LeadResponse, MarkReplied
from app.queue_logic import reserve_slots_for_new_leads_bulk

router = APIRouter(prefix="/api/leads", tags=["leads"])


@router.get("", response_model=list[LeadResponse])
async def list_leads(
    status: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    q = select(Lead).order_by(Lead.id)
    if status:
        q = q.where(Lead.status == status)
    result = await db.execute(q)
    return result.scalars().all()


@router.post("", response_model=LeadResponse)
async def create_lead(data: LeadCreate, db: AsyncSession = Depends(get_db)):
    # Disallow creating standalone leads — leads must be added to a campaign.
    # Use POST /api/campaigns/{campaign_id}/leads to create + enroll leads.
    raise HTTPException(405, "Creating leads without a campaign is not allowed; use POST /api/campaigns/{campaign_id}/leads")


@router.get("/{lead_id}", response_model=LeadResponse)
async def get_lead(lead_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Lead).where(Lead.id == lead_id))
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(404, "Lead not found")
    return lead


@router.patch("/{lead_id}", response_model=LeadResponse)
async def update_lead(lead_id: int, data: LeadUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Lead).where(Lead.id == lead_id))
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(404, "Lead not found")
    if data.name is not None:
        lead.name = data.name
    if data.custom_data is not None:
        lead.custom_data = data.custom_data
    status_changed = False
    if data.status is not None and data.status != lead.status:
        old_status = lead.status
        lead.status = data.status
        status_changed = True
    await db.flush()

    # schedule impact: lead becoming unsubscribed/bounced/etc should free up
    # slots and allow other leads to move earlier.  likewise, re-activating a
    # lead should cause it to be re-scheduled.  easiest is to recalc all
    # campaigns so that capacity is redistributed correctly.
    if status_changed:
        from app.routers.calendar import recalculate_all_campaigns
        log = __import__("logging").getLogger("campaign_engine.routes")
        log.info(
            "Lead %s status changed (%s -> %s); triggering full recalculation",
            lead_id,
            old_status,
            data.status,
        )
        await recalculate_all_campaigns(db)

    await db.refresh(lead)
    return lead


@router.delete("/{lead_id}")
async def delete_lead(lead_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Lead).where(Lead.id == lead_id))
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(404, "Lead not found")
    # gather campaigns this lead belongs to prior to deletion
    cl_res = await db.execute(
        select(CampaignLead.campaign_id)
        .where(CampaignLead.lead_id == lead_id)
    )
    campaign_ids = [r[0] for r in cl_res.all()]
    await db.execute(delete(EmailLog).where(EmailLog.lead_id == lead_id))
    await db.execute(delete(LeadReply).where(LeadReply.lead_id == lead_id))
    await db.delete(lead)

    # capacity freed by deletion; recalc whole queue to allow other leads to
    # move earlier
    if campaign_ids:
        from app.routers.calendar import recalculate_all_campaigns
        log = __import__("logging").getLogger("campaign_engine.routes")
        log.info(
            "Lead %s deleted (campaigns=%s); triggering full recalculation",
            lead_id,
            campaign_ids,
        )
        await recalculate_all_campaigns(db)
    return {"ok": True}




@router.post("/mark-replied")
async def mark_lead_replied(
    body: MarkReplied,
    db: AsyncSession = Depends(get_db),
):
    from app.models import LeadReply
    lead_id = body.lead_id
    campaign_id = body.campaign_id
    existing = await db.execute(
        select(LeadReply).where(
            LeadReply.lead_id == lead_id,
            LeadReply.campaign_id == campaign_id,
        )
    )
    if existing.scalar_one_or_none():
        return {"ok": True}
    db.add(LeadReply(lead_id=lead_id, campaign_id=campaign_id))
    return {"ok": True}


@router.get("/{lead_id}/history")
async def get_lead_history(lead_id: int, db: AsyncSession = Depends(get_db)):
    from app.models import EmailLog, Campaign
    result = await db.execute(
        select(EmailLog, Campaign.name)
        .join(Campaign, EmailLog.campaign_id == Campaign.id)
        .where(EmailLog.lead_id == lead_id)
        .order_by(EmailLog.sent_at.desc())
    )
    rows = result.all()
    return [
        {
            "campaign_id": log.campaign_id,
            "campaign_name": name,
            "sequence_index": log.sequence_index,
            "sent_at": log.sent_at.isoformat(),
            "subject": log.subject,
        }
        for log, name in rows
    ]
