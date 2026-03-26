"""One-off backfill: align CampaignLead state with EmailLog and interest rules.

1. **Blocked sends** (completed / bounced / unsubscribed / wrong_person, or interest
   ``not_interested`` / ``out_of_office``): delete all ``QueueSlot`` rows for those
   enrollments — same side effect as ``PATCH .../leads/{lead_id}`` when blocked.

2. **Contacted / completed**: for enrollments still ``active`` or ``contacted`` but with
   at least one ``EmailLog`` row for that (lead, campaign), set enrollment the same way
   as ``_update_enrollment_after_send`` in ``app/jobs.py`` (``contacted``, or
   ``completed`` when the max sent sequence index covers the last step).

Run from repo root (uses ``DATABASE_URL`` / settings like the app):

    python scripts/refresh_campaign_lead_state.py

With Docker Compose (service name ``backend``):

    docker compose -f docker-compose-not-host.dev.yml exec backend \\
      python scripts/refresh_campaign_lead_state.py

Dry run (no DB writes):

    python scripts/refresh_campaign_lead_state.py --dry-run

**Reading the summary**

- *Blocked enrollments*: rows that should not receive mail (terminal enrollment or
  ``not_interested`` / ``out_of_office``). If *QueueSlot rows removed* is ``0``,
  those enrollments simply have no pending slots left (often already true after
  a full recalculation or never scheduled).
- *Enrollment … updated*: non‑zero only when ``active``/``contacted`` still
  disagrees with ``EmailLog`` + sequence count (e.g. legacy ``active`` despite
  sends).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import warnings

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.exc import SAWarning

# Gmail/O365 ORM overlap warnings during mapper configure (noise for this CLI).
warnings.filterwarnings("ignore", category=SAWarning)

from app.database import AsyncSessionLocal
from app.models import CampaignLead, EmailLog, QueueSlot, Sequence


def _blocked_predicate():
    return or_(
        CampaignLead.enrollment_status.in_(
            ("bounced", "unsubscribed", "wrong_person", "completed")
        ),
        CampaignLead.interest_status.in_(("not_interested", "out_of_office")),
    )


async def _clear_slots_for_blocked(session, dry_run: bool) -> int:
    subq = select(CampaignLead.id).where(_blocked_predicate())
    count_before = (
        await session.execute(
            select(func.count(QueueSlot.id)).where(
                QueueSlot.campaign_lead_id.in_(subq)
            )
        )
    ).scalar() or 0
    if dry_run:
        return int(count_before)
    await session.execute(
        delete(QueueSlot).where(QueueSlot.campaign_lead_id.in_(subq))
    )
    return int(count_before)


async def _refresh_enrollment_from_sends(session, dry_run: bool) -> tuple[int, int, int, int]:
    """Bulk-align enrollment with EmailLog + sequence count (matches ``jobs._update_enrollment_after_send``).

    Returns ``(total_changed, examined_with_log, n_completed, n_promoted_active_to_contacted)``.
    """
    sent_stats = (
        select(
            EmailLog.lead_id,
            EmailLog.campaign_id,
            func.max(EmailLog.sequence_index).label("max_pos"),
        )
        .group_by(EmailLog.lead_id, EmailLog.campaign_id)
        .subquery()
    )
    seq_counts = (
        select(Sequence.campaign_id, func.count(Sequence.id).label("n_seq"))
        .group_by(Sequence.campaign_id)
        .subquery()
    )

    _active_or_contacted = func.lower(CampaignLead.enrollment_status).in_(("active", "contacted"))

    examined_q = (
        select(func.count(CampaignLead.id))
        .select_from(CampaignLead)
        .join(
            sent_stats,
            and_(
                CampaignLead.lead_id == sent_stats.c.lead_id,
                CampaignLead.campaign_id == sent_stats.c.campaign_id,
            ),
        )
        .where(_active_or_contacted)
    )
    examined = (await session.execute(examined_q)).scalar() or 0

    n_seq_col = seq_counts.c.n_seq
    mx = sent_stats.c.max_pos
    not_fully_sent = or_(func.coalesce(n_seq_col, 0) == 0, mx < func.coalesce(n_seq_col, 0) - 1)

    # 1) completed: active or contacted, campaign has sequences, last step sent
    completed_ids = (
        select(CampaignLead.id)
        .join(
            sent_stats,
            and_(
                CampaignLead.lead_id == sent_stats.c.lead_id,
                CampaignLead.campaign_id == sent_stats.c.campaign_id,
            ),
        )
        .join(seq_counts, seq_counts.c.campaign_id == CampaignLead.campaign_id)
        .where(
            _active_or_contacted,
            n_seq_col > 0,
            mx >= n_seq_col - 1,
        )
    )

    # 2) contacted: still *active* (any casing) but at least one send and not fully completed
    contacted_ids = (
        select(CampaignLead.id)
        .join(
            sent_stats,
            and_(
                CampaignLead.lead_id == sent_stats.c.lead_id,
                CampaignLead.campaign_id == sent_stats.c.campaign_id,
            ),
        )
        .outerjoin(seq_counts, seq_counts.c.campaign_id == CampaignLead.campaign_id)
        .where(
            func.lower(CampaignLead.enrollment_status) == "active",
            not_fully_sent,
        )
    )

    n_completed = (
        await session.execute(select(func.count()).select_from(completed_ids.subquery()))
    ).scalar() or 0
    n_contacted = (
        await session.execute(select(func.count()).select_from(contacted_ids.subquery()))
    ).scalar() or 0

    if not dry_run:
        if n_completed:
            await session.execute(
                update(CampaignLead)
                .where(CampaignLead.id.in_(completed_ids))
                .values(enrollment_status="completed")
            )
        if n_contacted:
            await session.execute(
                update(CampaignLead)
                .where(CampaignLead.id.in_(contacted_ids))
                .values(enrollment_status="contacted")
            )

    total = n_completed + n_contacted
    return total, int(examined), int(n_completed), int(n_contacted)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would change without committing.",
    )
    args = parser.parse_args()
    dry_run = args.dry_run

    async with AsyncSessionLocal() as session:
        n_blocked_cls = (
            await session.execute(
                select(func.count(CampaignLead.id)).where(_blocked_predicate())
            )
        ).scalar() or 0

        slots_removed = await _clear_slots_for_blocked(session, dry_run)
        enroll_changed, enroll_seen, n_done, n_touch = await _refresh_enrollment_from_sends(
            session, dry_run
        )

        print(
            f"Blocked enrollments (terminal enrollment or not_interested/out_of_office): {n_blocked_cls}"
        )
        print(f"QueueSlot rows removed for blocked enrollments: {slots_removed}")
        print(
            f"Enrollment rows with ≥1 EmailLog (active/contacted, any casing): {enroll_seen}; "
            f"updates: {enroll_changed} "
            f"(→ completed: {n_done}, active → contacted: {n_touch})"
        )

        if dry_run:
            print("Dry run — no changes committed.")
        else:
            await session.commit()
            print("Committed.")


if __name__ == "__main__":
    asyncio.run(main())
