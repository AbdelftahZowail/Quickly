# Queue Recalculation — triggers & actions

Last updated: 2026-02-19

## Purpose
Explain every situation where the campaign delivery queue must be recalculated (fully or partially), why it matters, and the recommended handling strategy.

---

## Quick summary ✅
- Recalculate when campaign scheduling, routing (inbox), targeting, or lead availability changes.
- Prefer **targeted/partial** recalculation when possible; **full** rebuild when rules, algorithms, or data integrity are changed.
- Always debounce/coalesce rapid, repeated updates and run recalculation as a background job with a per-campaign lock.

---

## Triggers and recommended action (detailed)
For each trigger below, the Recommended Action column indicates the minimal safe operation.

1. Campaign status changes
   - Examples: `paused` → `active` (unpause), `active` → `paused`, `archived`/`unarchived`.
   - Why: unpause needs rescheduling because send windows passed while paused; pause must prevent new sends.
   - Recommended action: 
     - Unpause: **Full recalculation** for that campaign's pending leads (reschedule according to now). 
     - Pause: **Targeted** — mark future scheduled sends as paused/held (no new reservations) and persist state.

2. Campaign schedule/time changes
   - Fields: start_date, end_date, daily send windows, timezone, working hours.
   - Why: scheduled send-times may fall outside new windows or outside campaign active dates.
   - Recommended action: **Full recalculation** of all queued/eligible leads for that campaign.

3. Campaign sequence/step changes
   - Examples: add/remove/reorder steps, change step delay or retry policy, change step type.
   - Why: step timing and downstream sends depend on sequence configuration.
   - Recommended action: **Full recalculation** for leads still in campaign (recompute step offsets and next-send dates).

4. Campaign targeting or filter changes
   - Examples: segment rules, query/filters, dedupe rules, audience size change.
   - Why: some leads may newly match or no longer match — queue must reflect membership.
   - Recommended action: **Targeted recalculation**: update only affected leads (add/reschedule new matches; cancel/remove those no longer matching).

5. Inbox changes (routing & delivery)
   - Examples: inbox toggled off/on, credentials revoked/updated, sending-rate/quota changes, per-inbox capacity changed.
   - Why: disabled or failing inboxes must not be assigned sends; active inboxes may accept queued sends.
   - Recommended action: 
     - Inbox disabled/fails: **Reassign** pending reservations to other inboxes where possible (partial recalculation for affected reservations). If no inbox available, hold or reschedule.
     - Inbox enabled/credentials fixed: **Targeted recalculation** to reassign/resume reservations.

6. Lead-level changes that affect scheduling
   - Examples: lead timezone, `do_not_contact`, working-hours, contact preferences, opted-out/bounced, lead removed/merged.
   - Why: availability and eligibility change per-lead.
   - Recommended action: **Targeted recalculation** for the specific lead(s): cancel or reschedule as required.

7. System / account-level policy changes
   - Examples: global send-rate limits, account quotas, holiday calendars, system timezone/DST policy.
   - Why: global limits affect allocation across campaigns/inboxes.
   - Recommended action: **Full or scoped recalculation** depending on scope of the policy change (often full across affected campaigns).

8. Manual operator actions
   - Examples: manual reschedule, cancel, move-to-top, reserve/unreserve leads, admin "rebuild queue" request.
   - Recommended action: **Targeted recalculation** for affected leads or campaign(s). Log the operator action.

9. Data recovery or migrations
   - Examples: DB restore, import, code deploy that changes scheduling logic or bug-fix to `queue_logic.py`.
   - Why: stored scheduling may be out-of-date or inconsistent with new logic.
   - Recommended action: **Full rebuild** of all queues (safety-first).

10. Retry/backoff and error handling changes
    - Examples: change of retry intervals, retry limits, bounce-handling or backoff rules.
    - Recommended action: **Targeted recalculation** for leads with pending retries; consider full campaign recalculation if rules change broadly.

11. Test-mode / staging toggles
    - Example: switching campaign into/out-of test-mode or sandbox.
    - Recommended action: **Targeted recalculation** — test-mode leads should be isolated or rescheduled.

12. Other time-sensitive events
    - Examples: Daylight Saving Time shift, server timezone change, major external calendar updates.
    - Recommended action: **Audit + Partial/Full** depending on how time calculations are performed (if timezone-aware scheduling is used, prefer full audit/recalc for safety).
13. server restarts
---

## Recalculation scope definitions
- Full recalculation: recompute schedule for every lead in the campaign (used for schedule/sequence/logic changes or DB restores).
- Targeted recalculation: recompute only the subset of leads affected by the change (preferred when possible for performance).
- Reassignment-only: only change which inbox handles already-reserved sends.
- No-op: configuration changes that do not affect timing or eligibility (rare) — document and skip.

---

## Operational guidance 🔧
- Debounce/coalesce: batch rapid updates for the same campaign (suggest 2–5s configurable window).
- Locking: use a per-campaign distributed lock (Redis/DB row lock) during recalculation to avoid race conditions.
- Background job: run recalculation asynchronously (push job to worker queue) and return a short response to the caller.
- Idempotence: make recalculation jobs idempotent so retries are safe.
- Audit & metrics: log reason, origin (user/API/system), affected-count, duration; emit metrics for visibility.
- Safety: when inboxes are unavailable, mark reservations as `held` (do not discard) unless explicitly cancelled.

---

## Where to hook triggers (recommended integration points)
- Campaign updates: `app/routers/campaigns.py` → trigger recalculation on status/schedule/sequence/target changes.
- Inbox updates: `app/routers/inboxes.py` → trigger reassignment/recalc on enable/disable or credential changes.
- Lead/segment updates: wherever lead membership changes are applied (API or background jobs).
- Queue logic: implement the recalculation worker in `app/queue_logic.py` or `app/jobs.py`.

> Example function to call: `queue_logic.recalculate_campaign(campaign_id, reason="unpause")` (implement if missing).

---

## Suggested tests to add ✅
- `test_recalculate_on_unpause` — unpausing a campaign reschedules pending leads into the next valid windows.
- `test_recalculate_on_inbox_disable` — disabling an inbox reassigns or holds affected reservations.
- `test_recalculate_on_schedule_change` — changing a campaign send window moves scheduled sends outside the new window.
- `test_debounce_coalescing` — multiple quick updates produce a single recalculation job.
- `test_full_rebuild_after_migration` — running full rebuild after scheduling logic change produces expected queue.

Place tests in `tests/` alongside `test_queue_logic.py`.

---

## Acceptance criteria (examples)
- Every campaign/unpause, inbox toggle, schedule/sequence change triggers an appropriate recalculation job.
- Jobs are debounced, locked per-campaign, idempotent, and logged with reason and impact.
- Unit + integration tests cover all trigger types and edge cases.

---

## Implementation checklist (suggested)
1. Add `recalculate_campaign(...)` entrypoint in `app/queue_logic.py`.
2. Hook into `routers/campaigns.py` and `routers/inboxes.py` for the triggers listed above.
3. Add background worker job + per-campaign lock.
4. Add metrics and audit log entries for every recalculation.
5. Add unit/integration tests listed above.

---

If you want, I can: add the `recalculate_campaign` function, wire triggers in the routers, and add the tests listed above. 
