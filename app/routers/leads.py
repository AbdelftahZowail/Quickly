"""Leads API routes."""
import asyncio
import csv
import io
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.campaign_lead_status import normalize_enrollment_status, normalize_interest
from app.database import get_db
from app.models import (
    Campaign,
    CampaignLead,
    EmailLog,
    GmailMessage,
    Lead,
    LeadReply,
    Office365Message,
)
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


async def _engagement_pair_sets(
    db: AsyncSession, lead_ids: list[int]
) -> tuple[set[tuple[int, int]], set[tuple[int, int]], set[tuple[int, int]]]:
    """(lead_id, campaign_id) sets for opened, clicked, replied."""
    if not lead_ids:
        return set(), set(), set()
    o_res = await db.execute(
        select(EmailLog.lead_id, EmailLog.campaign_id)
        .where(EmailLog.lead_id.in_(lead_ids), EmailLog.opened == True)  # noqa: E712
        .distinct()
    )
    opened = {(r[0], r[1]) for r in o_res.all()}
    c_res = await db.execute(
        select(EmailLog.lead_id, EmailLog.campaign_id)
        .where(EmailLog.lead_id.in_(lead_ids), EmailLog.clicked == True)  # noqa: E712
        .distinct()
    )
    clicked = {(r[0], r[1]) for r in c_res.all()}
    r_res = await db.execute(
        select(LeadReply.lead_id, LeadReply.campaign_id)
        .where(LeadReply.lead_id.in_(lead_ids))
        .distinct()
    )
    replied = {(r[0], r[1]) for r in r_res.all()}
    return opened, clicked, replied


def _lead_to_response(
    lead: Lead,
    opened: set[tuple[int, int]],
    clicked: set[tuple[int, int]],
    replied: set[tuple[int, int]],
    *,
    interactions: list[dict] | None = None,
) -> LeadResponse:
    camps = []
    for cl in lead.campaign_leads:
        c = cl.campaign
        pair = (lead.id, cl.campaign_id)
        camps.append(
            LeadCampaignInfo(
                campaign_id=cl.campaign_id,
                campaign_public_id=c.public_id if c else "",
                campaign_name=c.name if c else "",
                enrolled_at=cl.enrolled_at,
                status=getattr(cl, "enrollment_status", None) or "active",
                interest=cl.interest_status,
                opened=pair in opened,
                clicked=pair in clicked,
                replied=pair in replied,
                sending_paused=cl.sending_paused,
            )
        )
    return LeadResponse(
        id=lead.id,
        email=lead.email,
        name=lead.name or "",
        custom_data=lead.custom_data if isinstance(lead.custom_data, dict) else {},
        provider=lead.provider,
        email_verification_status=lead.email_verification_status,
        created_at=lead.created_at,
        campaigns=camps,
        interactions=interactions or [],
    )


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
        bounced_enrollment = exists(
            select(1).select_from(CampaignLead).where(
                CampaignLead.lead_id == Lead.id,
                CampaignLead.enrollment_status == "bounced",
            )
        )
        stmt = stmt.where(
            or_(
                Lead.email_verification_status.in_(("invalid", "risky")),
                Lead.status.in_(("invalid", "bounced")),
                bounced_enrollment,
            )
        )
    elif status:
        st = status.strip().lower()
        if st == "invalid":
            stmt = stmt.where(
                or_(
                    Lead.email_verification_status == "invalid",
                    Lead.status == "invalid",
                )
            )
        elif st == "replied":
            stmt = stmt.where(
                exists(select(1).select_from(LeadReply).where(LeadReply.lead_id == Lead.id))
            )
        else:
            stmt = stmt.where(
                exists(
                    select(1).select_from(CampaignLead).where(
                        CampaignLead.lead_id == Lead.id,
                        CampaignLead.enrollment_status == st,
                    )
                )
            )
    if q and q.strip():
        pat = f"%{q.strip()}%"
        stmt = stmt.where(or_(Lead.email.ilike(pat), Lead.name.ilike(pat)))
    return stmt


async def _reset_all_enrollments_for_lead(db: AsyncSession, lead_id: int) -> None:
    res = await db.execute(select(CampaignLead).where(CampaignLead.lead_id == lead_id))
    for cl in res.scalars().all():
        cl.enrollment_status = "active"
        cl.interest_status = None
        cl.sending_paused = False


async def _fetch_lead_interactions(db: AsyncSession, lead_id: int) -> list[dict]:
    """Merge outbound EmailLog rows with inbound mirrored messages (Gmail / O365)."""
    res = await db.execute(select(Lead).where(Lead.id == lead_id))
    lead = res.scalar_one_or_none()
    if not lead:
        return []

    events: list[dict] = []

    log_rows = await db.execute(
        select(EmailLog, Campaign.name, Campaign.public_id)
        .join(Campaign, EmailLog.campaign_id == Campaign.id)
        .where(EmailLog.lead_id == lead_id)
        .order_by(EmailLog.sent_at.asc())
    )
    for el, cname, pub in log_rows.all():
        events.append(
            {
                "direction": "outbound",
                "kind": "sent",
                "at": el.sent_at.isoformat(),
                "campaign_id": el.campaign_id,
                "campaign_name": cname,
                "campaign_public_id": pub,
                "subject": el.subject or "",
                "sequence_index": el.sequence_index,
            }
        )

    th_rows = await db.execute(
        select(EmailLog.thread_id)
        .where(
            EmailLog.lead_id == lead_id,
            EmailLog.thread_id.isnot(None),
            EmailLog.thread_id != "",
        )
        .distinct()
    )
    thread_ids = [r[0] for r in th_rows.all()]
    lead_em = (lead.email or "").strip().lower()

    if thread_ids and lead_em:
        from app.unibox import _extract_email_only, _header_value

        gm_res = await db.execute(
            select(GmailMessage).where(GmailMessage.thread_id.in_(thread_ids))
        )
        for msg in gm_res.scalars().all():
            try:
                from_a = _extract_email_only(_header_value(msg.headers_json or "[]", "From"))
            except Exception:
                from_a = ""
            if (from_a or "").strip().lower() != lead_em:
                continue
            subj = _header_value(msg.headers_json or "[]", "Subject") or ""
            ts = None
            if msg.internal_date is not None:
                ts = datetime.utcfromtimestamp(int(msg.internal_date) / 1000.0).isoformat() + "Z"
            events.append(
                {
                    "direction": "inbound",
                    "kind": "received",
                    "channel": "gmail",
                    "at": ts or (msg.updated_at.isoformat() if msg.updated_at else None),
                    "thread_id": msg.thread_id,
                    "subject": subj,
                    "snippet": (msg.snippet or "")[:500],
                }
            )

        o365_res = await db.execute(
            select(Office365Message).where(Office365Message.conversation_id.in_(thread_ids))
        )
        for msg in o365_res.scalars().all():
            if (msg.from_address or "").strip().lower() != lead_em:
                continue
            events.append(
                {
                    "direction": "inbound",
                    "kind": "received",
                    "channel": "office365",
                    "at": msg.received_at.isoformat() if msg.received_at else None,
                    "thread_id": msg.conversation_id,
                    "subject": msg.subject or "",
                    "snippet": (msg.body_plain or msg.body_html or "")[:500],
                }
            )

    lr_rows = await db.execute(
        select(LeadReply, Campaign.name, Campaign.public_id)
        .join(Campaign, LeadReply.campaign_id == Campaign.id)
        .where(LeadReply.lead_id == lead_id)
        .order_by(LeadReply.replied_at.asc())
    )
    for lr, cname, pub in lr_rows.all():
        events.append(
            {
                "direction": "inbound",
                "kind": "reply_marker",
                "at": lr.replied_at.isoformat(),
                "campaign_id": lr.campaign_id,
                "campaign_name": cname,
                "campaign_public_id": pub,
            }
        )

    events.sort(key=lambda e: (e.get("at") or "", e.get("direction", "")))
    return events


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


async def _after_lead_recovered(db: AsyncSession, lead_id: int) -> None:
    await _reset_all_enrollments_for_lead(db, lead_id)


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
    ids = [x.id for x in leads]
    opened, clicked, replied = await _engagement_pair_sets(db, ids)
    return [_lead_to_response(x, opened, clicked, replied) for x in leads]


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
    ids = [x.id for x in leads]
    opened, clicked, replied = await _engagement_pair_sets(db, ids)

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
        "email_verification_status",
        "campaigns",
        "enrollments_json",
        "enrolled_earliest",
        *sorted_custom,
    ]
    writer.writerow(header)

    for lead in leads:
        camps = "; ".join(cl.campaign.name for cl in lead.campaign_leads if cl.campaign)
        enc_payload = []
        for cl in lead.campaign_leads:
            c = cl.campaign
            pair = (lead.id, cl.campaign_id)
            enc_payload.append(
                {
                    "campaign_id": cl.campaign_id,
                    "campaign_public_id": c.public_id if c else "",
                    "campaign_name": c.name if c else "",
                    "status": getattr(cl, "enrollment_status", None) or "active",
                    "interest": cl.interest_status,
                    "opened": pair in opened,
                    "clicked": pair in clicked,
                    "replied": pair in replied,
                    "sending_paused": cl.sending_paused,
                }
            )
        row = [
            lead.id,
            lead.email or "",
            lead.name or "",
            lead.email_verification_status or "",
            camps,
            json.dumps(enc_payload, ensure_ascii=False),
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
    target = normalize_enrollment_status(body.enrollment_status)
    changed = False
    updated = 0
    for lead_id in body.lead_ids:
        res = await db.execute(select(CampaignLead).where(CampaignLead.lead_id == lead_id))
        for cl in res.scalars().all():
            if cl.enrollment_status != target:
                cl.enrollment_status = target
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
        await _after_lead_recovered(db, lead_id)
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
    opened, clicked, replied = await _engagement_pair_sets(db, [lead_id])
    interactions = await _fetch_lead_interactions(db, lead_id)
    return _lead_to_response(lead, opened, clicked, replied, interactions=interactions)


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
    enrollment_changed = False
    if data.enrollment_status is not None:
        target = normalize_enrollment_status(data.enrollment_status)
        cl_res = await db.execute(select(CampaignLead).where(CampaignLead.lead_id == lead_id))
        for cl in cl_res.scalars().all():
            if cl.enrollment_status != target:
                cl.enrollment_status = target
                enrollment_changed = True
    await db.flush()

    if enrollment_changed:
        from app.routers.schedule import recalculate_all_campaigns

        log.info(
            "Lead %s enrollment_status bulk-updated; triggering full recalculation",
            lead_id,
        )
        await recalculate_all_campaigns(db)

    result2 = await db.execute(
        _lead_query_with_campaigns().where(Lead.id == lead_id),
    )
    lead_loaded = result2.scalar_one()
    opened, clicked, replied = await _engagement_pair_sets(db, [lead_id])
    return _lead_to_response(lead_loaded, opened, clicked, replied)


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
    await _after_lead_recovered(db, lead_id)
    await db.flush()
    await _finalize_lead_recovery(db, [lead_id], verify)

    result3 = await db.execute(
        _lead_query_with_campaigns().where(Lead.id == lead_id),
    )
    lead_out = result3.scalar_one()
    opened, clicked, replied = await _engagement_pair_sets(db, [lead_id])
    return _lead_to_response(lead_out, opened, clicked, replied)


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
