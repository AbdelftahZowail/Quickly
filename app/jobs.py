"""Background job: send due emails from the queue."""
# // BEACON SYNC: This file handles tracking logic that Beacon mirrors.
# // Any changes to tracking behavior, event types, metadata collected, or URL structure
# // must also be reflected in the Beacon service (/beacon or the separate Beacon repo).
# // If you are an AI agent, add a TODO at the end of your response listing what needs to be updated in Beacon.

import asyncio
import logging
import random
import re
import secrets
from datetime import datetime, date, time, timedelta
from email.utils import make_msgid
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
    Office365Account,
    LeadUnsubscribeToken,
    GmailMessage,
    Office365Message,
    CustomEmailOverride,
)
from app.sender import send_email, render_body, get_lead_data, SendResult, SendFailure, build_quote_html, build_quote_plain, _plain_to_quoted_html, _strip_html_tags
from app.webhooks import fire_webhook_event
from app.app_settings import get_google_oauth_credentials, get_office365_oauth_credentials
from app import time as time_provider
from app.queue_logic import _parse_time, compute_effective_daily_limit
from app.campaign_lead_status import campaign_lead_may_receive_sends

log = logging.getLogger(__name__)


async def _update_enrollment_after_send(session: AsyncSession, cl: CampaignLead, campaign: Campaign, sequence: Sequence) -> None:
    n_seq = (
        await session.execute(
            select(func.count(Sequence.id)).where(Sequence.campaign_id == campaign.id)
        )
    ).scalar() or 0
    if getattr(cl, "enrollment_status", None) == "active":
        cl.enrollment_status = "contacted"
    if n_seq > 0 and sequence.position >= n_seq - 1:
        cl.enrollment_status = "completed"

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
        # Pre-fetch Office 365 OAuth credentials once for all O365 inboxes
        o365_client_id, o365_client_secret, o365_tenant_id = await get_office365_oauth_credentials(session)

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

            # ── Fetch provider-specific credentials ──────────────────────
            gmail_token = ""
            ga = None
            o365_account = None
            simulate_send = False

            if inbox.provider == "office365":
                o365_res = await session.execute(
                    select(Office365Account).where(Office365Account.inbox_id == inbox.id)
                )
                o365_account = o365_res.scalar_one_or_none()
                if o365_account:
                    pass
                else:
                    log.warning("Office 365 inbox %s (%s) has no Office365Account — skipping", inbox.id, inbox.email)
                    continue
            else:
                # Default: Gmail
                ga_result = await session.execute(
                    select(GmailAccount).where(GmailAccount.inbox_id == inbox.id)
                )
                ga = ga_result.scalar_one_or_none()
                if ga:
                    gmail_token = ga.access_token
                else:
                    if settings.test_mode:
                        log.info(
                            "Test mode: Gmail inbox %s (%s) has no GmailAccount -- simulating send",
                            inbox.id,
                            inbox.email,
                        )
                        simulate_send = True
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

                if not campaign_lead_may_receive_sends(cl, lead):
                    await session.delete(slot)
                    continue
                if campaign.stop_on_reply:
                    reply_check = await session.execute(
                        select(LeadReply).where(
                            LeadReply.lead_id == lead.id,
                            LeadReply.campaign_id == campaign.id,
                        )
                    )
                    if reply_check.scalar_one_or_none():
                        await session.delete(slot)
                        continue

                reply_to_msg_id = None
                prev_thread_id = None
                references_chain = None
                reply_graph_message_id = None  # O365 Graph internal message ID for reply API
                prev_email_body_plain: str = ""
                prev_email_body_html: str = ""
                prev_sent_at = None

                # ── A/B variant selection ─────────────────────────────────────
                chosen_variant_id = None
                seq_subject      = sequence.subject
                seq_body         = sequence.body
                seq_is_html      = sequence.is_html
                seq_preview_text = getattr(sequence, 'preview_text', None)

                if getattr(sequence, "sequence_type", "standard") != "personalized":
                    enabled_variants = [
                        v for v in getattr(sequence, 'variants', []) if v.enabled
                    ]
                    if enabled_variants:
                        # Use the pre-assigned variant if one exists on the slot
                        chosen = None
                        if slot.variant_id is not None:
                            chosen = next(
                                (v for v in enabled_variants if v.id == slot.variant_id),
                                None,
                            )
                        # Fallback: random selection (legacy slots without pre-assignment)
                        if chosen is None:
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

                # ── Personalized sequence override ────────────────────────────
                if getattr(sequence, "sequence_type", "standard") == "personalized":
                    ov_res = await session.execute(
                        select(CustomEmailOverride).where(
                            CustomEmailOverride.campaign_lead_id == cl.id,
                            CustomEmailOverride.sequence_id == sequence.id,
                        )
                    )
                    override = ov_res.scalar_one_or_none()
                    if override:
                        if override.subject is not None:
                            seq_subject = override.subject
                        elif sequence.fallback_subject:
                            seq_subject = sequence.fallback_subject
                        if override.body is not None:
                            seq_body = override.body
                        elif sequence.fallback_body:
                            seq_body = sequence.fallback_body
                        if override.is_html is not None:
                            seq_is_html = override.is_html
                        log.info(
                            "Personalized override: slot=%s seq=%s lead=%s using custom content",
                            slot.id, sequence.id, lead.id,
                        )
                    elif sequence.fallback_subject or sequence.fallback_body:
                        # No override row — use fallback content as safety net
                        if sequence.fallback_subject:
                            seq_subject = sequence.fallback_subject
                        if sequence.fallback_body:
                            seq_body = sequence.fallback_body
                        log.info(
                            "Personalized fallback (no override): slot=%s seq=%s lead=%s",
                            slot.id, sequence.id, lead.id,
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
                        # Guard: reject a thread_id that belongs to a *different* campaign
                        # for this lead.  Gmail may subject-match the very first email of
                        # this campaign into an existing thread from a previous campaign with
                        # the same subject — we must not continue that foreign thread.
                        if prev_thread_id:
                            _cross_camp_row = await session.execute(
                                select(EmailLog.id).where(
                                    EmailLog.lead_id == lead.id,
                                    EmailLog.thread_id == prev_thread_id,
                                    EmailLog.campaign_id != campaign.id,
                                ).limit(1)
                            )
                            if _cross_camp_row.scalar_one_or_none():
                                log.warning(
                                    "Thread %s for lead_id=%s is shared with another "
                                    "campaign – clearing thread_id to prevent "
                                    "cross-campaign contamination",
                                    prev_thread_id, lead.id,
                                )
                                prev_thread_id = None
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

                        # Fetch the previous email's body for quoting from the
                        # sequence/variant definition.  This is always available
                        # (no dependency on Gmail/O365 unibox sync) and is
                        # campaign-scoped by construction, so quoting can never
                        # accidentally include content from a different campaign's thread.
                        prev_sent_at = all_logs[-1].sent_at
                        _prev_log = all_logs[-1]
                        _prev_seq_row = await session.execute(
                            select(Sequence).where(
                                Sequence.campaign_id == campaign.id,
                                Sequence.position == _prev_log.sequence_index,
                            )
                        )
                        _prev_seq = _prev_seq_row.scalar_one_or_none()
                        if _prev_seq:
                            _prev_body_raw = _prev_seq.body or ""
                            _prev_is_html_raw = _prev_seq.is_html
                            # Override with the variant content if a variant was chosen
                            # for the previous send (same logic used at send time).
                            if _prev_log.variant_id:
                                _prev_var_row = await session.execute(
                                    select(SequenceVariant).where(
                                        SequenceVariant.id == _prev_log.variant_id
                                    )
                                )
                                _prev_var = _prev_var_row.scalar_one_or_none()
                                if _prev_var and _prev_var.body:
                                    _prev_body_raw = _prev_var.body
                                    if _prev_var.is_html is not None:
                                        _prev_is_html_raw = _prev_var.is_html
                            _rendered_prev = render_body(_prev_body_raw, get_lead_data(lead))
                            if _prev_is_html_raw:
                                prev_email_body_html = _rendered_prev
                            else:
                                prev_email_body_plain = _rendered_prev

                        # For O365 inboxes, look up the Graph internal message ID of
                        # the message we are replying to so we can use the Graph
                        # Reply API and preserve the correct conversationIndex.
                        # Primary: match by internet_message_id (RFC 2822 Message-ID)
                        # so the lookup is campaign-scoped regardless of which
                        # conversation_id Gmail may have assigned.
                        if inbox.provider == "office365" and reply_to_msg_id:
                            _needle_msgid = (
                                reply_to_msg_id
                                if reply_to_msg_id.startswith("<")
                                else f"<{reply_to_msg_id}>"
                            )
                            o365_real_row = await session.execute(
                                select(Office365Message)
                                .where(
                                    Office365Message.inbox_id == inbox.id,
                                    Office365Message.internet_message_id == _needle_msgid,
                                    ~Office365Message.message_id.startswith("local-"),
                                )
                                .limit(1)
                            )
                            o365_real_msg = o365_real_row.scalar_one_or_none()
                            # Fallback: latest real Graph message in the conversation.
                            if not o365_real_msg and prev_thread_id:
                                o365_real_row = await session.execute(
                                    select(Office365Message)
                                    .where(
                                        Office365Message.inbox_id == inbox.id,
                                        Office365Message.conversation_id == prev_thread_id,
                                        ~Office365Message.message_id.startswith("local-"),
                                    )
                                    .order_by(Office365Message.received_at.desc())
                                    .limit(1)
                                )
                                o365_real_msg = o365_real_row.scalar_one_or_none()
                            if o365_real_msg:
                                reply_graph_message_id = o365_real_msg.message_id
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
                link_pairs: list = []
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

                if getattr(inbox, "beacon_connected", False):
                    from app.beacon_client import register_beacon_mappings

                    iid = inbox.id
                    b_items: list[dict] = [
                        {"kind": "unsubscribe", "token": unsub_row.token, "inbox_id": iid}
                    ]
                    if do_track:
                        b_items.insert(0, {"kind": "open", "token": email_log_entry.open_token, "inbox_id": iid})
                        for _token, _url in link_pairs:
                            b_items.append(
                                {"kind": "click", "token": _token, "original_url": _url, "inbox_id": iid}
                            )
                    try:
                        await register_beacon_mappings(inbox, b_items)
                    except Exception:
                        log.exception(
                            "Beacon register failed for email_log_id=%s; aborting send",
                            email_log_entry.id,
                        )
                        await session.delete(email_log_entry)
                        continue

                # ── Append quoted previous email (follow-up sequences only) ──
                if prev_sent_at and (prev_email_body_html or prev_email_body_plain):
                    _from_name = inbox.display_name or inbox.email
                    _from_email = inbox.email
                    if is_html:
                        _prev_html = prev_email_body_html or _plain_to_quoted_html(prev_email_body_plain)
                        send_body = send_body + build_quote_html(
                            _prev_html, _from_name, _from_email, prev_sent_at
                        )
                    else:
                        _prev_plain = prev_email_body_plain or _strip_html_tags(prev_email_body_html)
                        if _prev_plain:
                            send_body = send_body + build_quote_plain(
                                _prev_plain, _from_name, _from_email, prev_sent_at
                            )

                # Unsubscribe header
                list_unsub_url = unsub_url if getattr(campaign, 'add_unsubscribe_header', True) else None

                # ── phase 3: send ────────────────────────────────────────────
                if simulate_send:
                    fake_thread_id = prev_thread_id or f"test-thread-{email_log_entry.id}"
                    result = SendResult(
                        message_id=make_msgid(domain="test.local"),
                        thread_id=fake_thread_id,
                        gmail_message_id=f"test-gmail-{email_log_entry.id}",
                    )
                else:
                    result = send_email(
                        to_email=lead.email,
                        subject=subject,
                        body=send_body,
                        from_email=from_addr,
                        from_name=from_name,
                        reply_to_msg_id=reply_to_msg_id,
                        references=references_chain,
                        is_html=is_html,
                        provider=inbox.provider or "gmail",
                        gmail_access_token=gmail_token,
                        gmail_account=ga,
                        thread_id=prev_thread_id,
                        list_unsubscribe_url=list_unsub_url,
                        google_client_id=g_client_id,
                        google_client_secret=g_client_secret,
                        office365_account=o365_account,
                        office365_client_id=o365_client_id,
                        office365_client_secret=o365_client_secret,
                        office365_tenant_id=o365_tenant_id,
                        conversation_id=prev_thread_id if inbox.provider == "office365" else None,
                        reply_graph_message_id=reply_graph_message_id if inbox.provider == "office365" else None,
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
                        prev_enr = getattr(cl, "enrollment_status", None) or "active"
                        cl.enrollment_status = "bounced"
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
                            "campaign_id": campaign.id,
                            "old_enrollment_status": prev_enr,
                            "new_enrollment_status": "bounced",
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
                await _update_enrollment_after_send(session, cl, campaign, sequence)
                sent_this_inbox += 1
                total_sent += 1
                quota_remaining -= 1
                # update last_sent_time for rate-limit comparisons
                last_sent_time = now

                # For Gmail: save the sent message to the local mirror so the body
                # is available for quoting in future follow-up emails.
                if inbox.provider != "office365" and email_log_entry.thread_id:
                    try:
                        from app.unibox import upsert_sent_message as _upsert_sent_gmail
                        await _upsert_sent_gmail(
                            session,
                            inbox_id=inbox.id,
                            thread_id=email_log_entry.thread_id,
                            gmail_message_id=result.gmail_message_id,
                            rfc_message_id=result.message_id,
                            subject=subject,
                            to_email=lead.email,
                            from_email=inbox.email,
                            body=send_body,
                            is_html=is_html,
                        )
                    except Exception:
                        log.exception(
                            "Failed to upsert sent Gmail message to unibox "
                            "inbox_id=%s lead=%s",
                            inbox.id, lead.email,
                        )

                # For Office 365: immediately save the sent message to the local
                # mirror so it appears in the unibox thread before the next sync.
                if inbox.provider == "office365" and email_log_entry.thread_id:
                    try:
                        from app.unibox import upsert_sent_o365_message
                        await upsert_sent_o365_message(
                            session,
                            inbox_id=inbox.id,
                            conversation_id=email_log_entry.thread_id,
                            internet_message_id=result.message_id,
                            subject=subject,
                            to_email=lead.email,
                            from_email=inbox.email,
                            body=send_body,
                            is_html=is_html,
                        )
                    except Exception:
                        log.exception(
                            "Failed to upsert sent O365 message to unibox "
                            "inbox_id=%s lead=%s",
                            inbox.id, lead.email,
                        )

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
                            lead_id=lead.id, campaign_id=campaign.id,
                            replied_at=time_provider.utcnow(),
                        )
                        session.add(fake_reply)
                        await session.flush()
                        await fire_webhook_event(session, "lead.replied", {
                            "lead_id": lead.id,
                            "lead_email": lead.email,
                            "lead_name": lead.name or "",
                            "thread_id": email_log_entry.thread_id,
                            "inbox_id": inbox.id,
                            "inbox_email": inbox.email,
                            "reply_id": fake_reply.id,
                            "timestamp": time_provider.utcnow().isoformat() + "Z",
                        })
                        log.info("TEST MODE: simulated reply for email_log_id=%s lead_id=%s", email_log_entry.id, lead.id)

        await session.commit()

    last_send_job_run = time_provider.now()
    last_send_job_sent_count = total_sent
    log.info("Send job finished: %d email(s) sent (next run in %d min)", total_sent, settings.queue_check_interval_minutes)


# ---------------------------------------------------------------------------
# Per-slot job: fired by APScheduler at the slot's exact scheduled_date
# ---------------------------------------------------------------------------

async def send_slot_job(slot_id: int) -> None:
    """Send a single queue slot identified by *slot_id*.

    Called by APScheduler as a DateTrigger job at the slot's ``scheduled_date``.
    All business-rule checks (quota, rate limit, sending window, etc.) are
    applied here; a slot that cannot be sent at fire time is simply skipped –
    the slot row remains in the database so the next recalculation can
    reschedule it.
    """
    global last_send_job_run, last_send_job_sent_count

    now = time_provider.now()
    log.info("send_slot_job: slot_id=%d firing at %s", slot_id, now.isoformat())

    async with AsyncSessionLocal() as session:
        # ── Load slot + all related entities in one query ────────────────
        slot_res = await session.execute(
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
            .options(selectinload(Sequence.variants))
            .where(QueueSlot.id == slot_id)
        )
        row = slot_res.first()
        if not row:
            log.info("send_slot_job: slot %d not found – already sent or cancelled", slot_id)
            return

        slot, cl, campaign, lead, sequence, inbox = row

        # ── Pre-flight checks ────────────────────────────────────────────
        if inbox.paused:
            log.info("send_slot_job: inbox %s paused, skipping slot %d", inbox.email, slot_id)
            return
        if getattr(campaign, "paused", False):
            log.info("send_slot_job: campaign %d paused, skipping slot %d", campaign.id, slot_id)
            return
        if not campaign_lead_may_receive_sends(cl, lead):
            log.info(
                "send_slot_job: lead %d not sendable for campaign_lead %d, dropping slot %d",
                lead.id, cl.id, slot_id,
            )
            await session.delete(slot)
            await session.commit()
            return
        if not _in_sending_window(now, campaign):
            log.info("send_slot_job: outside sending window for campaign %d, skipping slot %d",
                     campaign.id, slot_id)
            return

        # ── Daily quota check ────────────────────────────────────────────
        today = now.date()
        max_per_day = compute_effective_daily_limit(inbox)
        sent_count_res = await session.execute(
            select(func.count(EmailLog.id))
            .where(EmailLog.inbox_id == inbox.id, func.date(EmailLog.sent_at) == today)
        )
        already_sent = sent_count_res.scalar() or 0
        if already_sent >= max_per_day:
            log.warning(
                "send_slot_job: daily limit hit for inbox %s (sent=%d cap=%d), skipping slot %d",
                inbox.email, already_sent, max_per_day, slot_id,
            )
            await fire_webhook_event(
                session, "daily_limit",
                {"inbox_id": inbox.id, "inbox_email": inbox.email, "date": str(today)},
            )
            await session.commit()
            return

        # ── Rate-limit check ─────────────────────────────────────────────
        last_sent_res = await session.execute(
            select(EmailLog.sent_at)
            .where(EmailLog.inbox_id == inbox.id)
            .order_by(EmailLog.sent_at.desc())
            .limit(1)
        )
        last_sent_time = last_sent_res.scalar_one_or_none()
        if last_sent_time is not None:
            delta = now - last_sent_time
            required = timedelta(minutes=inbox.wait_minutes_between)
            if delta + timedelta(seconds=20) < required:
                log.info(
                    "send_slot_job: rate limit for inbox %s – "
                    "last sent %s ago (need %s), skipping slot %d",
                    inbox.email, delta, required, slot_id,
                )
                await fire_webhook_event(
                    session, "rate_limit",
                    {
                        "inbox_id": inbox.id, "inbox_email": inbox.email,
                        "last_sent": last_sent_time.isoformat(),
                        "now": now.isoformat(),
                        "wait_minutes": inbox.wait_minutes_between,
                    },
                )
                await session.commit()
                return

        # ── stop_on_reply check ──────────────────────────────────────────
        if campaign.stop_on_reply:
            reply_check = await session.execute(
                select(LeadReply).where(
                    LeadReply.lead_id == lead.id, LeadReply.campaign_id == campaign.id
                )
            )
            if reply_check.scalar_one_or_none():
                log.info(
                    "send_slot_job: lead %d already replied to campaign %d, dropping slot %d",
                    lead.id, campaign.id, slot_id,
                )
                await session.delete(slot)
                await session.commit()
                return

        # ── OAuth credentials ────────────────────────────────────────────
        g_client_id, g_client_secret = await get_google_oauth_credentials(session)
        o365_client_id, o365_client_secret, o365_tenant_id = await get_office365_oauth_credentials(session)

        gmail_token = ""
        ga = None
        o365_account = None
        simulate_send = False

        from app.settings_manager import settings as _settings
        from app.app_settings import get_inbox_tracking_base
        _fallback_tracking_base = _settings.base_url.rstrip("/")
        inbox_tracking_base = get_inbox_tracking_base(inbox, _fallback_tracking_base)

        if inbox.provider == "office365":
            o365_res = await session.execute(
                select(Office365Account).where(Office365Account.inbox_id == inbox.id)
            )
            o365_account = o365_res.scalar_one_or_none()
            if o365_account:
                pass
            else:
                log.warning(
                    "send_slot_job: O365 inbox %s has no Office365Account – skipping slot %d",
                    inbox.email, slot_id,
                )
                return
        else:
            ga_result = await session.execute(
                select(GmailAccount).where(GmailAccount.inbox_id == inbox.id)
            )
            ga = ga_result.scalar_one_or_none()
            if ga:
                gmail_token = ga.access_token
            else:
                if settings.test_mode:
                    log.info(
                        "send_slot_job: test mode Gmail inbox %s has no GmailAccount -- simulating send",
                        inbox.email,
                    )
                    simulate_send = True
                else:
                    log.warning(
                        "send_slot_job: Gmail inbox %s has no GmailAccount – skipping slot %d",
                        inbox.email, slot_id,
                    )
                    return

        # ── A/B variant selection ────────────────────────────────────────
        chosen_variant_id = None
        seq_subject      = sequence.subject
        seq_body         = sequence.body
        seq_is_html      = sequence.is_html
        seq_preview_text = getattr(sequence, "preview_text", None)

        if getattr(sequence, "sequence_type", "standard") != "personalized":
            enabled_variants = [v for v in getattr(sequence, "variants", []) if v.enabled]
            if enabled_variants:
                # Use the pre-assigned variant if one exists on the slot
                chosen = None
                if slot.variant_id is not None:
                    chosen = next(
                        (v for v in enabled_variants if v.id == slot.variant_id),
                        None,
                    )
                # Fallback: random selection (legacy slots without pre-assignment)
                if chosen is None:
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
                        "send_slot_job: A/B: slot=%s seq=%s chose variant_id=%s label=%r",
                        slot.id, sequence.id, chosen_variant_id, chosen.label,
                    )

        # ── Personalized sequence override ───────────────────────────────
        if getattr(sequence, "sequence_type", "standard") == "personalized":
            ov_res = await session.execute(
                select(CustomEmailOverride).where(
                    CustomEmailOverride.campaign_lead_id == cl.id,
                    CustomEmailOverride.sequence_id == sequence.id,
                )
            )
            override = ov_res.scalar_one_or_none()
            if override:
                if override.subject is not None:
                    seq_subject = override.subject
                elif sequence.fallback_subject:
                    seq_subject = sequence.fallback_subject
                if override.body is not None:
                    seq_body = override.body
                elif sequence.fallback_body:
                    seq_body = sequence.fallback_body
                if override.is_html is not None:
                    seq_is_html = override.is_html
                log.info(
                    "send_slot_job: Personalized override: slot=%s seq=%s lead=%s",
                    slot.id, sequence.id, lead.id,
                )
            elif sequence.fallback_subject or sequence.fallback_body:
                if sequence.fallback_subject:
                    seq_subject = sequence.fallback_subject
                if sequence.fallback_body:
                    seq_body = sequence.fallback_body
                log.info(
                    "send_slot_job: Personalized fallback (no override): slot=%s seq=%s lead=%s",
                    slot.id, sequence.id, lead.id,
                )

        # ── Thread / reply chain for follow-up sequences ─────────────────
        reply_to_msg_id = None
        prev_thread_id = None
        references_chain = None
        reply_graph_message_id = None
        prev_email_body_plain: str = ""
        prev_email_body_html: str = ""
        prev_sent_at = None

        if (seq_subject or "").strip() == "":
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
                reply_to_msg_id = all_logs[-1].message_id
                references_chain = " ".join(
                    (mid if mid.startswith("<") else f"<{mid}>")
                    for log_entry in all_logs
                    if (mid := log_entry.message_id)
                )
                prev_thread_id = all_logs[-1].thread_id
                if prev_thread_id:
                    _cross_camp_row = await session.execute(
                        select(EmailLog.id).where(
                            EmailLog.lead_id == lead.id,
                            EmailLog.thread_id == prev_thread_id,
                            EmailLog.campaign_id != campaign.id,
                        ).limit(1)
                    )
                    if _cross_camp_row.scalar_one_or_none():
                        log.warning(
                            "send_slot_job: thread %s for lead_id=%s shared with another "
                            "campaign – clearing thread_id",
                            prev_thread_id, lead.id,
                        )
                        prev_thread_id = None

                first_with_subject = next(
                    (e for e in all_logs if e.subject and e.subject.strip()
                     and e.subject != "(no subject)"),
                    None,
                )
                if first_with_subject:
                    orig_subj = first_with_subject.subject
                    subject = orig_subj if orig_subj.lower().startswith("re: ") else f"Re: {orig_subj}"
                else:
                    subject = "(no subject)"

                prev_sent_at = all_logs[-1].sent_at
                _prev_log = all_logs[-1]
                _prev_seq_row = await session.execute(
                    select(Sequence).where(
                        Sequence.campaign_id == campaign.id,
                        Sequence.position == _prev_log.sequence_index,
                    )
                )
                _prev_seq = _prev_seq_row.scalar_one_or_none()
                if _prev_seq:
                    _prev_body_raw = _prev_seq.body or ""
                    _prev_is_html_raw = _prev_seq.is_html
                    if _prev_log.variant_id:
                        _prev_var_row = await session.execute(
                            select(SequenceVariant).where(SequenceVariant.id == _prev_log.variant_id)
                        )
                        _prev_var = _prev_var_row.scalar_one_or_none()
                        if _prev_var and _prev_var.body:
                            _prev_body_raw = _prev_var.body
                            if _prev_var.is_html is not None:
                                _prev_is_html_raw = _prev_var.is_html
                    _rendered_prev = render_body(_prev_body_raw, get_lead_data(lead))
                    if _prev_is_html_raw:
                        prev_email_body_html = _rendered_prev
                    else:
                        prev_email_body_plain = _rendered_prev

                if inbox.provider == "office365" and reply_to_msg_id:
                    _needle_msgid = (
                        reply_to_msg_id
                        if reply_to_msg_id.startswith("<")
                        else f"<{reply_to_msg_id}>"
                    )
                    o365_real_row = await session.execute(
                        select(Office365Message)
                        .where(
                            Office365Message.inbox_id == inbox.id,
                            Office365Message.internet_message_id == _needle_msgid,
                            ~Office365Message.message_id.startswith("local-"),
                        )
                        .limit(1)
                    )
                    o365_real_msg = o365_real_row.scalar_one_or_none()
                    if not o365_real_msg and prev_thread_id:
                        o365_real_row = await session.execute(
                            select(Office365Message)
                            .where(
                                Office365Message.inbox_id == inbox.id,
                                Office365Message.conversation_id == prev_thread_id,
                                ~Office365Message.message_id.startswith("local-"),
                            )
                            .order_by(Office365Message.received_at.desc())
                            .limit(1)
                        )
                        o365_real_msg = o365_real_row.scalar_one_or_none()
                    if o365_real_msg:
                        reply_graph_message_id = o365_real_msg.message_id
            else:
                subject = "(no subject)"
        else:
            subject = seq_subject.strip()

        subject = render_body(subject, get_lead_data(lead))

        # ── HTML / plain-text decision ────────────────────────────────────
        format_override = None
        if getattr(campaign, "send_all_as_text", False):
            is_html = False
            if getattr(campaign, "track_opens", False) or getattr(campaign, "track_clicks", False):
                format_override = "text_forced_tracking_disabled"
        elif getattr(campaign, "send_first_as_text", False) and slot.sequence_index == 0:
            is_html = False
            if getattr(campaign, "track_opens", False) or getattr(campaign, "track_clicks", False):
                format_override = "first_text_tracking_disabled"
        else:
            if seq_is_html is not None:
                is_html = bool(seq_is_html)
            else:
                is_html = bool(re.search(r"<[a-zA-Z][^>]*>", seq_body or ""))
            if not is_html and (
                getattr(campaign, "track_opens", False) or getattr(campaign, "track_clicks", False)
            ):
                is_html = True
                format_override = "tracking_upgraded_to_html"

        # ── Unsubscribe token ─────────────────────────────────────────────
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
            await session.flush()

        unsub_url = f"{inbox_tracking_base}/u/{unsub_row.token}"
        lead_data = get_lead_data(lead)
        lead_data["unsubscribe_link"] = unsub_url

        body = render_body(seq_body, lead_data)
        if is_html and seq_preview_text:
            rendered_preview = render_body(seq_preview_text, lead_data)
            preheader = (
                '<div style="display:none !important; visibility:hidden; '
                'font-size:1px; overflow:hidden; max-height:0; mso-hide:all;">'
                f"{rendered_preview}</div>"
            )
            body = preheader + body

        from_addr = inbox.email
        from_name = inbox.display_name or ""

        # ── Pre-create EmailLog to get an ID for tracking ─────────────────
        email_log_entry = EmailLog(
            lead_id=lead.id,
            campaign_id=campaign.id,
            inbox_id=inbox.id,
            sequence_index=slot.sequence_index,
            variant_id=chosen_variant_id,
            subject=subject,
            format_override=format_override,
            message_id="",
            thread_id=prev_thread_id,
        )
        session.add(email_log_entry)
        await session.flush()

        # ── Inject open/click tracking ────────────────────────────────────
        send_body = body
        link_pairs: list = []
        do_track = is_html and (
            getattr(campaign, "track_opens", False) or getattr(campaign, "track_clicks", False)
        )
        if do_track:
            from app.tracking import inject_tracking_html
            from app.models import TrackedLink as _TrackedLink
            send_body, link_pairs = inject_tracking_html(
                body,
                email_log_entry.id,
                inbox_tracking_base,
                track_opens=getattr(campaign, "track_opens", False),
                track_clicks=getattr(campaign, "track_clicks", False),
                open_token=email_log_entry.open_token,
            )
            for _token, _url in link_pairs:
                session.add(_TrackedLink(email_log_id=email_log_entry.id, token=_token, original_url=_url))

        if getattr(inbox, "beacon_connected", False):
            from app.beacon_client import register_beacon_mappings

            iid = inbox.id
            b_items: list[dict] = [
                {"kind": "unsubscribe", "token": unsub_row.token, "inbox_id": iid}
            ]
            if do_track:
                b_items.insert(0, {"kind": "open", "token": email_log_entry.open_token, "inbox_id": iid})
                for _token, _url in link_pairs:
                    b_items.append(
                        {"kind": "click", "token": _token, "original_url": _url, "inbox_id": iid}
                    )
            try:
                await register_beacon_mappings(inbox, b_items)
            except Exception:
                log.exception(
                    "Beacon register failed for email_log_id=%s; aborting send",
                    email_log_entry.id,
                )
                await session.delete(email_log_entry)
                return

        # ── Append quoted previous email ──────────────────────────────────
        if prev_sent_at and (prev_email_body_html or prev_email_body_plain):
            _from_name = inbox.display_name or inbox.email
            _from_email = inbox.email
            if is_html:
                _prev_html = prev_email_body_html or _plain_to_quoted_html(prev_email_body_plain)
                send_body = send_body + build_quote_html(_prev_html, _from_name, _from_email, prev_sent_at)
            else:
                _prev_plain = prev_email_body_plain or _strip_html_tags(prev_email_body_html)
                if _prev_plain:
                    send_body = send_body + build_quote_plain(_prev_plain, _from_name, _from_email, prev_sent_at)

        list_unsub_url = unsub_url if getattr(campaign, "add_unsubscribe_header", True) else None

        # ── Send ──────────────────────────────────────────────────────────
        if simulate_send:
            fake_thread_id = prev_thread_id or f"test-thread-{email_log_entry.id}"
            result = SendResult(
                message_id=make_msgid(domain="test.local"),
                thread_id=fake_thread_id,
                gmail_message_id=f"test-gmail-{email_log_entry.id}",
            )
        else:
            result = send_email(
                to_email=lead.email,
                subject=subject,
                body=send_body,
                from_email=from_addr,
                from_name=from_name,
                reply_to_msg_id=reply_to_msg_id,
                references=references_chain,
                is_html=is_html,
                provider=inbox.provider or "gmail",
                gmail_access_token=gmail_token,
                gmail_account=ga,
                thread_id=prev_thread_id,
                list_unsubscribe_url=list_unsub_url,
                google_client_id=g_client_id,
                google_client_secret=g_client_secret,
                office365_account=o365_account,
                office365_client_id=o365_client_id,
                office365_client_secret=o365_client_secret,
                office365_tenant_id=o365_tenant_id,
                conversation_id=prev_thread_id if inbox.provider == "office365" else None,
                reply_graph_message_id=reply_graph_message_id if inbox.provider == "office365" else None,
            )

        # ── Permanent failure ─────────────────────────────────────────────
        if isinstance(result, SendFailure):
            log.warning(
                "send_slot_job: permanent failure for lead_id=%s inbox=%s: [%s] %s",
                lead.id, inbox.email, result.error_type, result.message,
            )
            await session.delete(email_log_entry)
            if result.error_type in ("bounce", "invalid_recipient"):
                prev_enr = getattr(cl, "enrollment_status", None) or "active"
                cl.enrollment_status = "bounced"
                from sqlalchemy import delete as _sql_delete
                await session.execute(
                    _sql_delete(QueueSlot).where(QueueSlot.campaign_lead_id == cl.id)
                )
                await fire_webhook_event(session, "email.bounced", {
                    "lead_id": lead.id, "lead_email": lead.email,
                    "campaign_id": campaign.id, "inbox_id": inbox.id,
                    "error_type": result.error_type, "error_message": result.message,
                    "timestamp": time_provider.utcnow().isoformat() + "Z",
                })
                await fire_webhook_event(session, "lead.status_changed", {
                    "lead_id": lead.id, "lead_email": lead.email,
                    "campaign_id": campaign.id,
                    "old_enrollment_status": prev_enr, "new_enrollment_status": "bounced",
                    "reason": result.message,
                    "timestamp": time_provider.utcnow().isoformat() + "Z",
                })
            elif result.error_type in ("auth_failed", "permission_denied"):
                await fire_webhook_event(session, "token_expired", {
                    "inbox_id": inbox.id, "inbox_email": inbox.email, "error": result.message,
                })
            await session.commit()
            return

        if not result:
            # Transient failure – roll back the pre-created log; slot stays for retry
            await session.delete(email_log_entry)
            await session.commit()
            log.warning("send_slot_job: transient failure for slot %d, slot retained for retry", slot_id)
            return

        # ── Success ───────────────────────────────────────────────────────
        email_log_entry.message_id = result.message_id
        email_log_entry.thread_id = result.thread_id or prev_thread_id
        await session.delete(slot)
        await _update_enrollment_after_send(session, cl, campaign, sequence)

        if inbox.provider != "office365" and email_log_entry.thread_id:
            try:
                from app.unibox import upsert_sent_message as _upsert_sent_gmail
                await _upsert_sent_gmail(
                    session,
                    inbox_id=inbox.id,
                    thread_id=email_log_entry.thread_id,
                    gmail_message_id=result.gmail_message_id,
                    rfc_message_id=result.message_id,
                    subject=subject,
                    to_email=lead.email,
                    from_email=inbox.email,
                    body=send_body,
                    is_html=is_html,
                )
            except Exception:
                log.exception(
                    "send_slot_job: failed to upsert sent Gmail message "
                    "inbox_id=%s lead=%s", inbox.id, lead.email,
                )

        if inbox.provider == "office365" and email_log_entry.thread_id:
            try:
                from app.unibox import upsert_sent_o365_message
                await upsert_sent_o365_message(
                    session,
                    inbox_id=inbox.id,
                    conversation_id=email_log_entry.thread_id,
                    internet_message_id=result.message_id,
                    subject=subject,
                    to_email=lead.email,
                    from_email=inbox.email,
                    body=send_body,
                    is_html=is_html,
                )
            except Exception:
                log.exception(
                    "send_slot_job: failed to upsert sent O365 message "
                    "inbox_id=%s lead=%s", inbox.id, lead.email,
                )

        await fire_webhook_event(session, "email.sent", {
            "email_log_id": email_log_entry.id,
            "lead_id": lead.id, "lead_email": lead.email,
            "campaign_id": campaign.id, "inbox_id": inbox.id, "inbox_email": inbox.email,
            "subject": subject, "sequence_index": slot.sequence_index,
            "message_id": result.message_id, "thread_id": result.thread_id,
            "timestamp": time_provider.utcnow().isoformat() + "Z",
        })

        # ── Test mode: simulate engagement events ─────────────────────────
        if settings.test_mode:
            from app.models import EmailOpen, EmailClick, TrackedLink as _TL
            fake_ip = f"10.0.{random.randint(0, 255)}.{random.randint(1, 254)}"
            if random.random() < 0.4:
                email_log_entry.opened = True
                session.add(EmailOpen(email_log_id=email_log_entry.id, ip_address=fake_ip))
                await fire_webhook_event(session, "email.opened", {
                    "email_log_id": email_log_entry.id,
                    "lead_id": lead.id, "campaign_id": campaign.id,
                    "ip_address": fake_ip, "timestamp": time_provider.utcnow().isoformat() + "Z",
                })
                if random.random() < 0.5:
                    email_log_entry.clicked = True
                    session.add(EmailClick(
                        email_log_id=email_log_entry.id, ip_address=fake_ip,
                        clicked_at=time_provider.utcnow(),
                    ))
                    await fire_webhook_event(session, "email.clicked", {
                        "email_log_id": email_log_entry.id,
                        "lead_id": lead.id, "campaign_id": campaign.id,
                        "original_url": "https://example.com/test-link",
                        "ip_address": fake_ip, "timestamp": time_provider.utcnow().isoformat() + "Z",
                    })
            if random.random() < 0.15:
                fake_reply = LeadReply(
                    lead_id=lead.id, campaign_id=campaign.id,
                    replied_at=time_provider.utcnow(),
                )
                session.add(fake_reply)
                await session.flush()  # get the assigned id
                await fire_webhook_event(session, "lead.replied", {
                    "lead_id": lead.id, "lead_email": lead.email, "lead_name": lead.name or "",
                    "thread_id": email_log_entry.thread_id or "fake-thread",
                    "inbox_id": inbox.id, "inbox_email": inbox.email,
                    "reply_id": fake_reply.id,
                    "timestamp": time_provider.utcnow().isoformat() + "Z",
                })

        await session.commit()

    last_send_job_run = time_provider.now()
    last_send_job_sent_count += 1
    log.info("send_slot_job: slot %d sent successfully", slot_id)


# ---------------------------------------------------------------------------
# Periodic slot-scan worker – 1 APScheduler job, exact-second asyncio Tasks
# ---------------------------------------------------------------------------

# Tracks slot IDs that already have a live asyncio Task so that two
# consecutive scans overlapping at the 60-second boundary don't double-send.
_pending_slot_ids: set[int] = set()


async def _dispatch_slot(slot_id: int, delay: float) -> None:
    """Sleep for *delay* seconds then call send_slot_job.  Removes from pending set."""
    try:
        if delay > 0:
            await asyncio.sleep(delay)
        await send_slot_job(slot_id)
    finally:
        _pending_slot_ids.discard(slot_id)


async def run_slot_scan_job() -> None:
    """1-minute APScheduler job with exact-second per-slot asyncio Tasks.

    Every minute, queries slots due within the next 60 seconds and spawns a
    lightweight ``asyncio.Task`` per slot.  Each task sleeps until the slot's
    exact ``scheduled_date`` then calls ``send_slot_job``.

    ``_pending_slot_ids`` prevents double-dispatch when two scan ticks see the
    same slot inside their overlapping 60-second windows.
    """
    now = time_provider.now()
    window_end = now + timedelta(seconds=60)

    async with AsyncSessionLocal() as session:
        due_res = await session.execute(
            select(QueueSlot.id, QueueSlot.scheduled_date)
            .where(QueueSlot.scheduled_date <= window_end)
            .order_by(QueueSlot.scheduled_date, QueueSlot.position_in_day)
        )
        rows = due_res.all()

    if not rows:
        return

    dispatched = 0
    for slot_id, scheduled_date in rows:
        if slot_id in _pending_slot_ids:
            continue
        delay = (scheduled_date - now).total_seconds()
        _pending_slot_ids.add(slot_id)
        asyncio.create_task(_dispatch_slot(slot_id, delay))
        dispatched += 1

    if dispatched:
        log.info(
            "run_slot_scan_job: dispatched %d task(s) (window %s → +60 s)",
            dispatched, now.isoformat(),
        )
