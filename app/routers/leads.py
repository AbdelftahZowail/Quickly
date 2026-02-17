"""Leads API routes."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from app.database import get_db
from app.models import Lead, CampaignLead, EmailLog, LeadReply
from app.schemas import LeadCreate, LeadUpdate, LeadResponse, MarkReplied
from app.queue_logic import reserve_slots_for_new_lead

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
    lead = Lead(
        email=data.email,
        name=data.name,
        custom_data=data.custom_data or {},
    )
    db.add(lead)
    await db.flush()
    if data.campaign_id:
        cl = CampaignLead(campaign_id=data.campaign_id, lead_id=lead.id)
        db.add(cl)
        await db.flush()
        await reserve_slots_for_new_lead(db, cl.id, data.campaign_id)
    await db.refresh(lead)
    return lead


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
    if data.status is not None:
        lead.status = data.status
    await db.flush()
    await db.refresh(lead)
    return lead


@router.delete("/{lead_id}")
async def delete_lead(lead_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Lead).where(Lead.id == lead_id))
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(404, "Lead not found")
    await db.execute(delete(EmailLog).where(EmailLog.lead_id == lead_id))
    await db.execute(delete(LeadReply).where(LeadReply.lead_id == lead_id))
    await db.delete(lead)
    return {"ok": True}


@router.post("/{lead_id}/campaigns/{campaign_id}")
async def add_lead_to_campaign(
    lead_id: int, campaign_id: int, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Lead).where(Lead.id == lead_id))
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(404, "Lead not found")
    existing = await db.execute(
        select(CampaignLead).where(
            CampaignLead.lead_id == lead_id,
            CampaignLead.campaign_id == campaign_id,
        )
    )
    if existing.scalar_one_or_none():
        return {"ok": True, "message": "Already in campaign"}
    cl = CampaignLead(campaign_id=campaign_id, lead_id=lead_id)
    db.add(cl)
    await db.flush()
    await reserve_slots_for_new_lead(db, cl.id, campaign_id)
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
