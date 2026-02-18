"""
Simulate queue progression by "sending" scheduled emails over a date range.

By default this script simulates 2 days starting from now:
- Finds due QueueSlot rows in the simulation window
- Applies the same high-level guards as the send job (paused campaigns,
  inactive leads, stop_on_reply)
- Inserts EmailLog rows with simulated sent_at timestamps
- Deletes processed QueueSlot rows

Usage:
    python simulate_queue_2_days.py
    python simulate_queue_2_days.py --days 2 --dry-run
    python simulate_queue_2_days.py --start "2026-02-18 09:00" --days 2
"""

import argparse
import asyncio
import logging
from dataclasses import dataclass, field
import json
import urllib.request
from datetime import datetime, timedelta
from app import time as time_provider
from typing import Dict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models import QueueSlot, CampaignLead, Campaign, Lead, Sequence, Inbox, EmailLog, LeadReply


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
log = logging.getLogger(__name__)


@dataclass
class SimulationResult:
    window_start: datetime
    window_end: datetime
    total_slots_found: int = 0
    simulated_sent: int = 0
    skipped_paused_campaign: int = 0
    skipped_inactive_lead: int = 0
    skipped_stop_on_reply: int = 0
    by_campaign: Dict[str, int] = field(default_factory=dict)
    by_inbox: Dict[str, int] = field(default_factory=dict)

    def bump_campaign(self, campaign_name: str):
        self.by_campaign[campaign_name] = self.by_campaign.get(campaign_name, 0) + 1

    def bump_inbox(self, inbox_email: str):
        self.by_inbox[inbox_email] = self.by_inbox.get(inbox_email, 0) + 1

    def print_summary(self, dry_run: bool):
        title = "SIMULATION SUMMARY (DRY RUN)" if dry_run else "SIMULATION SUMMARY"
        print("\n" + "=" * 80)
        print(title)
        print("=" * 80)
        print(f"Window start: {self.window_start.isoformat(sep=' ', timespec='seconds')}")
        print(f"Window end:   {self.window_end.isoformat(sep=' ', timespec='seconds')}")
        print(f"Queue slots found: {self.total_slots_found}")
        print(f"Simulated sent:    {self.simulated_sent}")
        print("Skipped:")
        print(f"  - Paused campaign: {self.skipped_paused_campaign}")
        print(f"  - Inactive lead:   {self.skipped_inactive_lead}")
        print(f"  - stop_on_reply:   {self.skipped_stop_on_reply}")

        if self.by_campaign:
            print("\nSent by campaign:")
            for name, count in sorted(self.by_campaign.items(), key=lambda item: (-item[1], item[0])):
                print(f"  - {name}: {count}")

        if self.by_inbox:
            print("\nSent by inbox:")
            for email, count in sorted(self.by_inbox.items(), key=lambda item: (-item[1], item[0])):
                print(f"  - {email}: {count}")

        print("=" * 80 + "\n")


class QueueSimulator:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def simulate(self, start_at: datetime, days: int, dry_run: bool) -> SimulationResult:
        if days <= 0:
            raise ValueError("days must be >= 1")

        end_at = start_at + timedelta(days=days)
        result = SimulationResult(window_start=start_at, window_end=end_at)

        rows_result = await self.session.execute(
            select(QueueSlot, CampaignLead, Campaign, Lead, Sequence, Inbox)
            .join(CampaignLead, QueueSlot.campaign_lead_id == CampaignLead.id)
            .join(Campaign, CampaignLead.campaign_id == Campaign.id)
            .join(Lead, CampaignLead.lead_id == Lead.id)
            .join(
                Sequence,
                (Sequence.campaign_id == Campaign.id)
                & (Sequence.position == QueueSlot.sequence_index),
            )
            .join(Inbox, QueueSlot.inbox_id == Inbox.id)
            .where(
                QueueSlot.scheduled_date >= start_at,
                QueueSlot.scheduled_date < end_at,
            )
            .order_by(QueueSlot.scheduled_date, QueueSlot.position_in_day)
        )
        rows = rows_result.all()
        result.total_slots_found = len(rows)

        log.info(
            "Simulating queue from %s to %s (%d slot(s) found)",
            start_at.isoformat(sep=" ", timespec="seconds"),
            end_at.isoformat(sep=" ", timespec="seconds"),
            result.total_slots_found,
        )

        for slot, _cl, campaign, lead, sequence, inbox in rows:
            if getattr(campaign, "paused", False):
                result.skipped_paused_campaign += 1
                continue

            if lead.status != "active":
                result.skipped_inactive_lead += 1
                continue

            if campaign.stop_on_reply:
                reply_check = await self.session.execute(
                    select(LeadReply.id).where(
                        LeadReply.lead_id == lead.id,
                        LeadReply.campaign_id == campaign.id,
                    )
                )
                if reply_check.scalar_one_or_none() is not None:
                    result.skipped_stop_on_reply += 1
                    continue

            subject = (sequence.subject or "").strip() or "(no subject)"
            simulated_log = EmailLog(
                lead_id=lead.id,
                campaign_id=campaign.id,
                inbox_id=inbox.id,
                sequence_index=slot.sequence_index,
                sent_at=slot.scheduled_date,
                subject=subject,
                message_id=f"sim-{campaign.id}-{lead.id}-{slot.sequence_index}-{slot.id}",
            )
            self.session.add(simulated_log)
            await self.session.delete(slot)

            result.simulated_sent += 1
            result.bump_campaign(campaign.name)
            result.bump_inbox(inbox.email)

        if dry_run:
            await self.session.rollback()
            log.info("Dry run enabled: rolled back all simulated changes")
        else:
            await self.session.commit()
            log.info("Simulation committed: %d email(s) logged and queue slots removed", result.simulated_sent)

        return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Simulate queue passing and email sending for a time window (default: 2 days)."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=2,
        help="Number of days to simulate from start (default: 2)",
    )
    parser.add_argument(
        "--start",
        type=str,
        default=None,
        help='Simulation start datetime in "YYYY-MM-DD HH:MM" format (default: now)',
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without committing them",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset any persisted time-travel offset back to real now",
    )
    return parser.parse_args()


def parse_start(start_text: str | None) -> datetime:
    if not start_text:
        return time_provider.now()
    return datetime.strptime(start_text, "%Y-%m-%d %H:%M")


async def main():
    args = parse_args()

    # --reset: clear persisted time offset and exit
    if args.reset:
        async with AsyncSessionLocal() as session:
            await time_provider.clear_persisted_offset(session)
        print("Persisted time offset cleared (server now uses real time)")
        return

    start_at = parse_start(args.start)

    async with AsyncSessionLocal() as session:
        simulator = QueueSimulator(session)
        sim_result = await simulator.simulate(start_at=start_at, days=args.days, dry_run=args.dry_run)

    sim_result.print_summary(dry_run=args.dry_run)

    # Persist the requested offset so the whole application/server uses it.
    # Do not persist when doing a dry-run.
    if not args.dry_run and args.days:
        async with AsyncSessionLocal() as session:
            await time_provider.persist_offset_days(session, args.days)
        print(f"Persisted time offset: {args.days} day(s) — entire app will use shifted 'now'.")

        # Try to notify a running server process (if any) to reload settings so the
        # change takes effect immediately application-wide. This is best-effort.
        try:
            req = urllib.request.Request(
                "http://localhost:8000/api/settings/time-offset",
                data=json.dumps({"time_offset_days": args.days}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                print("Notified running server to reload settings (if running)")
        except Exception:
            print("Unable to notify running server (it may not be running); DB value persisted.")
    elif args.dry_run:
        print("Dry run: persisted time offset NOT changed.")


if __name__ == "__main__":
    asyncio.run(main())
