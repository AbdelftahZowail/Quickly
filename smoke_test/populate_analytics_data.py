"""
Simple smoke test: ensure there is enough data for the analytics dashboard.

Two modes:
  default   – create a campaign, leads, and EmailLog rows spanning N days
  --existing – skip creating new rows; add open/click/reply analytics to
               emails that are already in the database

Usage:
    python smoke_test/populate_analytics_data.py
    python smoke_test/populate_analytics_data.py --leads 50 --days 21
    python smoke_test/populate_analytics_data.py --existing
    python smoke_test/populate_analytics_data.py --existing --campaign-id 3 --days 30
    python smoke_test/populate_analytics_data.py --existing --open-rate 0.6 --click-rate 0.3 --overwrite

Nothing is deleted by default; rerunning simply appends additional rows.
"""

import os
import sys
import argparse
import asyncio
import random
from datetime import datetime, timedelta

# allow importing from app by adding project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models import Campaign, Lead, EmailLog, EmailOpen, EmailClick, LeadReply, CampaignLead


async def _add_analytics(session, logs, open_rate, click_rate, reply_rate):
    """Attach random open/click/reply events to the given EmailLog rows.

    Opens and clicks are timestamped relative to each log's sent_at so
    the analytics timeline looks realistic.  Reply events are generated
    per unique (lead, campaign) pair rather than per log to avoid
    creating multiple LeadReply rows for the same lead/campaign.
    """
    replied_pairs: set[tuple[int, int]] = set()

    for el in logs:
        base_time = el.sent_at if el.sent_at else datetime.utcnow()

        if not el.opened and random.random() < open_rate:
            open_delay = timedelta(hours=random.uniform(0.5, 48))
            session.add(EmailOpen(
                email_log_id=el.id,
                ip_address=f"192.0.2.{random.randint(1, 254)}",
                opened_at=base_time + open_delay,
            ))
            el.opened = True

        # clicks only happen on opened emails
        if el.opened and not el.clicked and random.random() < click_rate:
            click_delay = timedelta(hours=random.uniform(0.5, 24))
            session.add(EmailClick(
                email_log_id=el.id,
                ip_address=f"192.0.2.{random.randint(1, 254)}",
                clicked_at=base_time + click_delay,
            ))
            el.clicked = True

        # one reply per (lead, campaign) pair at most
        pair = (el.lead_id, el.campaign_id)
        if pair not in replied_pairs and random.random() < reply_rate:
            replied_pairs.add(pair)
            reply_delay = timedelta(hours=random.uniform(1, 72))
            session.add(LeadReply(
                lead_id=el.lead_id,
                campaign_id=el.campaign_id,
                replied_at=base_time + reply_delay,
            ))


async def _create_mode(session, args):
    """Create dummy campaign/leads/EmailLog rows then add analytics."""
    # ensure a campaign exists
    camp = (await session.execute(select(Campaign))).scalars().first()
    if not camp:
        camp = Campaign(
            name="[TEST] Analytics",
            paused=False,
            sending_days=[0, 1, 2, 3, 4, 5, 6],
            sending_hours_start="09:00",
            sending_hours_end="17:00",
            wait_minutes_between=1,
        )
        session.add(camp)
        await session.commit()
        await session.refresh(camp)
        print(f"Created campaign {camp.id}")
    else:
        print(f"Using existing campaign {camp.id} ({camp.name})")

    # ensure the requested number of leads exist
    leads = (await session.execute(select(Lead))).scalars().all()
    need = max(0, args.leads - len(leads))
    for i in range(need):
        session.add(Lead(email=f"analytics_lead_{len(leads)+i+1}@example.com", status="active"))
    if need:
        await session.commit()
        leads = (await session.execute(select(Lead))).scalars().all()
        print(f"Created {need} new leads; total now {len(leads)}")
    else:
        print(f"Found {len(leads)} existing leads")

    # ensure campaign-lead links exist so the UI shows enrolled leads
    for lead in leads:
        existing = await session.execute(
            select(CampaignLead).where(
                CampaignLead.campaign_id == camp.id,
                CampaignLead.lead_id == lead.id,
            )
        )
        if existing.scalar_one_or_none() is None:
            session.add(CampaignLead(campaign_id=camp.id, lead_id=lead.id))
    await session.commit()

    # insert EmailLog rows, one per lead per day, then add analytics
    start = datetime.utcnow() - timedelta(days=args.days)
    print(f"Inserting email logs from {start.date()} to today ({args.days} days)")

    for d in range(args.days + 1):
        sent_at = start + timedelta(days=d)
        day_logs: list[EmailLog] = []
        for lead in leads:
            el = EmailLog(
                campaign_id=camp.id,
                lead_id=lead.id,
                inbox_id=None,
                sequence_index=0,
                sent_at=sent_at,
                subject="Dummy analytics email",
                message_id=f"dummy-{camp.id}-{lead.id}-{d}",
            )
            session.add(el)
            day_logs.append(el)

        # flush so the new rows get IDs before we attach events
        await session.flush()
        await _add_analytics(session, day_logs, args.open_rate, args.click_rate, args.reply_rate)
        await session.commit()

    print("Done. Reload the analytics page and select the desired date range.")


async def _existing_mode(session, args):
    """Add analytics events to EmailLog rows that already exist."""
    stmt = select(EmailLog)
    if args.campaign_id:
        stmt = stmt.where(EmailLog.campaign_id == args.campaign_id)
    if args.days:
        since = datetime.utcnow() - timedelta(days=args.days)
        stmt = stmt.where(EmailLog.sent_at >= since)

    logs = (await session.execute(stmt)).scalars().all()
    if not logs:
        print(
            "No matching EmailLog rows found.\n"
            "Run without --existing first to generate test data, or broaden --days / --campaign-id."
        )
        return

    print(f"Found {len(logs)} existing email log(s).")

    if not args.overwrite:
        logs = [el for el in logs if not el.opened and not el.clicked]
        print(f"  {len(logs)} without existing open/click events (use --overwrite to include all).")

    if not logs:
        print("All matching logs already have analytics. Use --overwrite to re-add events.")
        return

    await _add_analytics(session, logs, args.open_rate, args.click_rate, args.reply_rate)
    await session.commit()
    print(f"Analytics events added to {len(logs)} email log(s).")


async def main(args):
    async with AsyncSessionLocal() as session:
        if args.existing:
            await _existing_mode(session, args)
        else:
            await _create_mode(session, args)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Populate dummy analytics data for the dashboard")

    # create-mode options
    parser.add_argument("--leads", type=int, default=20,
                        help="number of leads to ensure (create mode, default: 20)")
    parser.add_argument("--days", type=int, default=14,
                        help="days of history to create, or look-back window for --existing (default: 14)")

    # existing-mode options
    parser.add_argument("--existing", action="store_true",
                        help="add analytics to existing sent emails instead of creating new ones")
    parser.add_argument("--campaign-id", type=int, default=None,
                        help="restrict to a specific campaign ID (existing mode)")
    parser.add_argument("--overwrite", action="store_true",
                        help="re-add events even if logs already have open/click data (existing mode)")

    # analytics rates (shared between both modes)
    parser.add_argument("--open-rate", type=float, default=0.5,
                        help="probability [0–1] an email gets an open event (default: 0.5)")
    parser.add_argument("--click-rate", type=float, default=0.2,
                        help="probability [0–1] an opened email gets a click event (default: 0.2)")
    parser.add_argument("--reply-rate", type=float, default=0.1,
                        help="probability [0–1] a lead replies per campaign (default: 0.1)")

    args = parser.parse_args()
    asyncio.run(main(args))
