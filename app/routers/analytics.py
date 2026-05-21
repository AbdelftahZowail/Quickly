"""Analytics endpoints — aggregated daily statistics, no row limit."""
import logging
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import cast, Date, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Campaign, CampaignLead, EmailClick, EmailLog, EmailOpen, LeadReply

log = logging.getLogger("quickly.analytics")
router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/daily")
async def daily_analytics(
    start_date: str = Query(..., description="Inclusive start date YYYY-MM-DD"),
    end_date: str = Query(..., description="Inclusive end date YYYY-MM-DD"),
    campaign_id: Optional[List[int]] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Return per-day, per-campaign aggregated stats for the requested range.

    No row limit — result size is bounded by (days × campaigns).
    """
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
    except ValueError:
        raise HTTPException(status_code=400, detail="start_date and end_date must be YYYY-MM-DD")

    # campaign name lookup
    camp_stmt = select(Campaign.id, Campaign.name)
    if campaign_id:
        camp_stmt = camp_stmt.where(Campaign.id.in_(campaign_id))
    camp_rows = (await db.execute(camp_stmt)).all()
    campaign_names = {cid: name for cid, name in camp_rows}

    # sent per day per campaign
    sent_stmt = (
        select(
            cast(EmailLog.sent_at, Date).label("day"),
            EmailLog.campaign_id,
            func.count().label("sent"),
        )
        .where(EmailLog.sent_at >= start, EmailLog.sent_at < end)
        .group_by(cast(EmailLog.sent_at, Date), EmailLog.campaign_id)
    )
    if campaign_id:
        sent_stmt = sent_stmt.where(EmailLog.campaign_id.in_(campaign_id))

    # opens per day per campaign
    opens_stmt = (
        select(
            cast(EmailOpen.opened_at, Date).label("day"),
            EmailLog.campaign_id,
            func.count().label("total_opens"),
            func.count(func.distinct(EmailOpen.ip_address)).label("unique_opens"),
        )
        .join(EmailLog, EmailOpen.email_log_id == EmailLog.id)
        .where(EmailOpen.opened_at >= start, EmailOpen.opened_at < end)
        .group_by(cast(EmailOpen.opened_at, Date), EmailLog.campaign_id)
    )
    if campaign_id:
        opens_stmt = opens_stmt.where(EmailLog.campaign_id.in_(campaign_id))

    # clicks per day per campaign
    clicks_stmt = (
        select(
            cast(EmailClick.clicked_at, Date).label("day"),
            EmailLog.campaign_id,
            func.count().label("total_clicks"),
            func.count(func.distinct(EmailClick.ip_address)).label("unique_clicks"),
        )
        .join(EmailLog, EmailClick.email_log_id == EmailLog.id)
        .where(EmailClick.clicked_at >= start, EmailClick.clicked_at < end)
        .group_by(cast(EmailClick.clicked_at, Date), EmailLog.campaign_id)
    )
    if campaign_id:
        clicks_stmt = clicks_stmt.where(EmailLog.campaign_id.in_(campaign_id))

    # replies per day per campaign (excluding OOO and auto-reply)
    replies_stmt = (
        select(
            cast(LeadReply.replied_at, Date).label("day"),
            LeadReply.campaign_id,
            func.count().label("total_replies"),
        )
        .join(
            CampaignLead,
            (LeadReply.lead_id == CampaignLead.lead_id)
            & (LeadReply.campaign_id == CampaignLead.campaign_id),
        )
        .where(
            LeadReply.replied_at >= start,
            LeadReply.replied_at < end,
            CampaignLead.interest_status.notin_(["out_of_office", "auto_reply"]),
        )
        .group_by(cast(LeadReply.replied_at, Date), LeadReply.campaign_id)
    )
    if campaign_id:
        replies_stmt = replies_stmt.where(LeadReply.campaign_id.in_(campaign_id))

    sent_rows    = (await db.execute(sent_stmt)).all()
    opens_rows   = (await db.execute(opens_stmt)).all()
    clicks_rows  = (await db.execute(clicks_stmt)).all()
    replies_rows = (await db.execute(replies_stmt)).all()

    result: dict[tuple, dict] = {}

    def _day_str(day) -> str:
        return day.isoformat() if hasattr(day, "isoformat") else str(day)

    def _ensure(day, cid: int) -> dict:
        k = (_day_str(day), int(cid))
        if k not in result:
            result[k] = {
                "date": k[0],
                "campaign_id": k[1],
                "campaign_name": campaign_names.get(k[1], ""),
                "sent": 0,
                "total_opens": 0,
                "unique_opens": 0,
                "total_clicks": 0,
                "unique_clicks": 0,
                "total_replies": 0,
            }
        return result[k]

    for row in sent_rows:
        _ensure(row.day, row.campaign_id)["sent"] = row.sent
    for row in opens_rows:
        entry = _ensure(row.day, row.campaign_id)
        entry["total_opens"] = row.total_opens
        entry["unique_opens"] = row.unique_opens
    for row in clicks_rows:
        entry = _ensure(row.day, row.campaign_id)
        entry["total_clicks"] = row.total_clicks
        entry["unique_clicks"] = row.unique_clicks
    for row in replies_rows:
        _ensure(row.day, row.campaign_id)["total_replies"] = row.total_replies

    return sorted(result.values(), key=lambda x: (x["date"], x["campaign_id"]))
