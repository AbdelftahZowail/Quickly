import asyncio
import random
from datetime import date, timedelta
from sqlalchemy import select, delete
from app.database import AsyncSessionLocal
from app.models import Lead, Campaign, CampaignInbox, CampaignLead, Inbox, Sequence, QueueSlot, EmailLog, LeadReply
from app.queue_logic import recalculate_queue
from app import time as time_provider

# ========== CONFIGURATION ==========
# Adjust these values to control the test data generation
NUM_LEADS = 300                  # How many test leads to create
NUM_CAMPAIGNS = 5               # How many test campaigns to create
NUM_SEQUENCES_PER_CAMPAIGN = 3  # How many email sequences per campaign
# WAIT_DAYS_BETWEEN_SEQUENCES = [1]  # Days to wait between follow-up sequences (cycles in order)
WAIT_DAYS_BETWEEN_SEQUENCES = [2, 3, 1, 3, 3, 5, 3, 3, 1, 1]  # Days to wait between follow-up sequences (cycles in order)
# ===================================

TEST_PREFIX = "[TEST]"
async def main():
    session = AsyncSessionLocal()
    try:
        print("Starting test data population...")

        # 1. Get or Create Inbox
        print("Checking for existing inboxes...")
        result = await session.execute(select(Inbox))
        inboxes = result.scalars().all()
        
        created_inbox_ids = []
        if len(inboxes) < 7: # If we have less than 5 inboxes, create dummies until its 5 for testing
            for i in range(len(inboxes) + 1, 8):
                print("No inboxes found. Creating a dummy test inbox...")
                dummy_inbox = Inbox(
                    email=f"test_inbox_{i}@example.com",
                    display_name=f"Test Inbox {i}",
                    provider="resend",
                    max_emails_per_day=10,
                    wait_minutes_between=10
                )
                session.add(dummy_inbox)
                await session.commit()
                await session.refresh(dummy_inbox)
                inboxes = [dummy_inbox]
                created_inbox_ids.append(dummy_inbox.id)
        else:
            print(f"Found {len(inboxes)} existing inboxes. Will use them for test campaigns.")

        # 2. Create Test Campaigns
        created_campaign_ids = []
        print(f"Creating {NUM_CAMPAIGNS} test campaigns...")
        
        # Calculate Sending Days (All days EXCEPT today)
        today_weekday = time_provider.today().weekday()
        sending_days = [d for d in range(7) if d != today_weekday]
        print(f"Configuring campaigns to send on days expected to exclude today ({today_weekday}): {sending_days}")

        n_cycle = 0  # Initialize cycle counter for sequences
        for i in range(1, NUM_CAMPAIGNS + 1):
            camp_name = f"{TEST_PREFIX} Campaign {i}"
            # Check if exists
            exists = await session.execute(select(Campaign).where(Campaign.name == camp_name))
            if exists.scalar_one_or_none():
                print(f"Campaign '{camp_name}' already exists. Skipping creation.")
                continue

            camp = Campaign(
                name=camp_name,
                paused=False,
                sending_days=sending_days, # Excludes today so it starts tomorrow
                sending_hours_start="09:00",
                sending_hours_end="17:00",
                wait_minutes_between=1
            )
            session.add(camp)
            await session.commit()
            await session.refresh(camp)
            created_campaign_ids.append(camp.id)

            # Link Inboxes
            for idx, inbox in enumerate(inboxes):
                ci = CampaignInbox(campaign_id=camp.id, inbox_id=inbox.id, position=idx)
                session.add(ci)
            
            # Create Sequences
            for seq_idx in range(NUM_SEQUENCES_PER_CAMPAIGN):
                wait_days = 0 if seq_idx == 0 else WAIT_DAYS_BETWEEN_SEQUENCES[n_cycle]
                n_cycle = (n_cycle + 1) % len(WAIT_DAYS_BETWEEN_SEQUENCES)  # Cycle through wait days
                subject = f"Test Subject {i}" if seq_idx == 0 else None  # First email has subject, follow-ups are replies
                seq = Sequence(
                    campaign_id=camp.id,
                    position=seq_idx,
                    subject=subject,
                    body=f"Hello, this is test email #{seq_idx + 1} for campaign {i}.",
                    wait_days_after_previous=wait_days
                )
                session.add(seq)
            await session.commit()
        
        # 3. Create Test Leads
        created_lead_ids = []
        print(f"Creating {NUM_LEADS} test leads...")
        new_leads = []
        for i in range(1, NUM_LEADS + 1):
            email = f"test_lead_{i:03d}@example.com"
            # Check if exists
            exists = await session.execute(select(Lead).where(Lead.email == email))
            if exists.scalar_one_or_none():
                # Get ID
                l = (await session.execute(select(Lead).where(Lead.email == email))).scalar_one()
                created_lead_ids.append(l.id)
                continue

            lead = Lead(
                email=email,
                name=f"Test User {i}",
                status="active"
            )
            session.add(lead)
            new_leads.append(lead)
        
        if new_leads:
            await session.commit()
            for l in new_leads:
                created_lead_ids.append(l.id)

        # 4. Assign Leads to Campaigns
        print("Assigning leads to campaigns...")
        
        # Get campaigns to assign to
        # We might have skipped creation, so fetch them by name or ID
        if created_campaign_ids:
            campaigns = await session.execute(select(Campaign).where(Campaign.id.in_(created_campaign_ids)))
            campaigns = campaigns.scalars().all()
        else:
            # Fallback if we re-ran and no new IDs
             campaigns = await session.execute(select(Campaign).where(Campaign.name.like(f"{TEST_PREFIX}%")))
             campaigns = campaigns.scalars().all()

        if not campaigns:
            print("No test campaigns found!")
            return

        for lead_id in created_lead_ids:
            # Randomly pick a campaign
            camp = random.choice(campaigns)
            
            # Check if already assigned
            existing = await session.execute(select(CampaignLead).where(
                CampaignLead.campaign_id == camp.id,
                CampaignLead.lead_id == lead_id
            ))
            if existing.scalar_one_or_none():
                continue

            cl = CampaignLead(campaign_id=camp.id, lead_id=lead_id)
            session.add(cl)
            await session.commit()
            await session.refresh(cl)

            # Schedule logic - Let it calculate based on sending_days (which excludes today)
            await recalculate_queue(session, camp.id)
            # print(f"Assigned Lead {lead_id} to Campaign {camp.id} - sending days exclude today")

        print("Done! Test data populated.")
        print(f"Created/Found {len(created_lead_ids)} leads.")
        print(f"Created/Found {len(created_campaign_ids)} campaigns.")
        print("To delete test data, run this script with --delete argument.")

    except Exception as e:
        print(f"An error occurred: {e}")
        await session.rollback()
    finally:
        await session.close()


async def delete_test_data():
    session = AsyncSessionLocal()
    try:
        print("Deleting test data...")
        
        # Delete CampaignLeads for test campaigns
        # First find test campaigns
        camps = await session.execute(select(Campaign).where(Campaign.name.like(f"{TEST_PREFIX}%")))
        camps = camps.scalars().all()
        camp_ids = [c.id for c in camps]
        
        if camp_ids:
             # Delete QueueSlots ? Cascade should handle it but let's be safe
             # Actually models have cascade="all, delete-orphan", so deleting Campaign should clear slots and CLs
             pass

        # Delete Test Campaigns
        print(f"Deleting {len(camps)} test campaigns...")
        for c in camps:
            await session.delete(c)
        
        # Delete Test Leads
        leads = await session.execute(select(Lead).where(Lead.email.like("test_lead_%@example.com")))
        leads = leads.scalars().all()
        print(f"Deleting {len(leads)} test leads...")
        for l in leads:
            await session.delete(l)

        # Delete Dummy Inbox if it exists and is unused? 
        # Maybe safer not to delete inbox if it might have been used for other things?
        # But we created it specifically with known email 'test_inbox@example.com'
        inbox = await session.execute(select(Inbox).where(Inbox.email == "test_inbox@example.com"))
        inbox = inbox.scalar_one_or_none()
        if inbox:
             print("Deleting dummy test inbox...")
             await session.delete(inbox)

        await session.commit()
        print("Test data deleted.")

    except Exception as e:
        print(f"Error deleting data: {e}")
        await session.rollback()
    finally:
        await session.close()

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--delete":
        asyncio.run(delete_test_data())
    else:
        asyncio.run(main())
