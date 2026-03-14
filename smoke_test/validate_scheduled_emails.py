"""
Validate scheduled emails against business rules.

This script checks:
1. Emails follow the exact sending schedule defined in the campaign
2. Each lead is always contacted with the same inbox
3. Additional checks can be easily added

Usage:
    python validate_scheduled_emails.py
"""
import os
import sys

# When running this validation helper directly its containing directory is placed
# on the import path, meaning ``import app`` would fail.  Prepend the project
# root so that sibling packages are resolvable.  This mirrors the change made in
# other smoke_test utilities.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import asyncio
import logging
from datetime import datetime, date, time, timedelta
from typing import List, Dict, Optional, Set, Tuple
from dataclasses import dataclass, field
from collections import defaultdict

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import AsyncSessionLocal
from app.models import Campaign, Sequence, CampaignLead, QueueSlot, Inbox, EmailLog, CampaignInbox

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
log = logging.getLogger(__name__)


@dataclass
class ValidationIssue:
    """Represents a single validation issue."""
    check_name: str
    severity: str  # 'ERROR' or 'WARNING'
    lead_email: str
    campaign_name: str
    details: str


@dataclass
class ValidationResult:
    """Stores all validation results."""
    total_slots_checked: int = 0
    total_leads_checked: int = 0
    issues: List[ValidationIssue] = field(default_factory=list)
    passes: List[str] = field(default_factory=list)
    # Efficiency tracking
    total_capacity: int = 0  # Sum of max_emails_per_day across all inbox-days
    total_empty_slots: int = 0  # Unused capacity
    inbox_stats: Dict[str, Dict] = field(default_factory=dict)  # Per-inbox statistics
    
    def add_error(self, check_name: str, lead_email: str, campaign_name: str, details: str):
        """Add an error to the results."""
        self.issues.append(ValidationIssue(check_name, 'ERROR', lead_email, campaign_name, details))
    
    def add_warning(self, check_name: str, lead_email: str, campaign_name: str, details: str):
        """Add a warning to the results."""
        self.issues.append(ValidationIssue(check_name, 'WARNING', lead_email, campaign_name, details))

    def add_pass(self, check_name: str, subject: str, details: str):
        """Record a check that passed."""
        self.passes.append(f"[PASS] {check_name} | {subject}: {details}")
    
    def has_errors(self) -> bool:
        """Check if there are any errors."""
        return any(issue.severity == 'ERROR' for issue in self.issues)
    
    def print_summary(self):
        """Print a summary of validation results."""
        print("\n" + "="*80)
        print("VALIDATION SUMMARY")
        print("="*80)
        print(f"Total leads checked: {self.total_leads_checked}")
        print(f"Total queue slots checked: {self.total_slots_checked}")
        print(f"Total issues found: {len(self.issues)}")
        
        errors = [i for i in self.issues if i.severity == 'ERROR']
        warnings = [i for i in self.issues if i.severity == 'WARNING']
        
        print(f"  - Errors: {len(errors)}")
        print(f"  - Warnings: {len(warnings)}")
        print("="*80)
        
        # Print efficiency statistics
        if self.total_capacity > 0:
            efficiency = ((self.total_capacity - self.total_empty_slots) / self.total_capacity) * 100
            print(f"\nQUEUE EFFICIENCY:")
            print("-"*80)
            print(f"Total inbox capacity (across all days): {self.total_capacity} emails")
            print(f"Scheduled emails: {self.total_slots_checked}")
            print(f"Empty slots (unused capacity): {self.total_empty_slots}")
            print(f"Capacity utilization: {efficiency:.1f}%")
            print("\nPer-Inbox Statistics:")
            
            for inbox_email, stats in sorted(self.inbox_stats.items()):
                inbox_eff = ((stats['capacity'] - stats['empty']) / stats['capacity']) * 100 if stats['capacity'] > 0 else 0
                print(f"  {inbox_email}:")
                print(f"    - Active days: {stats['days']}")
                print(f"    - Capacity: {stats['capacity']} emails (max {stats['max_per_day']}/day)")
                print(f"    - Scheduled: {stats['used']} emails")
                print(f"    - Empty slots: {stats['empty']}")
                print(f"    - Utilization: {inbox_eff:.1f}%")
            print("="*80)
        
        if self.passes:
            print("\nPASSED CHECKS:")
            print("-"*80)
            for msg in self.passes:
                print(f"  {msg}")

        if self.issues:
            print("\nISSUES FOUND:")
            print("-"*80)
            for issue in self.issues:
                print(f"\n[{issue.severity}] {issue.check_name}")
                print(f"  Campaign: {issue.campaign_name}")
                print(f"  Lead: {issue.lead_email}")
                print(f"  Details: {issue.details}")
        elif not self.passes:
            print("\n✓ All validation checks passed! No issues found.")
        
        print("\n" + "="*80 + "\n")


def next_business_date(from_date: date, sending_days: List[int], delta_days: int) -> date:
    """
    Advance from_date by delta_days counting only business days (in sending_days).
    This mirrors the logic in queue_logic.py
    """
    if delta_days <= 0:
        d = from_date
        while d.weekday() not in sending_days:
            d += timedelta(days=1)
        return d
    count = 0
    d = from_date
    while count < delta_days:
        d += timedelta(days=1)
        if d.weekday() in sending_days:
            count += 1
    return d


def count_business_days_between(start_date: date, end_date: date, sending_days: List[int]) -> int:
    """
    Count the number of business days between start_date and end_date (exclusive of start, inclusive of end).
    Only counts days that are in sending_days.
    """
    if end_date <= start_date:
        return 0
    
    count = 0
    current = start_date + timedelta(days=1)
    while current <= end_date:
        if current.weekday() in sending_days:
            count += 1
        current += timedelta(days=1)
    return count


class EmailScheduleValidator:
    """Validates scheduled emails against business rules."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
        self.result = ValidationResult()
    
    async def validate_all(self) -> ValidationResult:
        """Run all validation checks."""
        log.info("Starting validation of scheduled emails...")
        
        # Get all campaigns with scheduled slots
        campaigns = await self._get_active_campaigns()
        log.info(f"Found {len(campaigns)} campaigns to validate")
        
        for campaign in campaigns:
            await self._validate_campaign(campaign)
        
        # Check inbox daily limits across all campaigns
        await self._check_inbox_daily_limits()
        
        # Check inbox send rate (wait_minutes_between)
        await self._check_inbox_send_rate()

        # Check that per-inbox jitter is uniformly distributed
        await self._check_jitter_distribution()

        # Calculate queue efficiency
        await self._calculate_queue_efficiency()
        
        log.info("Validation complete")
        return self.result
    
    async def _get_active_campaigns(self) -> List[Campaign]:
        """Get all campaigns that have queue slots scheduled.

        PostgreSQL does not support equality for `json` columns, so we avoid a
        plain ``distinct()`` which expands to ``SELECT DISTINCT campaign.*``
        (including the ``sending_days`` json field).  Instead we ask for a
        distinct campaign *id* only and load the campaigns in a second step.
        """
        # first grab unique campaign IDs
        id_query = (
            select(Campaign.id)
            .join(CampaignLead, Campaign.id == CampaignLead.campaign_id)
            .join(QueueSlot, CampaignLead.id == QueueSlot.campaign_lead_id)
            .distinct(Campaign.id)
        )
        res = await self.session.execute(id_query)
        campaign_ids = [row[0] for row in res.fetchall()]
        if not campaign_ids:
            return []

        # load full campaign objects
        query = select(Campaign).where(Campaign.id.in_(campaign_ids))
        result = await self.session.execute(query)
        return result.scalars().all()
    
    async def _validate_campaign(self, campaign: Campaign):
        """Validate all scheduled emails for a campaign."""
        log.info(f"Validating campaign: {campaign.name} (ID: {campaign.id})")
        
        # Load campaign sequences
        sequences_query = select(Sequence).where(
            Sequence.campaign_id == campaign.id
        ).order_by(Sequence.position)
        sequences = (await self.session.execute(sequences_query)).scalars().all()
        
        if not sequences:
            log.warning(f"Campaign {campaign.name} has no sequences")
            return
        
        # Load campaign leads and their slots (with eager loading of lead relationship)
        leads_query = select(CampaignLead).where(
            CampaignLead.campaign_id == campaign.id
        ).options(selectinload(CampaignLead.lead))
        campaign_leads = (await self.session.execute(leads_query)).scalars().all()
        
        inbox_consistency_violations = []
        schedule_violation_count = 0

        for campaign_lead in campaign_leads:
            # Load lead info
            lead = campaign_lead.lead
            
            # Load all queue slots for this lead
            slots_query = (
                select(QueueSlot)
                .where(QueueSlot.campaign_lead_id == campaign_lead.id)
                .order_by(QueueSlot.sequence_index)
            )
            slots = (await self.session.execute(slots_query)).scalars().all()
            
            if not slots:
                continue
            
            self.result.total_leads_checked += 1
            self.result.total_slots_checked += len(slots)
            
            # Run validation checks for this lead
            schedule_violation_count += await self._check_sending_schedule(campaign, sequences, lead, slots)
            violation = await self._check_inbox_consistency(campaign, lead, slots)
            if violation:
                inbox_consistency_violations.append(violation)
            # Add more checks here as needed

        if schedule_violation_count:
            self.result.add_error(
                'Schedule Validation',
                f"{schedule_violation_count} slot(s)",
                campaign.name,
                f"{schedule_violation_count} slot(s) have incorrect spacing between sequences."
            )

        if inbox_consistency_violations:
            self.result.add_error(
                'Inbox Consistency',
                f"{len(inbox_consistency_violations)} lead(s)",
                campaign.name,
                f"{len(inbox_consistency_violations)} lead(s) are being contacted from multiple inboxes."
            )
    
    async def _check_sending_schedule(
        self, 
        campaign: Campaign, 
        sequences: List[Sequence],
        lead,
        slots: List[QueueSlot]
    ) -> int:
        """
        Check that emails follow the exact sending schedule defined in the campaign.
        Returns the number of violations found.
        """
        sending_days = campaign.sending_days or [0, 1, 2, 3, 4]
        
        # Get sent emails to include in scheduling validation
        sent_query = (
            select(EmailLog)
            .where(
                EmailLog.lead_id == lead.id,
                EmailLog.campaign_id == campaign.id
            )
            .order_by(EmailLog.sequence_index)
        )
        sent_emails = (await self.session.execute(sent_query)).scalars().all()
        
        # Build a complete timeline: sent emails + scheduled slots
        timeline = []
        
        for email_log in sent_emails:
            timeline.append({
                'type': 'sent',
                'sequence_index': email_log.sequence_index,
                'date': email_log.sent_at.date(),
                'datetime': email_log.sent_at
            })
        
        for slot in slots:
            timeline.append({
                'type': 'scheduled',
                'sequence_index': slot.sequence_index,
                'date': slot.scheduled_date.date(),
                'datetime': slot.scheduled_date,
                'inbox_id': slot.inbox_id
            })
        
        # Sort by sequence index
        timeline.sort(key=lambda x: x['sequence_index'])
        
        today = date.today()
        violations = 0

        # Validate each email against the previous one
        for i in range(1, len(timeline)):
            prev = timeline[i - 1]
            curr = timeline[i]
            
            curr_seq_idx = curr['sequence_index']
            
            # Find the corresponding sequence definition
            if curr_seq_idx >= len(sequences):
                violations += 1
                continue
            
            sequence = sequences[curr_seq_idx]
            expected_wait_days = sequence.wait_days_after_previous
            
            # Calculate actual business days between emails
            actual_business_days = count_business_days_between(
                prev['date'],
                curr['date'],
                sending_days
            )
            
            if actual_business_days < expected_wait_days:
                # Gap is shorter than the required minimum — always an error.
                violations += 1

            elif actual_business_days > expected_wait_days and curr['type'] == 'scheduled':
                # Gap is larger than required. This is only acceptable when:
                #   (a) The ideal target date was already in the past at scheduling
                #       time, so ASAP scheduling naturally pushed it forward, OR
                #   (b) The inbox was at capacity on the ideal target date, so the
                #       schedule had no choice but to use a later day.
                # Any other case means the algorithm introduced a delay for no reason.
                ideal_date = next_business_date(prev['date'], sending_days, expected_wait_days)

                if ideal_date >= today:
                    # Ideal date is not in the past — check if the inbox was full.
                    inbox_id = curr.get('inbox_id')
                    if inbox_id is not None:
                        day_start = datetime.combine(ideal_date, time(0, 0))
                        day_end = datetime.combine(ideal_date + timedelta(days=1), time(0, 0))
                        count_result = await self.session.execute(
                            select(func.count(QueueSlot.id)).where(
                                QueueSlot.inbox_id == inbox_id,
                                QueueSlot.scheduled_date >= day_start,
                                QueueSlot.scheduled_date < day_end,
                            )
                        )
                        slots_on_ideal = count_result.scalar() or 0
                        inbox_result = await self.session.execute(
                            select(Inbox.max_emails_per_day).where(Inbox.id == inbox_id)
                        )
                        max_per_day = inbox_result.scalar() or 999

                        if slots_on_ideal < max_per_day:
                            # Inbox had capacity on the ideal date but slot was
                            # placed later — the algorithm introduced an unnecessary gap.
                            violations += 1
                # else: ideal_date is in the past → ASAP scheduling is justified, no violation.
        
        return violations
    
    async def _check_inbox_consistency(self, campaign: Campaign, lead, slots: List[QueueSlot]):
        """
        Check that all emails to this lead use the same inbox.
        Returns the lead email if a violation is found, otherwise None.
        """
        # Get all inbox IDs used for this lead (including sent emails)
        inbox_ids = set()
        
        # Check sent emails
        sent_query = (
            select(EmailLog.inbox_id)
            .where(
                EmailLog.lead_id == lead.id,
                EmailLog.campaign_id == campaign.id,
                EmailLog.inbox_id.isnot(None)
            )
        )
        sent_inbox_ids = (await self.session.execute(sent_query)).scalars().all()
        inbox_ids.update(sent_inbox_ids)
        
        # Check scheduled slots
        for slot in slots:
            inbox_ids.add(slot.inbox_id)
        
        if len(inbox_ids) > 1:
            return lead.email
        
        return None
    
    async def _check_inbox_daily_limits(self):
        """
        Check that no inbox exceeds its max_emails_per_day limit on any date.
        This checks across all campaigns since an inbox can be used by multiple campaigns.
        """
        # Get all inboxes with their limits
        inboxes_query = select(Inbox)
        inboxes = (await self.session.execute(inboxes_query)).scalars().all()
        
        for inbox in inboxes:
            # Get all scheduled slots for this inbox grouped by date
            slots_query = select(QueueSlot).where(QueueSlot.inbox_id == inbox.id)
            slots = (await self.session.execute(slots_query)).scalars().all()
            
            if not slots:
                continue
            
            # Group slots by date
            slots_by_date = defaultdict(list)
            for slot in slots:
                slot_date = slot.scheduled_date.date()
                slots_by_date[slot_date].append(slot)
            
            # Check each date against the limit
            violating_days = 0
            max_overrun = 0
            for slot_date, day_slots in slots_by_date.items():
                count = len(day_slots)
                if count > inbox.max_emails_per_day:
                    violating_days += 1
                    overrun = count - inbox.max_emails_per_day
                    if overrun > max_overrun:
                        max_overrun = overrun

            if violating_days:
                self.result.add_error(
                    'Inbox Daily Limit',
                    inbox.email,
                    'Multiple campaigns',
                    f"{violating_days} day(s) exceed the {inbox.max_emails_per_day} emails/day limit. "
                    f"Worst overrun: +{max_overrun} email(s)."
                )
    
    async def _check_inbox_send_rate(self):
        """
        Check that no inbox sends emails faster than its wait_minutes_between setting.
        Every pair of consecutive scheduled emails from the same inbox (ordered by
        scheduled_date) must be at least wait_minutes_between minutes apart.
        """
        inboxes_query = select(Inbox)
        inboxes = (await self.session.execute(inboxes_query)).scalars().all()

        for inbox in inboxes:
            min_gap = inbox.wait_minutes_between
            if min_gap <= 0:
                continue  # No rate limit configured

            # Fetch all scheduled slots for this inbox ordered by time
            slots_query = (
                select(QueueSlot)
                .where(QueueSlot.inbox_id == inbox.id)
                .order_by(QueueSlot.scheduled_date)
            )
            slots = (await self.session.execute(slots_query)).scalars().all()

            if len(slots) < 2:
                continue

            violations = []
            min_gap_found = None

            for i in range(1, len(slots)):
                prev = slots[i - 1]
                curr = slots[i]

                gap_minutes = (
                    curr.scheduled_date - prev.scheduled_date
                ).total_seconds() / 60

                if gap_minutes < min_gap:
                    violations.append(gap_minutes)
                    if min_gap_found is None or gap_minutes < min_gap_found:
                        min_gap_found = gap_minutes

            if violations:
                self.result.add_error(
                    'Inbox Send Rate',
                    inbox.email,
                    'Multiple campaigns',
                    f"{len(violations)} pair(s) of slots violate the {min_gap}-minute send rate. "
                    f"Smallest gap found: {min_gap_found:.1f} minute(s)."
                )

    async def _calculate_queue_efficiency(self):
        """
        Calculate how efficiently the queue is using available inbox capacity.
        Tracks total capacity vs. actual usage to identify empty slots.
        """
        # Get all inboxes
        inboxes_query = select(Inbox)
        inboxes = (await self.session.execute(inboxes_query)).scalars().all()
        
        # Get all scheduled slots to determine active dates per inbox
        all_slots_query = select(QueueSlot)
        all_slots = (await self.session.execute(all_slots_query)).scalars().all()
        
        # Group slots by inbox and date
        inbox_date_usage = defaultdict(lambda: defaultdict(int))
        for slot in all_slots:
            slot_date = slot.scheduled_date.date()
            inbox_date_usage[slot.inbox_id][slot_date] += 1
        
        # Calculate capacity and usage per inbox
        total_capacity = 0
        total_used = 0
        
        for inbox in inboxes:
            if inbox.id not in inbox_date_usage:
                continue  # Skip inboxes with no scheduled emails
            
            dates_with_emails = inbox_date_usage[inbox.id]
            active_days = len(dates_with_emails)
            inbox_capacity = active_days * inbox.max_emails_per_day
            inbox_used = sum(dates_with_emails.values())
            inbox_empty = inbox_capacity - inbox_used
            
            total_capacity += inbox_capacity
            total_used += inbox_used
            
            # Store per-inbox stats
            self.result.inbox_stats[inbox.email] = {
                'days': active_days,
                'capacity': inbox_capacity,
                'used': inbox_used,
                'empty': inbox_empty,
                'max_per_day': inbox.max_emails_per_day
            }
        
        self.result.total_capacity = total_capacity
        self.result.total_empty_slots = total_capacity - total_used
        
        # avoid division by zero when there is no capacity at all
        if total_capacity > 0:
            pct = (total_used / total_capacity) * 100
            log.info(
                f"Queue efficiency: {total_used}/{total_capacity} slots used "
                f"({pct:.1f}% utilization), "
                f"{self.result.total_empty_slots} empty slots"
            )
        else:
            log.info(
                "Queue efficiency: no capacity (empty inboxes or no scheduled slots)"
            )
    
    async def _check_jitter_distribution(self):
        """
        Check that the random jitter applied to each slot is statistically
        uniform across an inbox's scheduled queue.

        For each inbox with max_jitter_seconds > 0 the jitter on a slot is
        recovered by subtracting the minimum inter-send gap
        (wait_minutes_between * 60 s) from the actual gap between consecutive
        same-day slots.  Because jitter is drawn from a uniform [0, max_jitter_s]
        distribution its expected average is max_jitter_seconds / 2.  A WARNING
        is raised when the observed mean deviates from that expectation by more
        than 10 seconds.

        At least 10 same-day consecutive pairs are required before the check
        fires; with fewer samples the variance is too large to be meaningful.
        """
        MIN_SAMPLES = 10
        TOLERANCE_SECONDS = 10.0

        inboxes_query = select(Inbox)
        inboxes = (await self.session.execute(inboxes_query)).scalars().all()

        for inbox in inboxes:
            max_jitter = inbox.max_jitter_seconds
            if not max_jitter or max_jitter <= 0:
                continue  # Jitter disabled for this inbox

            min_gap_seconds = inbox.wait_minutes_between * 60
            expected_avg = max_jitter / 2.0

            # Fetch all slots ordered by time so same-day pairs are consecutive
            slots_query = (
                select(QueueSlot)
                .where(QueueSlot.inbox_id == inbox.id)
                .order_by(QueueSlot.scheduled_date)
            )
            slots = (await self.session.execute(slots_query)).scalars().all()

            if len(slots) < 2:
                continue

            # Group into per-day buckets (already time-sorted)
            slots_by_date: Dict[date, List] = defaultdict(list)
            for slot in slots:
                slots_by_date[slot.scheduled_date.date()].append(slot)

            jitter_samples: List[float] = []
            for day_slots in slots_by_date.values():
                if len(day_slots) < 2:
                    continue
                for i in range(1, len(day_slots)):
                    gap_s = (
                        day_slots[i].scheduled_date - day_slots[i - 1].scheduled_date
                    ).total_seconds()
                    extracted = gap_s - min_gap_seconds
                    # Only keep values that sit within the plausible jitter range;
                    # gaps that are too large or negative indicate cross-day or
                    # scheduling anomalies already covered by other checks.
                    if 0.0 <= extracted <= max_jitter + TOLERANCE_SECONDS:
                        jitter_samples.append(extracted)

            if len(jitter_samples) < MIN_SAMPLES:
                continue  # Not enough data to draw a conclusion

            avg_jitter = sum(jitter_samples) / len(jitter_samples)
            deviation = abs(avg_jitter - expected_avg)

            if deviation > TOLERANCE_SECONDS:
                self.result.add_warning(
                    'Jitter Distribution',
                    inbox.email,
                    'Multiple campaigns',
                    f"Average extracted jitter is {avg_jitter:.1f}s but expected "
                    f"~{expected_avg:.1f}s (half of max_jitter_seconds={max_jitter}s). "
                    f"Deviation: {deviation:.1f}s (tolerance: ±{TOLERANCE_SECONDS:.0f}s). "
                    f"Sampled from {len(jitter_samples)} consecutive same-day slot pairs."
                )
            else:
                self.result.add_pass(
                    'Jitter Distribution',
                    inbox.email,
                    f"avg jitter {avg_jitter:.1f}s ≈ expected {expected_avg:.1f}s "
                    f"(deviation {deviation:.1f}s ≤ ±{TOLERANCE_SECONDS:.0f}s, "
                    f"{len(jitter_samples)} samples)"
                )

    # ========================================================================
    # ADD NEW VALIDATION CHECKS BELOW
    # ========================================================================
    # Example template for adding a new check:
    #
    # async def _check_your_new_rule(self, campaign: Campaign, lead, slots: List[QueueSlot]):
    #     """
    #     Description of what this check validates.
    #     """
    #     # Your validation logic here
    #     if something_is_wrong:
    #         self.result.add_error(
    #             'Your Check Name',
    #             lead.email,
    #             campaign.name,
    #             'Description of the issue'
    #         )
    #
    # Then call it from _validate_campaign:
    #     await self._check_your_new_rule(campaign, lead, slots)
    # ========================================================================


async def main():
    """Main entry point for validation script."""
    async with AsyncSessionLocal() as session:
        validator = EmailScheduleValidator(session)
        result = await validator.validate_all()
        result.print_summary()
        
        # Exit with error code if validation failed
        if result.has_errors():
            exit(1)
        else:
            exit(0)


if __name__ == "__main__":
    asyncio.run(main())
