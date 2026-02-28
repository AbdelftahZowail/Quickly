"""
Simple smoke test: ensure there is enough data for the analytics dashboard.

Run this after resetting/starting with an empty database.  It will
create a single campaign, a handful of leads, and insert EmailLog rows
spanning the past two weeks.  The frontend chart can then be pointed at
"last 14 days" (or wider range) to verify layout, stacking, and KPI cards.

Usage:
    python smoke_test/populate_analytics_data.py
    python smoke_test/populate_analytics_data.py --leads 50 --days 21

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


async def main(args):
    async with AsyncSessionLocal() as session:
        # ensure a campaign exists
        camp = (await session.execute(select(Campaign))).scalars().first()
        if not camp:
            camp = Campaign(name="[TEST] Analytics", paused=False,
                            sending_days=[0,1,2,3,4,5,6],
                            sending_hours_start="09:00",
                            sending_hours_end="17:00",
                            wait_minutes_between=1)
            session.add(camp)
            await session.commit()
            await session.refresh(camp)
            print(f"Created campaign {camp.id}")
        else:
            print(f"Using existing campaign {camp.id} ({camp.name})")

        # ensure some leads exist
        leads = (await session.execute(select(Lead))).scalars().all()
        need = max(0, args.leads - len(leads))
        for i in range(need):
            nl = Lead(email=f"analytics_lead_{len(leads)+i+1}@example.com", status="active")
            session.add(nl)
        if need:
            await session.commit()
            leads = (await session.execute(select(Lead))).scalars().all()
            print(f"Created {need} new leads; total now {len(leads)}")
        else:
            print(f"Found {len(leads)} existing leads")

        # ensure campaign leads exist so UI shows leads enrolled
        for lead in leads:
            existing_cl = await session.execute(
                select(CampaignLead).where(
                    CampaignLead.campaign_id == camp.id,
                    CampaignLead.lead_id == lead.id,
                )
            )
            if existing_cl.scalar_one_or_none() is None:
                cl = CampaignLead(campaign_id=camp.id, lead_id=lead.id)
                session.add(cl)
        await session.commit()

        # insert EmailLog entries for each day in range
        start = datetime.now() - timedelta(days=args.days)
        print(f"Inserting email logs from {start.date()} to today ({args.days} days)")
        for d in range(args.days + 1):
            date = start + timedelta(days=d)
            for lead in leads:
                el = EmailLog(
                    campaign_id=camp.id,
                    lead_id=lead.id,
                    inbox_id=None,
                    sequence_index=0,
                    sent_at=date,
                    subject="Dummy analytics email",
                    message_id=f"dummy-{camp.id}-{lead.id}-{d}",
                )
                session.add(el)
            # commit per day to flush logs so we can create events
            await session.commit()
            # add opens/clicks for today's logs
            for el in (await session.execute(select(EmailLog).where(EmailLog.campaign_id==camp.id, EmailLog.sent_at==date))).scalars():
                if random.random() < 0.5:
                    op = EmailOpen(email_log_id=el.id, ip_address=f"192.0.2.{random.randint(1,254)}")
                    session.add(op)
                    el.opened = True
                if random.random() < 0.2:
                    clk = EmailClick(email_log_id=el.id, ip_address=f"192.0.2.{random.randint(1,254)}")
                    session.add(clk)
                    el.clicked = True
            # randomly mark some leads replied and insert LeadReply
            for lead in leads:
                if random.random() < 0.1:
                    lead.status = 'replied'
                    lr = LeadReply(lead_id=lead.id, campaign_id=camp.id)
                    session.add(lr)
            await session.commit()
        print("EmailLog rows added.  Reload analytics page and select desired range.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Populate dummy data for analytics chart")
    parser.add_argument("--leads", type=int, default=20, help="number of leads to ensure")
    parser.add_argument("--days", type=int, default=14, help="how many days of history to generate")
    args = parser.parse_args()
    asyncio.run(main(args))
