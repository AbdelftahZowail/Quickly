import os
import sys

# when this helper is invoked directly (e.g. ``python smoke_test/populate_test_data.py``)
# the interpreter adds the *smoke_test* directory to sys.path as entry 0, which means
# sibling packages such as ``app`` are not visible.  The tests and other callers
# execute the module with ``-m`` or from the project root, so this isn’t an issue
# for normal usage, but being invoked as a script is convenient at the shell.  To
# make that work we explicitly prepend the workspace root to sys.path here.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import asyncio
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models import Lead, Campaign, Inbox, Sequence
from app import time as time_provider
from app.schemas import InboxCreate, CampaignCreate, CampaignLeadAdd, SequenceCreate
from app.routers.inbox import create_inbox
from app.routers.campaigns import create_campaign, create_sequence, bulk_add_leads_to_campaign
from app.routers import schedule as schedule_router
from app.routers.schedule import recalculate_all_campaigns

# ========== CONFIGURATION ==========
# Adjust these values to control the test data generation
NUM_LEADS = 150                  # How many test leads to create
NUM_CAMPAIGNS = 3               # How many test campaigns to create
# NOTE: sequence position 0 is the first email, follow-ups are replies in thread.
NUM_SEQUENCES_PER_CAMPAIGN = 3  # How many email sequences per campaign
# WAIT_DAYS_BETWEEN_SEQUENCES = [1]  # Days to wait between follow-up sequences (cycles in order)
WAIT_DAYS_BETWEEN_SEQUENCES = [2, 3, 1, 3, 3, 5, 3, 3, 1, 1]  # Days to wait between follow-up sequences (cycles in order)
INBOX_NUMBER = 3  # How many inboxes to be available for campaigns to link to
# Whether to wait for background queue build to finish before exiting
WAIT_FOR_RECALC = True
# ===================================

TEST_PREFIX = "[TEST]"
TEST_INBOX_PREFIX = "test_inbox_"
TEST_DOMAIN = "example.com"
async def main(skip_duplicates: bool = True):
    session = AsyncSessionLocal()
    try:
        print("Starting test data population...")

        # 1. Get or Create Inbox
        print("Checking for existing inboxes...")
        target_emails = [f"{TEST_INBOX_PREFIX}{i}@{TEST_DOMAIN}" for i in range(1, INBOX_NUMBER + 1)]
        result = await session.execute(select(Inbox).where(Inbox.email.in_(target_emails)))
        inboxes = result.scalars().all()

        inbox_by_email = {i.email: i for i in inboxes}
        for i in range(1, INBOX_NUMBER + 1):
            email = f"{TEST_INBOX_PREFIX}{i}@{TEST_DOMAIN}"
            if email in inbox_by_email:
                continue
            print(f"Creating test inbox {email}...")
            inbox_data = InboxCreate(
                email=email,
                display_name=f"Test Inbox {i}",
                provider="gmail",
                max_emails_per_day=20,
                wait_minutes_between=8,
                max_jitter_seconds=120,
                tracking_domain=None,
                ramp_up_enabled=False,
                ramp_up_period_days=42,
            )
            inbox = await create_inbox(inbox_data, db=session)
            inboxes.append(inbox)

        if inboxes:
            print(f"Using {len(inboxes)} test inboxes for campaigns.")

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
            exists = await session.execute(select(Campaign).where(Campaign.name == camp_name))
            existing = exists.scalar_one_or_none()

            if not existing:
                camp_data = CampaignCreate(
                    name=camp_name,
                    inbox_ids=[i.id for i in inboxes],
                    sending_days=sending_days,  # Excludes today so it starts tomorrow
                    sending_hours_start="09:00",
                    sending_hours_end="17:00",
                    wait_minutes_between=5,
                    stop_on_reply=True,
                    paused=False,
                    priority=i - 1,
                    track_opens=False,
                    track_clicks=False,
                    add_unsubscribe_header=True,
                    send_first_as_text=False,
                    send_all_as_text=False,
                    timezone=None,
                    match_lead_provider=True,
                )
                camp_resp = await create_campaign(camp_data, db=session)
                created_campaign_ids.append(camp_resp.id)
                camp_id = camp_resp.id
            else:
                camp_id = existing.id
                print(f"Campaign '{camp_name}' already exists. Leaving settings unchanged.")

            # Create sequences (only missing positions)
            seq_result = await session.execute(
                select(Sequence).where(Sequence.campaign_id == camp_id)
            )
            existing_seqs = {s.position for s in seq_result.scalars().all()}
            for seq_idx in range(NUM_SEQUENCES_PER_CAMPAIGN):
                if seq_idx in existing_seqs:
                    continue
                wait_days = 0 if seq_idx == 0 else WAIT_DAYS_BETWEEN_SEQUENCES[n_cycle]
                n_cycle = (n_cycle + 1) % len(WAIT_DAYS_BETWEEN_SEQUENCES)
                subject = f"Test Subject {i}" if seq_idx == 0 else None
                seq_data = SequenceCreate(
                    position=seq_idx,
                    subject=subject,
                    body=f"Hello, this is test email #{seq_idx + 1} for campaign {i}.",
                    wait_days_after_previous=wait_days,
                    is_html=False,
                    preview_text=None,
                )
                await create_sequence(camp_id, seq_data, db=session)
        
        # 3. Create Test Leads
        print(f"Preparing {NUM_LEADS} test leads...")
        leads_by_campaign: dict[int, list[CampaignLeadAdd]] = {}
        campaigns_result = await session.execute(
            select(Campaign).where(Campaign.name.like(f"{TEST_PREFIX}%"))
        )
        campaigns = campaigns_result.scalars().all()

        if not campaigns:
            print("No test campaigns found!")
            return

        for i in range(1, NUM_LEADS + 1):
            email = f"test_lead_{i:03d}@{TEST_DOMAIN}"
            campaign = campaigns[(i - 1) % len(campaigns)]
            leads_by_campaign.setdefault(campaign.id, []).append(
                CampaignLeadAdd(
                    email=email,
                    name=f"Test User {i}",
                    custom_data={"source": "smoke_test", "batch": "populate_test_data"},
                )
            )

        # 4. Bulk-add leads to campaigns
        print("Adding leads to campaigns using bulk endpoint...")
        total_added = 0
        total_duplicates = 0
        for campaign_id, leads in leads_by_campaign.items():
            if not leads:
                continue
            result = await bulk_add_leads_to_campaign(
                campaign_id,
                leads_data=leads,
                skip_duplicates=skip_duplicates,
                verify_emails=False,
                db=session,
            )
            total_added += result.get("added", 0)
            total_duplicates += len(result.get("duplicate_leads", []) or [])
            print(f"Campaign {campaign_id}: added {result.get('added', 0)} lead(s)")

        await session.commit()

        # 5. Run global recalculation endpoint (fresh session)
        print("Triggering global recalculation...")
        async with AsyncSessionLocal() as recalc_session:
            recalc_result = await recalculate_all_campaigns(db=recalc_session)
            await recalc_session.commit()
        print(f"Recalculate result: {recalc_result}")
        if WAIT_FOR_RECALC and recalc_result.get("background_started"):
            # Wait briefly for the background recalculation task to complete.
            # This uses the module-level task variable in the schedule router.
            for _ in range(60):
                task = getattr(schedule_router, "_background_recalc_task", None)
                if not task or task.done():
                    break
                await asyncio.sleep(1)
            else:
                print("Background recalculation still running; check /api/schedule/status for progress.")

        print("Done! Test data populated.")
        print(f"Added {total_added} lead(s) across {len(campaigns)} campaigns.")
        if skip_duplicates and total_added == 0 and total_duplicates > 0:
            print("All leads were skipped as duplicates. Run with --allow-duplicates or --reset.")
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
        leads = await session.execute(select(Lead).where(Lead.email.like(f"test_lead_%@{TEST_DOMAIN}")))
        leads = leads.scalars().all()
        print(f"Deleting {len(leads)} test leads...")
        for l in leads:
            await session.delete(l)

        # Delete dummy test inboxes created by this script
        inboxes = await session.execute(select(Inbox).where(Inbox.email.like(f"{TEST_INBOX_PREFIX}%@{TEST_DOMAIN}")))
        inboxes = inboxes.scalars().all()
        if inboxes:
            print(f"Deleting {len(inboxes)} test inboxes...")
            for inbox in inboxes:
                await session.delete(inbox)

        await session.commit()
        print("Test data deleted.")

    except Exception as e:
        print(f"Error deleting data: {e}")
        await session.rollback()
    finally:
        await session.close()


async def reset_test_data(skip_duplicates: bool = True):
    await delete_test_data()
    await main(skip_duplicates=skip_duplicates)

if __name__ == "__main__":
    import sys
    args = set(sys.argv[1:])
    if "--delete" in args:
        asyncio.run(delete_test_data())
    elif "--reset" in args:
        asyncio.run(reset_test_data(skip_duplicates="--allow-duplicates" not in args))
    else:
        asyncio.run(main(skip_duplicates="--allow-duplicates" not in args))
