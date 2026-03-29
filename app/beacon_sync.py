"""Rebuild Beacon registration rows from Quickly DB (opens, clicks, unsubscribes)."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.beacon_client import register_beacon_mappings_batched
from app.models import EmailLog, Inbox, LeadUnsubscribeToken

log = logging.getLogger("quickly.beacon_sync")


async def collect_inbox_beacon_items(session: AsyncSession, inbox_id: int) -> list[dict[str, Any]]:
    """All tracking tokens for this inbox from EmailLog + TrackedLink + LeadUnsubscribeToken."""
    result = await session.execute(
        select(EmailLog)
        .where(EmailLog.inbox_id == inbox_id)
        .options(selectinload(EmailLog.tracked_links))
    )
    logs = result.scalars().unique().all()
    if not logs:
        return []

    pairs = {(lg.lead_id, lg.campaign_id) for lg in logs}
    ut_result = await session.execute(
        select(LeadUnsubscribeToken).where(
            tuple_(LeadUnsubscribeToken.lead_id, LeadUnsubscribeToken.campaign_id).in_(list(pairs))
        )
    )
    unsub_by_pair = {(u.lead_id, u.campaign_id): u.token for u in ut_result.scalars().all()}

    raw: list[dict[str, Any]] = []
    for lg in logs:
        if lg.open_token:
            raw.append({"kind": "open", "token": lg.open_token})
        for tl in lg.tracked_links:
            raw.append({"kind": "click", "token": tl.token, "original_url": tl.original_url})
        utok = unsub_by_pair.get((lg.lead_id, lg.campaign_id))
        if utok:
            raw.append({"kind": "unsubscribe", "token": utok})

    # One row per (kind, token) for Beacon; duplicate unsubs across sends collapse here.
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for item in raw:
        k = item["kind"]
        t = item["token"]
        by_key[(k, t)] = item
    return list(by_key.values())


async def sync_inbox_tracking_to_beacon(session: AsyncSession, inbox: Inbox) -> int:
    """Push every known mapping for this inbox to Beacon (e.g. after connect or beacon DB reset)."""
    if not getattr(inbox, "beacon_connected", False):
        return 0
    items = await collect_inbox_beacon_items(session, inbox.id)
    if not items:
        return 0
    await register_beacon_mappings_batched(inbox, items)
    log.info("beacon sync: inbox_id=%s pushed %s registration row(s)", inbox.id, len(items))
    return len(items)
