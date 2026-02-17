# Queue Integrity Issues & Recommended Fixes

## Summary
The queue system has several scenarios where changes to campaigns, inboxes, or leads do NOT trigger queue recalculation, leading to corrupted or invalid schedules.

---

## Critical Issues

### Issue 1: Campaign Inbox Changes Don't Recalculate Queue
**File:** `app/routers/campaigns.py` line 123-128

**Problem:**
When you update a campaign's inbox list (add/remove inboxes), existing queue slots are NOT recalculated. This causes:
- Orphaned slots referencing removed inboxes
- New inboxes not being used for existing leads
- Broken inbox persistence (leads might switch inboxes incorrectly)

**Example Scenario:**
1. Campaign has 100 leads scheduled across Inbox A and Inbox B
2. You remove Inbox B from campaign
3. All 150 slots assigned to Inbox B remain in the queue
4. Send job tries to send from Inbox B which is no longer in the campaign

**Fix:**
Add recalculation after inbox_ids update:
```python
if data.inbox_ids is not None:
    if not data.inbox_ids:
        raise HTTPException(400, "At least one inbox required")
    await db.execute(delete(CampaignInbox).where(CampaignInbox.campaign_id == campaign_id))
    await db.flush()
    for pos, inbox_id in enumerate(data.inbox_ids):
        db.add(CampaignInbox(campaign_id=campaign_id, inbox_id=inbox_id, position=pos))
    await db.flush()
    # NEW: Recalculate queue to reassign slots
    await recalculate_queue_after_sequence_change(db, campaign_id)
```

---

### Issue 2: Sending Window Changes Don't Recalculate Times
**File:** `app/routers/campaigns.py` line 129-134

**Problem:**
When you change `sending_days`, `sending_hours_start`, or `sending_hours_end`, existing queue slots keep their old scheduled times. This causes:
- Emails scheduled outside new sending hours (e.g., 6 PM when new limit is 5 PM)
- Emails scheduled on weekends when you change to weekdays-only
- Following emails still spaced by old wait_days, but on wrong dates

**Example Scenario:**
1. Campaign sends Mon-Sun 9 AM - 9 PM
2. You have 300 slots scheduled including weekends and evening slots
3. You change to Mon-Fri 9 AM - 5 PM
4. All 300 slots remain with their old times (some on weekends, some at 8 PM)

**Fix:**
Add recalculation when sending window changes:
```python
schedule_changed = False
if data.sending_days is not None:
    campaign.sending_days = data.sending_days
    schedule_changed = True
if data.sending_hours_start is not None:
    campaign.sending_hours_start = data.sending_hours_start
    schedule_changed = True
if data.sending_hours_end is not None:
    campaign.sending_hours_end = data.sending_hours_end
    schedule_changed = True

if schedule_changed:
    await db.flush()
    await recalculate_queue_after_sequence_change(db, campaign_id)
```

---

### Issue 3: Inbox Rate Limits Don't Trigger Recalculation
**File:** `app/routers/inbox.py` line 54-59

**Problem:**
When you change an inbox's `max_emails_per_day` or `wait_minutes_between`, campaigns using that inbox don't recalculate their queues. This causes:
- Days scheduled beyond new daily limit
- Emails scheduled too close together (violates new wait_minutes)
- Wrong estimated send times

**Example Scenario:**
1. Inbox A has max_emails_per_day=50, wait_minutes_between=5
2. 3 campaigns use Inbox A, total 60 emails scheduled on Monday
3. You reduce to max_emails_per_day=20
4. Monday still has 60 emails scheduled (violates the limit)

**Fix:**
This is more complex - need to recalculate ALL campaigns using this inbox:
```python
@router.patch("/{inbox_id}", response_model=InboxResponse)
async def update_inbox(inbox_id: int, data: InboxUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Inbox).where(Inbox.id == inbox_id))
    inbox = result.scalar_one_or_none()
    if not inbox:
        raise HTTPException(404, "Inbox not found")
    
    capacity_changed = False
    if data.display_name is not None:
        inbox.display_name = data.display_name
    if data.max_emails_per_day is not None:
        inbox.max_emails_per_day = data.max_emails_per_day
        capacity_changed = True
    if data.wait_minutes_between is not None:
        inbox.wait_minutes_between = data.wait_minutes_between
        capacity_changed = True
    if data.provider is not None:
        inbox.provider = data.provider
    await db.flush()
    
    # NEW: If capacity changed, recalculate all campaigns using this inbox
    if capacity_changed:
        from app.queue_logic import recalculate_queue_after_sequence_change
        campaign_result = await db.execute(
            select(CampaignInbox.campaign_id)
            .where(CampaignInbox.inbox_id == inbox_id)
            .distinct()
        )
        campaign_ids = [cid for (cid,) in campaign_result.all()]
        log.info("Inbox %s capacity changed; recalculating %d campaigns", inbox_id, len(campaign_ids))
        for cid in campaign_ids:
            await recalculate_queue_after_sequence_change(db, cid)
    
    await db.refresh(inbox)
    return inbox
```

---

## Medium Issues

### Issue 4: Lead Status Changes Don't Stop Queue
**Problem:**
When a lead's status changes to "bounced" or "unsubscribed", their queue slots remain active.

**Fix:**
Add status check in send job (app/jobs.py) before sending:
```python
# In run_send_job(), before sending each email:
if lead.status in ("bounced", "unsubscribed", "replied"):
    log.info("Skipping lead %s (status=%s)", lead.id, lead.status)
    # Optionally delete remaining queue slots for this lead
    await session.execute(
        delete(QueueSlot).where(
            QueueSlot.campaign_lead_id == cl.id,
            QueueSlot.sequence_index >= slot.sequence_index
        )
    )
    continue
```

Also add a lead status update endpoint that removes queue slots:
```python
# In app/routers/leads.py
@router.patch("/{lead_id}/status")
async def update_lead_status(
    lead_id: int, 
    status: str,  # "active", "bounced", "unsubscribed", "replied"
    db: AsyncSession = Depends(get_db)
):
    lead = await db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    
    lead.status = status
    
    # If lead is no longer active, remove all their pending queue slots
    if status in ("bounced", "unsubscribed"):
        await db.execute(
            delete(QueueSlot)
            .where(
                QueueSlot.campaign_lead_id.in_(
                    select(CampaignLead.id).where(CampaignLead.lead_id == lead_id)
                )
            )
        )
        log.info("Removed queue slots for lead %s (status=%s)", lead_id, status)
    
    await db.flush()
    return {"ok": True}
```

---

## Testing Recommendations

Create integration tests for these scenarios:

### Test 1: Inbox Removal
```python
async def test_remove_inbox_recalculates():
    # Create campaign with 2 inboxes
    # Add 50 leads (slots distributed across both inboxes)
    # Update campaign to use only 1 inbox
    # Verify: all 150 slots now use only the remaining inbox
    # Verify: inbox persistence maintained (same lead always uses same inbox)
```

### Test 2: Sending Window Change
```python
async def test_sending_window_change():
    # Create campaign with Mon-Sun 9AM-9PM
    # Add leads, verify some scheduled on weekends
    # Change to Mon-Fri 9AM-5PM
    # Verify: no slots on weekends
    # Verify: no slots after 5PM
    # Verify: wait_days still respected
```

### Test 3: Inbox Capacity Reduction
```python
async def test_reduce_inbox_capacity():
    # Create inbox with max=50, wait=5
    # Create campaign using this inbox
    # Add 80 leads (causing multiple days)
    # Reduce inbox max to 20
    # Verify: no day has more than 20 emails
    # Verify: emails redistributed to later days
```

### Test 4: Lead Status Change
```python
async def test_bounced_lead_stops_sending():
    # Create campaign with 3 sequences
    # Add lead, send first email
    # Mark lead as bounced
    # Verify: remaining 2 slots deleted
    # Run send job
    # Verify: no attempts to send to bounced lead
```

---

## Performance Considerations

**Warning:** Recalculating queues can be expensive for large campaigns.

- Campaign with 1,000 leads × 3 sequences = 3,000 slots to recalculate
- Each recalculation queries database multiple times per lead
- Could take 10-30 seconds for very large campaigns

**Recommendations:**
1. Add a progress indicator/toast in UI when recalculation is triggered
2. Consider running recalculation as a background task for campaigns with >500 leads
3. Add a setting to disable auto-recalculation and let admin trigger it manually

---

## Alternative: Validation Warning System

Instead of auto-recalculation (which can be slow), you could:

1. Add a `queue_valid` boolean flag to Campaign model
2. Set `queue_valid = False` whenever settings change
3. Show warning in UI: "Queue needs recalculation after settings change"
4. Add explicit "Recalculate Queue" button that admin must click
5. This gives admin control over when to pay the performance cost

---

## Migration Plan

1. **Immediate:** Add validation checks to prevent invalid sends:
   - In send job, skip slots outside sending window
   - In send job, skip slots for bounced/unsubscribed leads
   - Log warnings when these occur

2. **Short-term:** Implement auto-recalculation for Issues 1-3
   - Add to campaign update endpoint
   - Add to inbox update endpoint
   - Add comprehensive tests

3. **Long-term:** Optimize recalculation performance
   - Batch database operations
   - Use bulk inserts/deletes
   - Add caching for campaign settings
   - Consider materialized views for slot counts

---

## Conclusion

**Your intuition was correct** - the queue WILL get corrupted with the current implementation when:
- Inboxes are added/removed from campaigns
- Sending schedules are changed
- Inbox rate limits are modified
- Leads are marked as bounced/unsubscribed

The fixes are straightforward but require careful testing. The validation script you already have (validate_scheduled_emails.py) is excellent - expand it to catch these additional scenarios.
