"""Read-only aggregation of local usage into the telemetry payload shape.

Everything is computed from this instance's own tables — no external calls.
Where the app stores current state rather than timestamped events, the value is
either derived by joining the event's own timestamp or sent as ``null`` rather
than guessed.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    APIKey,
    Campaign,
    CampaignLead,
    EmailClick,
    EmailLog,
    EmailOpen,
    Inbox,
    Lead,
    LeadReply,
    QueueSlot,
    Sequence,
    SequenceVariant,
    User,
    Webhook,
)
from app.settings_manager import settings

# interest_status values that are not real replies for reporting (matches
# /api/analytics/daily).
_NON_REPLY_INTERESTS = ("out_of_office", "auto_reply")


async def _scalar(db: AsyncSession, stmt, default=0):
    value = (await db.execute(stmt)).scalar()
    return default if value is None else value


async def _count(db: AsyncSession, model) -> int:
    return int(await _scalar(db, select(func.count()).select_from(model)))


async def _exists(db: AsyncSession, model) -> bool:
    return bool(await _count(db, model))


async def _min_dt(db: AsyncSession, column):
    return await _scalar(db, select(func.min(column)), None)


def _iso(value: datetime) -> str:
    return value.isoformat() + "Z"


async def heartbeat_stats(db: AsyncSession) -> dict:
    first_user = await _min_dt(db, User.created_at)
    uptime_days = max(0, (datetime.utcnow() - first_user).days) if first_user else 0
    return {
        "leads_count": await _count(db, Lead),
        "campaigns_count": await _count(db, Campaign),
        "inboxes_count": await _count(db, Inbox),
        "uptime_days": uptime_days,
    }


async def _daily_counts(db: AsyncSession, column, start: datetime, end: datetime) -> dict:
    rows = (
        await db.execute(
            select(func.date(column).label("day"), func.count().label("n"))
            .where(column >= start, column < end)
            .group_by(func.date(column))
        )
    ).all()
    # PostgreSQL returns datetime.date here; SQLite's CAST(... AS DATE) returns
    # a string. Normalize both forms so the same payload is produced in tests,
    # development, and production.
    return {
        (row.day if isinstance(row.day, date) else datetime.fromisoformat(str(row.day)).date()): int(row.n)
        for row in rows
    }


async def daily_counters(db: AsyncSession, days: int = 7) -> list[dict]:
    """Per-day outcome counters for the last *days* days (today included)."""
    today = datetime.now(timezone.utc).date()
    start = datetime.combine(today - timedelta(days=days - 1), datetime.min.time())
    end = datetime.combine(today + timedelta(days=1), datetime.min.time())

    sent = await _daily_counts(db, EmailLog.sent_at, start, end)
    opened = await _daily_counts(db, EmailOpen.opened_at, start, end)
    clicked = await _daily_counts(db, EmailClick.clicked_at, start, end)

    # Replies per day, plus how many of those leads currently carry each AI
    # label. The label is current state, not a historical event, so this reads
    # as "replies that day whose lead is now labelled X".
    reply_rows = (
        await db.execute(
            select(
                func.date(LeadReply.replied_at).label("day"),
                func.count().label("total"),
                func.count()
                .filter(CampaignLead.interest_status == "interested")
                .label("positive"),
                func.count()
                .filter(CampaignLead.interest_status == "not_interested")
                .label("negative"),
            )
            .join(
                CampaignLead,
                (LeadReply.lead_id == CampaignLead.lead_id)
                & (LeadReply.campaign_id == CampaignLead.campaign_id),
            )
            .where(
                LeadReply.replied_at >= start,
                LeadReply.replied_at < end,
                CampaignLead.interest_status.notin_(_NON_REPLY_INTERESTS),
            )
            .group_by(func.date(LeadReply.replied_at))
        )
    ).all()
    def _day(value) -> date:
        return value if isinstance(value, date) else datetime.fromisoformat(str(value)).date()

    replies = {_day(row.day): int(row.total) for row in reply_rows}
    positive = {_day(row.day): int(row.positive) for row in reply_rows}
    negative = {_day(row.day): int(row.negative) for row in reply_rows}

    classifier_on = bool(
        await _scalar(
            db,
            select(func.count())
            .select_from(CampaignLead)
            .where(CampaignLead.interest_status.is_not(None)),
            0,
        )
    )

    rows: list[dict] = []
    day = today - timedelta(days=days - 1)
    while day <= today:
        rows.append(
            {
                "date": day.isoformat(),
                "emails_sent": sent.get(day, 0),
                "opened": opened.get(day, 0),
                "clicked": clicked.get(day, 0),
                "replies": replies.get(day, 0),
                "replies_positive": positive.get(day, 0) if classifier_on else None,
                "replies_negative": negative.get(day, 0) if classifier_on else None,
                # The app flips enrollment_status with no timestamp, so a
                # per-day bounce/unsubscribe count cannot be derived honestly.
                "bounced": None,
                "unsubscribes": None,
                # No per-day send-failure counter exists yet.
                "send_failures": None,
            }
        )
        day += timedelta(days=1)
    return rows


async def activation_events(db: AsyncSession) -> list[dict]:
    """One-time milestones. Re-sent each payload; the hub de-duplicates."""
    events: list[dict] = []

    def add(name: str, occurred_at, provider: str | None = None) -> None:
        if not occurred_at:
            return
        event = {"name": name, "occurred_at": _iso(occurred_at)}
        if provider:
            event["provider"] = provider
        events.append(event)

    add("setup_completed", await _min_dt(db, User.created_at))

    first_inbox = (
        await db.execute(
            select(Inbox.created_at, Inbox.provider).order_by(Inbox.created_at).limit(1)
        )
    ).first()
    if first_inbox:
        add("first_inbox_connected", first_inbox.created_at, first_inbox.provider)

    add("first_lead_imported", await _min_dt(db, Lead.created_at))
    add("first_campaign_created", await _min_dt(db, Campaign.created_at))
    add("first_email_sent", await _min_dt(db, EmailLog.sent_at))
    add("first_reply_received", await _min_dt(db, LeadReply.replied_at))

    first_classified = await _scalar(
        db,
        select(func.min(LeadReply.replied_at))
        .join(
            CampaignLead,
            (LeadReply.lead_id == CampaignLead.lead_id)
            & (LeadReply.campaign_id == CampaignLead.campaign_id),
        )
        .where(CampaignLead.interest_status.is_not(None)),
        None,
    )
    add("first_ai_classification", first_classified)

    add("first_webhook", await _min_dt(db, Webhook.created_at))
    return events


async def feature_flags(db: AsyncSession) -> list[dict]:
    flags: list[dict] = []

    def add(name: str, used: bool) -> None:
        if used:
            flags.append({"name": name, "used": True})

    classifier_used = bool(
        await _scalar(
            db,
            select(func.count())
            .select_from(CampaignLead)
            .where(CampaignLead.interest_status.is_not(None)),
            0,
        )
    )
    add("ai_classifier", classifier_used)
    add("sequences", await _exists(db, Sequence))
    add("ab_variants", await _exists(db, SequenceVariant))
    add("scheduling", await _exists(db, QueueSlot))
    add("test_mode", bool(settings.test_mode))
    add("webhooks", await _exists(db, Webhook))
    add("api_keys", await _exists(db, APIKey))
    return flags
