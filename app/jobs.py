"""Background job: send due emails from the queue."""
import logging
import random
import re
import secrets
from datetime import datetime, date, time, timedelta
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore[assignment,misc]

from app.settings_manager import settings
from app.database import AsyncSessionLocal
from app.models import (
    QueueSlot,
    CampaignLead,
    Campaign,
    Sequence,
    SequenceVariant,
    Lead,
    Inbox,
    EmailLog,
    LeadReply,
    GmailAccount,
    LeadUnsubscribeToken,
)
from app.sender import send_email, render_body, get_lead_data, SendResult, SendFailure
from app.webhooks import fire_webhook_event
from app.routers.gmail_oauth import refresh_access_token
from app.app_settings import get_google_oauth_credentials
from app import time as time_provider
from app.queue_logic import _parse_time, compute_effective_daily_limit

log = logging.getLogger(__name__)

# Updated each time run_send_job finishes (for /api/status)
last_send_job_run: datetime | None = None
last_send_job_sent_count: int = 0




def _in_sending_window(now_utc: datetime, campaign: Campaign) -> bool:
    """Check if *now_utc* falls within the campaign's sending days and hours.

    The campaign's ``sending_hours_start`` / ``sending_hours_end`` are expressed
    in its configured timezone (e.g. Africa/Cairo).  We convert ``now_utc`` to
    that timezone before comparing so the gate-check is always correct regardless
    of which UTC offset the server runs at.
    """
    tz_name = getattr(campaign, "timezone", None)
    if ZoneInfo and tz_name:
        try:
            tz = ZoneInfo(tz_name)
            # now_utc is a naive datetime from time_provider.now() which
            # returns server-local time; in Docker that equals UTC.
            now_local = now_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).replace(tzinfo=None)
        except Exception:
            now_local = now_utc
    else:
        now_local = now_utc

    if campaign.sending_days is None or now_local.weekday() not in campaign.sending_days:
        return False
    start = _parse_time(campaign.sending_hours_start or "09:00")
    end = _parse_time(campaign.sending_hours_end or "17:00")
    return start <= now_local.time() <= end


async def run_send_job():
    """Run once: send today's due emails. Slots are per inbox (QueueSlot.inbox_id)."""
    global last_send_job_run, last_send_job_sent_count
    # Use local time so we match queue_logic (slots are stored in local time) and sending window (09:00–17:00 is local)
    now = time_provider.now()
    log.info("Send job running at %s (local)", now.isoformat())

    async with AsyncSessionLocal() as session:
        today = now.date()

        result = await session.execute(select(Inbox).where(Inbox.paused == False))  # noqa: E712
        inboxes = result.scalars().all()

        total_sent = 0
        # Pre-fetch Google OAuth credentials once for all Gmail inboxes
        g_client_id, g_client_secret = await get_google_oauth_credentials(session)

        # Fallback tracking base URL (used when an inbox has no custom domain)
        from app.settings_manager import settings as _settings
        from app.app_settings import get_inbox_tracking_base
        _fallback_tracking_base = _settings.base_url.rstrip("/")

        for inbox in inboxes:
            # compute how many emails already sent today so we enforce a hard
            # daily cap rather than only relying on ``sent_this_inbox`` below.
            # Use the warmup-aware effective limit so ramp-up is respected.
            max_per_day = compute_effective_daily_limit(inbox)
            sent_count_result = await session.execute(
                select(func.count(EmailLog.id))
                .where(
                    EmailLog.inbox_id == inbox.id,
                    func.date(EmailLog.sent_at) == today,
                )
            )
            already_sent = sent_count_result.scalar() or 0
            quota_remaining = max_per_day - already_sent

            result = await session.execute(
                select(QueueSlot, CampaignLead, Campaign, Lead, Sequence)
                .join(CampaignLead, QueueSlot.campaign_lead_id == CampaignLead.id)
                .join(Campaign, CampaignLead.campaign_id == Campaign.id)
                .join(Lead, CampaignLead.lead_id == Lead.id)
                .join(
                    Sequence,
                    (Sequence.campaign_id == Campaign.id)
                    & (Sequence.position == QueueSlot.sequence_index),
                )
                .options(selectinload(Sequence.variants))
                .where(
                    QueueSlot.inbox_id == inbox.id,
                    func.date(QueueSlot.scheduled_date) == today,
                    QueueSlot.scheduled_date <= now,
                )
                .order_by(QueueSlot.scheduled_date, QueueSlot.position_in_day)
            )
            rows = result.all()

            if quota_remaining <= 0:
                if not rows:
                    # An inbox that already exhausted its quota but has no due
                    # work should not emit repeated daily_limit safeguards.
                    continue
                # we would break the daily limit even before sending a single
                # message; try a recalculation to redistribute, then report.
                log.warning("Daily limit hit for inbox %s; attempting recalculation", inbox.email)
                try:
                    from app.routers.schedule import recalculate_all_campaigns
                    await recalculate_all_campaigns(session)
                    # Re-check after recalculation
                    recheck = await session.execute(
                        select(func.count(EmailLog.id)).where(
                            EmailLog.inbox_id == inbox.id,
                            func.date(EmailLog.sent_at) == today,
                        )
                    )
                    still_over = (recheck.scalar() or 0) >= max_per_day
                    if still_over:
                        await fire_webhook_event(
                            session, "daily_limit",
                            {"inbox_id": inbox.id, "inbox_email": inbox.email, "date": str(today),
                             "recalculated": True, "resolved": False},
                        )
                    else:
                        await fire_webhook_event(
                            session, "daily_limit",
                            {"inbox_id": inbox.id, "inbox_email": inbox.email, "date": str(today),
                             "recalculated": True, "resolved": True,
                             "message": "Daily limit was hit but resolved after recalculation"},
                        )
                        # Update quota_remaining after recalc
                        quota_remaining = max_per_day - (recheck.scalar() or 0)
                        if quota_remaining > 0:
                            continue  # retry this inbox with fresh capacity
                except Exception as e:
                    log.error("Recalculation after daily_limit failed: %s", e)
                    await fire_webhook_event(
                        session, "daily_limit",
                        {"inbox_id": inbox.id, "inbox_email": inbox.email, "date": str(today)},
                    )
                continue

            if not rows:
                continue

            # For Gmail inboxes, pre-fetch and refresh the access token
            gmail_token = ""
            ga_result = await session.execute(
                select(GmailAccount).where(GmailAccount.inbox_id == inbox.id)
            )
            ga = ga_result.scalar_one_or_none()
            if ga:
                # Refresh token if expired or within 5 min of expiry
                if ga.token_expiry and ga.token_expiry <= time_provider.utcnow() + timedelta(minutes=5):
                    refreshed = refresh_access_token(ga, g_client_id, g_client_secret)
                    if refreshed:
                        await session.flush()
                    else:
                        log.error("Gmail token refresh failed for inbox %s (%s)", inbox.id, inbox.email)
                        await fire_webhook_event(
                            session,
                            "token_expired",
                            {"inbox_id": inbox.id, "inbox_email": inbox.email},
                        )
                        continue
                gmail_token = ga.access_token
            else:
                log.warning("Gmail inbox %s (%s) has no GmailAccount — skipping", inbox.id, inbox.email)
                continue

            sent_this_inbox = 0
            # Use the warmup-aware effective limit as the per-inbox rate cap.
            max_per_day = compute_effective_daily_limit(inbox)

            # compute last sent timestamp so we can enforce the wait-minutes
            last_sent_time = None

            # Per-inbox tracking base URL — custom domain takes priority
            inbox_tracking_base = get_inbox_tracking_base(inbox, _fallback_tracking_base)
            last_sent_res = await session.execute(
                select(EmailLog.sent_at)
                .where(EmailLog.inbox_id == inbox.id)
                .order_by(EmailLog.sent_at.desc())
                .limit(1)
            )
            last_sent_time = last_sent_res.scalar_one_or_none()

            for slot, cl, campaign, lead, sequence in rows:
                # HARD LIMIT: daily quota
                if sent_this_inbox >= quota_remaining:
                    await fire_webhook_event(
                        session,
                        "daily_limit",
                        {"inbox_id": inbox.id, "inbox_email": inbox.email, "date": str(today)},
                    )
                    break

                # HARD LIMIT: rate/minutes between messages
                if last_sent_time is not None:
                    delta = now - last_sent_time
                    required = timedelta(minutes=inbox.wait_minutes_between)
                    # allow up to 20 second of slack before firing rate_limit
                    if delta + timedelta(seconds=20) < required:
                        # Try recalculation to spread slots out
                        try:
                            from app.routers.schedule import recalculate_all_campaigns
                            await recalculate_all_campaigns(session)
                            # Re-fetch the last sent time after recalc
                            recheck_res = await session.execute(
                                select(EmailLog.sent_at)
                                .where(EmailLog.inbox_id == inbox.id)
                                .order_by(EmailLog.sent_at.desc())
                                .limit(1)
                            )
                            new_last = recheck_res.scalar_one_or_none()
                            new_delta = now - new_last if new_last else delta
                            if new_delta + timedelta(seconds=20) < required:
                                await fire_webhook_event(
                                    session, "rate_limit",
                                    {"inbox_id": inbox.id, "inbox_email": inbox.email,
                                     "last_sent": (new_last or last_sent_time).isoformat(),
                                     "now": now.isoformat(),
                                     "wait_minutes": inbox.wait_minutes_between,
                                     "recalculated": True, "resolved": False},
                                )
                            else:
                                await fire_webhook_event(
                                    session, "rate_limit",
                                    {"inbox_id": inbox.id, "inbox_email": inbox.email,
                                     "last_sent": (new_last or last_sent_time).isoformat(),
                                     "now": now.isoformat(),
                                     "wait_minutes": inbox.wait_minutes_between,
                                     "recalculated": True, "resolved": True,
                                     "message": "Rate limit was hit but resolved after recalculation"},
                                )
                                last_sent_time = new_last
                                continue  # try next slot
                        except Exception as e:
                            log.error("Recalculation after rate_limit failed: %s", e)
                            await fire_webhook_event(
                                session, "rate_limit",
                                {"inbox_id": inbox.id, "inbox_email": inbox.email,
                                 "last_sent": last_sent_time.isoformat(),
                                 "now": now.isoformat(),
                                 "wait_minutes": inbox.wait_minutes_between},
                            )
                        break
                if getattr(campaign, 'paused', False):
                    continue  # Skip paused campaigns
                if not _in_sending_window(now, campaign):
                    break

                if lead.status not in ("active",):
                    continue
                # Skip leads with invalid/risky email verification
                if getattr(lead, 'email_verification_status', None) in ("invalid", "risky"):
                    continue
                # Skip leads whose sending is paused for this campaign
                # (e.g. marked not_interested by the AI classifier).
                if getattr(cl, 'sending_paused', False):
                    continue
                if campaign.stop_on_reply:
                    reply_check = await session.execute(
                        select(LeadReply).where(
                            LeadReply.lead_id == lead.id,
                            LeadReply.campaign_id == campaign.id,
                        )
                    )
                    if reply_check.scalar_one_or_none():
                        continue

                reply_to_msg_id = None
                prev_thread_id = None
                references_chain = None

                # ── A/B variant selection ─────────────────────────────────────
                chosen_variant_id = None
                seq_subject      = sequence.subject
                seq_body         = sequence.body
                seq_is_html      = sequence.is_html
                seq_preview_text = getattr(sequence, 'preview_text', None)

                enabled_variants = [
                    v for v in getattr(sequence, 'variants', []) if v.enabled
                ]
                if enabled_variants:
                    # options: None = default content, or any enabled variant
                    options = [None] + enabled_variants
                    chosen = random.choice(options)
                    if chosen is not None:
                        chosen_variant_id = chosen.id
                        if chosen.subject is not None:
                            seq_subject = chosen.subject
                        if chosen.body:
                            seq_body = chosen.body
                        if chosen.is_html is not None:
                            seq_is_html = chosen.is_html
                        if chosen.preview_text is not None:
                            seq_preview_text = chosen.preview_text
                        log.info(
                            "A/B: slot=%s seq=%s chose variant_id=%s label=%r",
                            slot.id, sequence.id, chosen_variant_id, chosen.label,
                        )

                if (seq_subject or "").strip() == "":
                    # Fetch ALL prior emails in this lead+campaign thread for proper References chain
                    all_logs_result = await session.execute(
                        select(EmailLog).where(
                            EmailLog.lead_id == lead.id,
                            EmailLog.campaign_id == campaign.id,
                            EmailLog.message_id.isnot(None),
                            EmailLog.message_id != "",
                        ).order_by(EmailLog.sent_at.asc())
                    )
                    all_logs = all_logs_result.scalars().all()

                    if all_logs:
                        # In-Reply-To = most recent message (direct parent)
                        reply_to_msg_id = all_logs[-1].message_id
                        # References = ALL message IDs in order (space-separated)
                        references_chain = " ".join(
                            (mid if mid.startswith("<") else f"<{mid}>")
                            for log_entry in all_logs
                            if (mid := log_entry.message_id)
                        )
                        # Gmail threadId from most recent
                        prev_thread_id = all_logs[-1].thread_id
                        # Subject: Re: <original subject> from the FIRST email that had a real subject
                        first_with_subject = next(
                            (e for e in all_logs if e.subject and e.subject.strip() and e.subject != "(no subject)"),
                            None,
                        )
                        if first_with_subject:
                            orig_subj = first_with_subject.subject
                            if orig_subj.lower().startswith("re: "):
                                subject = orig_subj
                            else:
                                subject = f"Re: {orig_subj}"
                        else:
                            subject = "(no subject)"
                    else:
                        subject = "(no subject)"
                else:
                    subject = seq_subject.strip()

                # Apply lead-data variable substitution to the subject line
                # (same {{firstName}} / {{company}} etc. tokens as the body).
                subject = render_body(subject, get_lead_data(lead))

                # ── Determine HTML mode ──────────────────────────────────────
                # Override chain (highest → lowest priority):
                #   1. Settings-level text-only (send_all_as_text / send_first_as_text)
                #   2. Tracking requires HTML (tracking overrides text → html)
                #   3. Sequence-level is_html checkbox
                #   4. Legacy auto-detect from body content
                format_override = None  # tracks if/why format was overridden

                if getattr(campaign, 'send_all_as_text', False):
                    is_html = False
                    # Check if tracking would have wanted HTML
                    wants_tracking = (
                        getattr(campaign, 'track_opens', False)
                        or getattr(campaign, 'track_clicks', False)
                    )
                    if wants_tracking:
                        format_override = "text_forced_tracking_disabled"
                elif getattr(campaign, 'send_first_as_text', False) and slot.sequence_index == 0:
                    is_html = False
                    wants_tracking = (
                        getattr(campaign, 'track_opens', False)
                        or getattr(campaign, 'track_clicks', False)
                    )
                    if wants_tracking:
                        format_override = "first_text_tracking_disabled"
                else:
                    # Determine base format from sequence or auto-detect
                    if seq_is_html is not None:
                        is_html = bool(seq_is_html)
                    else:
                        is_html = bool(re.search(r'<[a-zA-Z][^>]*>', seq_body or ""))

                    # Tracking override: if tracking is on and sequence is plain text,
                    # upgrade to HTML so the pixel/links can be injected
                    if not is_html and (
                        getattr(campaign, 'track_opens', False)
                        or getattr(campaign, 'track_clicks', False)
                    ):
                        is_html = True
                        format_override = "tracking_upgraded_to_html"

                # ── Unsubscribe token (fetch or create) ──────────────────────
                unsub_token_res = await session.execute(
                    select(LeadUnsubscribeToken).where(
                        LeadUnsubscribeToken.lead_id == lead.id,
                        LeadUnsubscribeToken.campaign_id == campaign.id,
                    )
                )
                unsub_row = unsub_token_res.scalar_one_or_none()
                if unsub_row is None:
                    unsub_row = LeadUnsubscribeToken(
                        lead_id=lead.id,
                        campaign_id=campaign.id,
                        token=secrets.token_urlsafe(32),
                    )
                    session.add(unsub_row)
                    await session.flush()  # get the token persisted

                unsub_url = f"{inbox_tracking_base}/u/{unsub_row.token}"

                # Build lead data dict with built-in unsubscribe_link variable
                lead_data = get_lead_data(lead)
                lead_data["unsubscribe_link"] = unsub_url

                body = render_body(seq_body, lead_data)
                # Inject hidden preheader so email clients show the custom preview text.
                if is_html and seq_preview_text:
                    rendered_preview = render_body(seq_preview_text, lead_data)
                    preheader = (
                        '<div style="display:none !important; visibility:hidden; '
                        'font-size:1px; overflow:hidden; max-height:0; mso-hide:all;">'
                        f'{rendered_preview}</div>'
                    )
                    body = preheader + body
                from_addr = inbox.email
                from_name = inbox.display_name or ""

                # ── phase 1: pre-create EmailLog to get an ID for tracking ──
                email_log_entry = EmailLog(
                    lead_id=lead.id,
                    campaign_id=campaign.id,
                    inbox_id=inbox.id,
                    sequence_index=slot.sequence_index,
                    variant_id=chosen_variant_id,
                    subject=subject,
                    format_override=format_override,
                    message_id="",      # filled in after successful send
                    thread_id=prev_thread_id,
                )
                session.add(email_log_entry)
                await session.flush()  # acquire email_log_entry.id

                # ── phase 2: inject open/click tracking into HTML bodies ────
                send_body = body
                do_track = is_html and (
                    getattr(campaign, 'track_opens', False)
                    or getattr(campaign, 'track_clicks', False)
                )
                if do_track:
                    from app.tracking import inject_tracking_html
                    from app.models import TrackedLink as _TrackedLink
                    send_body, link_pairs = inject_tracking_html(
                        body,
                        email_log_entry.id,
                        inbox_tracking_base,
                        track_opens=getattr(campaign, 'track_opens', False),
                        track_clicks=getattr(campaign, 'track_clicks', False),
                        open_token=email_log_entry.open_token,
                    )
                    for _token, _url in link_pairs:
                        session.add(
                            _TrackedLink(
                                email_log_id=email_log_entry.id,
                                token=_token,
                                original_url=_url,
                            )
                        )

                # Unsubscribe header
                list_unsub_url = unsub_url if getattr(campaign, 'add_unsubscribe_header', True) else None

                # ── phase 3: send ────────────────────────────────────────────
                result = send_email(
                    to_email=lead.email,
                    subject=subject,
                    body=send_body,
                    from_email=from_addr,
                    from_name=from_name,
                    reply_to_msg_id=reply_to_msg_id,
                    references=references_chain,
                    is_html=is_html,
                    provider="gmail",
                    gmail_access_token=gmail_token,
                    gmail_account=ga,
                    thread_id=prev_thread_id,
                    list_unsubscribe_url=list_unsub_url,
                    google_client_id=g_client_id,
                    google_client_secret=g_client_secret,
                )

                # ── Handle permanent failure (bounce / auth) ─────────────────
                if isinstance(result, SendFailure):
                    log.warning(
                        "Permanent send failure for lead_id=%s inbox=%s: [%s] %s",
                        lead.id, inbox.email, result.error_type, result.message,
                    )
                    # Delete the pre-created log entry
                    await session.delete(email_log_entry)

                    # Mark lead as bounced and delete remaining queue slots
                    if result.error_type in ("bounce", "invalid_recipient"):
                        lead.status = "bounced"
                        # Delete ALL remaining queue slots for this lead+campaign
                        from sqlalchemy import delete as sql_delete
                        await session.execute(
                            sql_delete(QueueSlot).where(
                                QueueSlot.campaign_lead_id == cl.id,
                            )
                        )
                        await fire_webhook_event(session, "email.bounced", {
                            "lead_id": lead.id,
                            "lead_email": lead.email,
                            "campaign_id": campaign.id,
                            "inbox_id": inbox.id,
                            "error_type": result.error_type,
                            "error_message": result.message,
                            "timestamp": time_provider.utcnow().isoformat() + "Z",
                        })
                        await fire_webhook_event(session, "lead.status_changed", {
                            "lead_id": lead.id,
                            "lead_email": lead.email,
                            "old_status": "active",
                            "new_status": "bounced",
                            "reason": result.message,
                            "timestamp": time_provider.utcnow().isoformat() + "Z",
                        })
                    elif result.error_type in ("auth_failed", "permission_denied"):
                        await fire_webhook_event(session, "token_expired", {
                            "inbox_id": inbox.id,
                            "inbox_email": inbox.email,
                            "error": result.message,
                        })
                        # Stop processing this inbox — auth is broken
                        break
                    continue

                if not result:
                    # Transient failure — roll back the pre-created log; slot stays for retry
                    await session.delete(email_log_entry)
                    continue

                # ── success: update log and consume the slot ─────────────────
                email_log_entry.message_id = result.message_id
                email_log_entry.thread_id = result.thread_id or prev_thread_id
                await session.delete(slot)
                sent_this_inbox += 1
                total_sent += 1
                quota_remaining -= 1
                # update last_sent_time for rate-limit comparisons
                last_sent_time = now

                # Fire email.sent webhook
                await fire_webhook_event(session, "email.sent", {
                    "email_log_id": email_log_entry.id,
                    "lead_id": lead.id,
                    "lead_email": lead.email,
                    "campaign_id": campaign.id,
                    "inbox_id": inbox.id,
                    "inbox_email": inbox.email,
                    "subject": subject,
                    "sequence_index": slot.sequence_index,
                    "message_id": result.message_id,
                    "thread_id": result.thread_id,
                    "timestamp": time_provider.utcnow().isoformat() + "Z",
                })

                # ── Test mode: simulate random engagement events ─────────
                if settings.test_mode:
                    from app.models import EmailOpen, EmailClick, TrackedLink as _TL
                    fake_ip = f"10.0.{random.randint(0,255)}.{random.randint(1,254)}"

                    # ~40% chance of open
                    if random.random() < 0.4:
                        email_log_entry.opened = True
                        session.add(EmailOpen(
                            email_log_id=email_log_entry.id,
                            ip_address=fake_ip,
                        ))
                        await fire_webhook_event(session, "email.opened", {
                            "email_log_id": email_log_entry.id,
                            "lead_id": lead.id,
                            "campaign_id": campaign.id,
                            "ip_address": fake_ip,
                            "timestamp": time_provider.utcnow().isoformat() + "Z",
                        })
                        log.info("TEST MODE: simulated open for email_log_id=%s", email_log_entry.id)

                    # ~20% chance of click (only if opened)
                    if email_log_entry.opened and random.random() < 0.5:
                        email_log_entry.clicked = True
                        session.add(EmailClick(
                            email_log_id=email_log_entry.id,
                            ip_address=fake_ip,
                            clicked_at=time_provider.utcnow(),
                        ))
                        await fire_webhook_event(session, "email.clicked", {
                            "email_log_id": email_log_entry.id,
                            "lead_id": lead.id,
                            "campaign_id": campaign.id,
                            "original_url": "https://example.com/test-link",
                            "ip_address": fake_ip,
                            "timestamp": time_provider.utcnow().isoformat() + "Z",
                        })
                        log.info("TEST MODE: simulated click for email_log_id=%s", email_log_entry.id)

                    # ~15% chance of reply
                    if random.random() < 0.15:
                        fake_reply = LeadReply(
                            lead_id=lead.id,
                            campaign_id=campaign.id,
                            inbox_id=inbox.id,
                            thread_id=email_log_entry.thread_id or "fake-thread",
                            message_id=f"<fake-reply-{email_log_entry.id}@test>",
                            snippet="This is a simulated test reply.",
                            received_at=time_provider.utcnow(),
                        )
                        session.add(fake_reply)
                        await fire_webhook_event(session, "lead.replied", {
                            "lead_id": lead.id,
                            "lead_email": lead.email,
                            "lead_name": lead.name or "",
                            "thread_id": email_log_entry.thread_id,
                            "inbox_id": inbox.id,
                            "inbox_email": inbox.email,
                            "message_id": fake_reply.message_id,
                            "timestamp": time_provider.utcnow().isoformat() + "Z",
                        })
                        log.info("TEST MODE: simulated reply for email_log_id=%s lead_id=%s", email_log_entry.id, lead.id)

        await session.commit()

    last_send_job_run = time_provider.now()
    last_send_job_sent_count = total_sent
    log.info("Send job finished: %d email(s) sent (next run in %d min)", total_sent, settings.queue_check_interval_minutes)
