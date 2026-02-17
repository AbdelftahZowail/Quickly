"""
Validate scheduled emails against business rules.

This script checks:
1. Emails follow the exact sending schedule defined in the campaign
2. Each lead is always contacted with the same inbox
3. Additional checks can be easily added

Usage:
    python validate_scheduled_emails.py
"""
import asyncio
import logging
from datetime import datetime, date, timedelta
from typing import List, Dict, Optional, Set, Tuple
from dataclasses import dataclass, field
from collections import defaultdict

from sqlalchemy import select
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
        
        if self.issues:
            print("\nISSUES FOUND:")
            print("-"*80)
            for issue in self.issues:
                print(f"\n[{issue.severity}] {issue.check_name}")
                print(f"  Campaign: {issue.campaign_name}")
                print(f"  Lead: {issue.lead_email}")
                print(f"  Details: {issue.details}")
        else:
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
        
        # Calculate queue efficiency
        await self._calculate_queue_efficiency()
        
        log.info("Validation complete")
        return self.result
    
    async def _get_active_campaigns(self) -> List[Campaign]:
        """Get all campaigns that have queue slots scheduled."""
        query = (
            select(Campaign)
            .join(CampaignLead, Campaign.id == CampaignLead.campaign_id)
            .join(QueueSlot, CampaignLead.id == QueueSlot.campaign_lead_id)
            .distinct()
        )
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
            await self._check_sending_schedule(campaign, sequences, lead, slots)
            await self._check_inbox_consistency(campaign, lead, slots)
            # Add more checks here as needed
    
    async def _check_sending_schedule(
        self, 
        campaign: Campaign, 
        sequences: List[Sequence],
        lead,
        slots: List[QueueSlot]
    ):
        """
        Check that emails follow the exact sending schedule defined in the campaign.
        Validates wait_days_after_previous for each sequence.
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
        
        # Validate each email against the previous one
        for i in range(1, len(timeline)):
            prev = timeline[i - 1]
            curr = timeline[i]
            
            prev_seq_idx = prev['sequence_index']
            curr_seq_idx = curr['sequence_index']
            
            # Find the corresponding sequence definition
            if curr_seq_idx >= len(sequences):
                self.result.add_error(
                    'Schedule Validation',
                    lead.email,
                    campaign.name,
                    f"Sequence index {curr_seq_idx} not found in campaign sequences (max: {len(sequences)-1})"
                )
                continue
            
            sequence = sequences[curr_seq_idx]
            expected_wait_days = sequence.wait_days_after_previous
            
            # Calculate actual business days between emails
            actual_business_days = count_business_days_between(
                prev['date'],
                curr['date'],
                sending_days
            )
            
            if actual_business_days != expected_wait_days:
                self.result.add_error(
                    'Schedule Validation',
                    lead.email,
                    campaign.name,
                    f"Email #{curr_seq_idx + 1} scheduled {actual_business_days} business day(s) after "
                    f"email #{prev_seq_idx + 1}, but campaign requires {expected_wait_days} business day(s). "
                    f"Previous: {prev['date']} ({prev['type']}), Current: {curr['date']} ({curr['type']})"
                )
    
    async def _check_inbox_consistency(self, campaign: Campaign, lead, slots: List[QueueSlot]):
        """
        Check that all emails to this lead use the same inbox.
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
            # Get inbox emails for readable error message
            inbox_query = select(Inbox.email).where(Inbox.id.in_(inbox_ids))
            inbox_emails = (await self.session.execute(inbox_query)).scalars().all()
            
            self.result.add_error(
                'Inbox Consistency',
                lead.email,
                campaign.name,
                f"Lead is being contacted from {len(inbox_ids)} different inboxes: {', '.join(inbox_emails)}. "
                f"All emails to a lead should come from the same inbox."
            )
    
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
            for slot_date, day_slots in slots_by_date.items():
                count = len(day_slots)
                if count > inbox.max_emails_per_day:
                    # Get campaign names for this date to provide context
                    campaign_lead_ids = [slot.campaign_lead_id for slot in day_slots]
                    cl_query = (
                        select(CampaignLead)
                        .where(CampaignLead.id.in_(campaign_lead_ids))
                        .options(selectinload(CampaignLead.campaign))
                    )
                    campaign_leads = (await self.session.execute(cl_query)).scalars().all()
                    campaign_names = list(set(cl.campaign.name for cl in campaign_leads))
                    
                    self.result.add_error(
                        'Inbox Daily Limit',
                        inbox.email,
                        ', '.join(campaign_names) if campaign_names else 'Multiple campaigns',
                        f"Inbox has {count} emails scheduled on {slot_date}, but limit is "
                        f"{inbox.max_emails_per_day} emails per day. Exceeds limit by {count - inbox.max_emails_per_day}."
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
        
        log.info(
            f"Queue efficiency: {total_used}/{total_capacity} slots used "
            f"({((total_used/total_capacity)*100):.1f}% utilization), "
            f"{self.result.total_empty_slots} empty slots"
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
