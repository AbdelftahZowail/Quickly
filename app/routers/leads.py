"""Leads API routes."""
import asyncio
import csv
import io
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Campaign, CampaignLead, EmailLog, Lead, LeadReply
from app.schemas import (
    LeadBulkDeleteRequest,
    LeadBulkRecoverItem,
    LeadBulkRecoverRequest,
    LeadBulkStatusRequest,
    LeadCreate,
    LeadRecoverRequest,
    LeadResponse,
    LeadUpdate,
    LeadCampaignInfo,
    MarkReplied,
)

log = logging.getLogger("quickly.routes")

router = APIRouter(prefix="/api/leads", tags=["leads"])


def _lead_to_response(lead: Lead) -> LeadResponse:
    d = LeadResponse.model_validate(lead)
    d.campaigns = [
        LeadCampaignInfo(
            campaign_id=cl.campaign_id,
            campaign_name=cl.campaign.name,
            enrolled_at=cl.enrolled_at,
            interest_status=cl.interest_status,
            sending_paused=cl.sending_paused,
        )
        for cl in lead.campaign_leads
    ]
    return d


def _lead_query_with_campaigns():
    return select(Lead).options(
        selectinload(Lead.campaign_leads).selectinload(CampaignLead.campaign),
    )


def _build_leads_stmt(
    *,
    q: str | None,
    status: str | None,
    bad_only: bool,
):
    stmt = _lead_query_with_campaigns().order_by(Lead.id)
    if bad_only:
        stmt = stmt.where(Lead.status.in_(["bounced", "invalid"]))
    elif status:
        stmt = stmt.where(Lead.status == status)
    if q and q.strip():
        pat = f"%{q.strip()}%"
        stmt = stmt.where(or_(Lead.email.ilike(pat), Lead.name.ilike(pat)))
    return stmt


def _enrolled_earliest_iso(lead: Lead) -> str:
    if not lead.campaign_leads:
        return ""
    dates = [cl.enrolled_at for cl in lead.campaign_leads if cl.enrolled_at]
    if not dates:
        return ""
    earliest = min(dates)
    if isinstance(earliest, datetime):
        return earliest.date().isoformat()
    return str(earliest)


async def _mutate_lead_recover(lead: Lead, norm: str, verify: bool) -> None:
    from app.email_verification import PENDING

    lead.email = norm
    lead.status = "active"
    lead.provider = None
    if verify:
        lead.email_verification_status = PENDING
    else:
        lead.email_verification_status = None


async def _finalize_lead_recovery(db: AsyncSession, lead_ids: list[int], verify: bool) -> None:
    if not lead_ids:
        return
    if verify:
        from app.routers.campaigns import _run_background_verification

        asyncio.create_task(_run_background_verification(lead_ids))
    else:
        from app.routers.schedule import recalculate_all_campaigns

        await recalculate_all_campaigns(db)


@router.get("", response_model=list[LeadResponse])
async def list_leads(
    status: str | None = Query(None),
    bad_only: bool = Query(
        False,
        description="If true, only bounced and invalid leads (ignores single status filter).",
    ),
    q: str | None = Query(None, description="Search email or name (substring)"),
    db: AsyncSession = Depends(get_db),
):
    stmt = _build_leads_stmt(q=q, status=status, bad_only=bad_only)
    result = await db.execute(stmt)
    leads = result.scalars().all()
    return [_lead_to_response(lead) for lead in leads]


@router.get("/export")
async def export_leads_csv(
    status: str | None = Query(None),
    bad_only: bool = Query(False),
    q: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """CSV export aligned with the Leads UI: core columns plus all custom_data keys."""
    stmt = _build_leads_stmt(q=q, status=status, bad_only=bad_only)
    result = await db.execute(stmt)
    leads = list(result.scalars().all())

    custom_keys: set[str] = set()
    for lead in leads:
        cd = lead.custom_data
        if isinstance(cd, dict):
            custom_keys.update(cd.keys())
    sorted_custom = sorted(custom_keys)

    output = io.StringIO()
    writer = csv.writer(output)
    header = [
        "id",
        "email",
        "name",
        "status",
        "email_verification_status",
        "campaigns",
        "enrolled_earliest",
        *sorted_custom,
    ]
    writer.writerow(header)

    for lead in leads:
        camps = "; ".join(cl.campaign.name for cl in lead.campaign_leads if cl.campaign)
        row = [
            lead.id,
            lead.email or "",
            lead.name or "",
            lead.status or "",
            lead.email_verification_status or "",
            camps,
            _enrolled_earliest_iso(lead),
        ]
        cd = lead.custom_data if isinstance(lead.custom_data, dict) else {}
        for k in sorted_custom:
            val = cd.get(k, "")
            if val is None:
                row.append("")
            elif isinstance(val, (dict, list)):
                row.append(json.dumps(val, ensure_ascii=False))
            else:
                row.append(str(val))
        writer.writerow(row)

    output.seek(0)
    suffix = "bounced_invalid" if bad_only else "export"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="leads_{suffix}.csv"'},
    )


@router.post("/bulk-delete")
async def bulk_delete_leads(
    body: LeadBulkDeleteRequest,
    db: AsyncSession = Depends(get_db),
):
    if not body.lead_ids:
        return {"ok": True, "deleted": 0}
    all_campaign_ids: set[int] = set()
    deleted = 0
    for lead_id in body.lead_ids:
        res = await db.execute(select(Lead).where(Lead.id == lead_id))
        lead = res.scalar_one_or_none()
        if not lead:
            continue
        cl_res = await db.execute(
            select(CampaignLead.campaign_id).where(CampaignLead.lead_id == lead_id),
        )
        for (cid,) in cl_res.all():
            all_campaign_ids.add(cid)
        await db.execute(delete(EmailLog).where(EmailLog.lead_id == lead_id))
        await db.execute(delete(LeadReply).where(LeadReply.lead_id == lead_id))
        await db.delete(lead)
        deleted += 1

    if all_campaign_ids:
        from app.routers.schedule import recalculate_all_campaigns

        log.info(
            "bulk_delete_leads: deleted %d lead(s); recalc (campaigns touched)",
            deleted,
        )
        await recalculate_all_campaigns(db)
    return {"ok": True, "deleted": deleted}


@router.post("/bulk-status")
async def bulk_update_lead_status(
    body: LeadBulkStatusRequest,
    db: AsyncSession = Depends(get_db),
):
    if not body.lead_ids:
        return {"ok": True, "updated": 0}
    changed = False
    updated = 0
    for lead_id in body.lead_ids:
        res = await db.execute(select(Lead).where(Lead.id == lead_id))
        lead = res.scalar_one_or_none()
        if not lead:
            continue
        if lead.status != body.status:
            lead.status = body.status
            changed = True
            updated += 1
    await db.flush()
    if changed:
        from app.routers.schedule import recalculate_all_campaigns

        await recalculate_all_campaigns(db)
    return {"ok": True, "updated": updated}


@router.post("/bulk-recover")
async def bulk_recover_leads(
    body: LeadBulkRecoverRequest,
    db: AsyncSession = Depends(get_db),
):
    """Recover many leads: one verification batch or one queue recalculation."""
    if not body.items:
        return {"recovered": 0, "errors": [], "recovered_ids": []}

    from app.app_settings import EMAIL_VERIFICATION_ENABLED, get_setting

    enabled = (await get_setting(db, EMAIL_VERIFICATION_ENABLED) or "false").lower() in (
        "true",
        "1",
        "yes",
    )
    verify = body.verify_email and enabled

    errors: list[dict] = []
    recovered_ids: list[int] = []

    for item in body.items:
        lead_id = item.lead_id
        res = await db.execute(
            _lead_query_with_campaigns().where(Lead.id == lead_id),
        )
        lead = res.scalar_one_or_none()
        if not lead:
            errors.append({"lead_id": lead_id, "detail": "not_found"})
            continue
        norm = item.email.strip().lower()
        if not norm:
            errors.append({"lead_id": lead_id, "detail": "empty_email"})
            continue
        dup = await db.execute(
            select(Lead.id).where(Lead.email == norm, Lead.id != lead_id),
        )
        if dup.scalar_one_or_none():
            errors.append({"lead_id": lead_id, "detail": "duplicate_email"})
            continue
        await _mutate_lead_recover(lead, norm, verify)
        recovered_ids.append(lead_id)

    await db.flush()
    await _finalize_lead_recovery(db, recovered_ids, verify)
    return {"recovered": len(recovered_ids), "errors": errors, "recovered_ids": recovered_ids}


@router.post("/recover-import")
async def import_recover_csv(
    file: UploadFile = File(...),
    verify_emails: bool = Query(True),
    db: AsyncSession = Depends(get_db),
):
    """CSV with id + email columns (and optional extra columns). Uses same recovery rules as bulk-recover."""
    raw = await file.read()
    text = raw.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        raise HTTPException(400, "Empty CSV")

    header = [h.strip().lower() for h in rows[0]]
    id_idx = None
    email_idx = None
    if "id" in header or "lead_id" in header:
        try:
            id_idx = header.index("id") if "id" in header else header.index("lead_id")
        except ValueError:
            id_idx = None
        try:
            email_idx = header.index("email")
        except ValueError:
            email_idx = None
        data_rows = rows[1:]
    else:
        id_idx, email_idx = 0, 1
        data_rows = rows

    if id_idx is None or email_idx is None:
        raise HTTPException(400, "CSV must include id and email columns (header row or first two columns)")

    items: list[LeadBulkRecoverItem] = []
    for parts in data_rows:
        if len(parts) <= max(id_idx, email_idx):
            continue
        try:
            lid = int(str(parts[id_idx]).strip())
        except ValueError:
            continue
        em = str(parts[email_idx]).strip().strip('"')
        if lid and em:
            items.append(LeadBulkRecoverItem(lead_id=lid, email=em))

    if not items:
        raise HTTPException(400, "No valid id,email rows found")

    bulk_body = LeadBulkRecoverRequest(items=items, verify_email=verify_emails)
    return await bulk_recover_leads(bulk_body, db)


@router.post("", response_model=LeadResponse)
async def create_lead(data: LeadCreate, db: AsyncSession = Depends(get_db)):
    raise HTTPException(
        405,
        "Creating leads without a campaign is not allowed; use POST /api/campaigns/{campaign_id}/leads",
    )


@router.post("/mark-replied")
async def mark_lead_replied(
    body: MarkReplied,
    db: AsyncSession = Depends(get_db),
):
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


@router.get("/{lead_id}", response_model=LeadResponse)
async def get_lead(lead_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        _lead_query_with_campaigns().where(Lead.id == lead_id),
    )
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(404, "Lead not found")
    return _lead_to_response(lead)


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

    if status_changed:
        from app.routers.schedule import recalculate_all_campaigns

        log.info(
            "Lead %s status changed (%s -> %s); triggering full recalculation",
            lead_id,
            old_status,
            data.status,
        )
        await recalculate_all_campaigns(db)

    result2 = await db.execute(
        _lead_query_with_campaigns().where(Lead.id == lead_id),
    )
    lead_loaded = result2.scalar_one()
    return _lead_to_response(lead_loaded)


@router.post("/{lead_id}/recover", response_model=LeadResponse)
async def recover_lead(
    lead_id: int,
    body: LeadRecoverRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        _lead_query_with_campaigns().where(Lead.id == lead_id),
    )
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(404, "Lead not found")

    norm = body.email.strip().lower()
    if not norm:
        raise HTTPException(400, "Email is required")

    dup = await db.execute(
        select(Lead.id).where(Lead.email == norm, Lead.id != lead_id),
    )
    if dup.scalar_one_or_none():
        raise HTTPException(409, "Another lead already uses this email address")

    from app.app_settings import EMAIL_VERIFICATION_ENABLED, get_setting

    enabled = (await get_setting(db, EMAIL_VERIFICATION_ENABLED) or "false").lower() in (
        "true",
        "1",
        "yes",
    )
    verify = body.verify_email and enabled

    await _mutate_lead_recover(lead, norm, verify)
    await db.flush()
    await _finalize_lead_recovery(db, [lead_id], verify)

    result3 = await db.execute(
        _lead_query_with_campaigns().where(Lead.id == lead_id),
    )
    lead_out = result3.scalar_one()
    return _lead_to_response(lead_out)


@router.delete("/{lead_id}")
async def delete_lead(lead_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Lead).where(Lead.id == lead_id))
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(404, "Lead not found")
    cl_res = await db.execute(
        select(CampaignLead.campaign_id).where(CampaignLead.lead_id == lead_id),
    )
    campaign_ids = [r[0] for r in cl_res.all()]
    await db.execute(delete(EmailLog).where(EmailLog.lead_id == lead_id))
    await db.execute(delete(LeadReply).where(LeadReply.lead_id == lead_id))
    await db.delete(lead)

    if campaign_ids:
        from app.routers.schedule import recalculate_all_campaigns

        log.info(
            "Lead %s deleted (campaigns=%s); triggering full recalculation",
            lead_id,
            campaign_ids,
        )
        await recalculate_all_campaigns(db)
    return {"ok": True}


@router.get("/{lead_id}/history")
async def get_lead_history(lead_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(EmailLog, Campaign.name)
        .join(Campaign, EmailLog.campaign_id == Campaign.id)
        .where(EmailLog.lead_id == lead_id)
        .order_by(EmailLog.sent_at.desc()),
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


@router.get("/{lead_id}/replies")
async def get_lead_replies(lead_id: int, db: AsyncSession = Depends(get_db)):
    exists = await db.execute(select(Lead.id).where(Lead.id == lead_id))
    if not exists.scalar_one_or_none():
        raise HTTPException(404, "Lead not found")
    result = await db.execute(
        select(LeadReply, Campaign.name)
        .join(Campaign, LeadReply.campaign_id == Campaign.id)
        .where(LeadReply.lead_id == lead_id)
        .order_by(LeadReply.replied_at.desc()),
    )
    return [
        {
            "campaign_id": lr.campaign_id,
            "campaign_name": name,
            "replied_at": lr.replied_at.isoformat(),
        }
        for lr, name in result.all()
    ]
