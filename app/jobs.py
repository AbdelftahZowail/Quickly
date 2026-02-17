"""Background job: send due emails from the queue."""
import logging
from datetime import datetime, date, time
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.settings_manager import settings
from app.database import AsyncSessionLocal
from app.models import (
    QueueSlot,
    CampaignLead,
    Campaign,
    Sequence,
    Lead,
    Inbox,
    EmailLog,
    LeadReply,
    GmailAccount,
)
from app.sender import send_email, render_body, get_lead_data, SendResult
from app.routers.gmail_oauth import refresh_access_token
from app.app_settings import get_google_oauth_credentials

log = logging.getLogger(__name__)

# Updated each time run_send_job finishes (for /api/status)
last_send_job_run: datetime | None = None
last_send_job_sent_count: int = 0


def _parse_time(s: str) -> time:
    try:
        parts = s.strip().split(":")
        if len(parts) != 2:
            return time(9, 0)
        h = int(parts[0])
        m = int(parts[1])
        if h == 24 and m == 0:
            return time(23, 59)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            return time(9, 0)
        return time(h, m)
    except Exception:
        return time(9, 0)


def _in_sending_window(now: datetime, campaign: Campaign) -> bool:
    """Check if now is within campaign's sending days and hours."""
    if campaign.sending_days is None or now.weekday() not in campaign.sending_days:
        return False
    start = _parse_time(campaign.sending_hours_start or "09:00")
    end = _parse_time(campaign.sending_hours_end or "17:00")
    t = now.time()
    return start <= t <= end


async def run_send_job():
    """Run once: send today's due emails. Slots are per inbox (QueueSlot.inbox_id)."""
    global last_send_job_run, last_send_job_sent_count
    # Use local time so we match queue_logic (slots are stored in local time) and sending window (09:00–17:00 is local)
    now = datetime.now()
    log.info("Send job running at %s (local)", now.isoformat())

    async with AsyncSessionLocal() as session:
        today = now.date()

        result = await session.execute(select(Inbox))
        inboxes = result.scalars().all()

        total_sent = 0
        # Pre-fetch Google OAuth credentials once for all Gmail inboxes
        g_client_id, g_client_secret = await get_google_oauth_credentials(session)

        for inbox in inboxes:
            # For Gmail inboxes, pre-fetch and refresh the access token
            gmail_token = ""
            inbox_provider = getattr(inbox, "provider", "") or ""
            if inbox_provider == "gmail":
                ga_result = await session.execute(
                    select(GmailAccount).where(GmailAccount.inbox_id == inbox.id)
                )
                ga = ga_result.scalar_one_or_none()
                if ga:
                    # Refresh token if expired (or within 5 min of expiry)
                    if ga.token_expiry and ga.token_expiry <= datetime.utcnow():
                        refreshed = refresh_access_token(ga, g_client_id, g_client_secret)
                        if refreshed:
                            await session.flush()
                        else:
                            log.error("Gmail token refresh failed for inbox %s (%s)", inbox.id, inbox.email)
                            continue
                    gmail_token = ga.access_token
                else:
                    log.warning("Gmail inbox %s (%s) has no GmailAccount — skipping", inbox.id, inbox.email)
                    continue

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
                .where(
                    QueueSlot.inbox_id == inbox.id,
                    func.date(QueueSlot.scheduled_date) == today,
                    QueueSlot.scheduled_date <= now,
                )
                .order_by(QueueSlot.scheduled_date, QueueSlot.position_in_day)
            )
            rows = result.all()

            sent_this_inbox = 0
            max_per_day = inbox.max_emails_per_day

            for slot, cl, campaign, lead, sequence in rows:
                if sent_this_inbox >= max_per_day:
                    break
                if getattr(campaign, 'paused', False):
                    continue  # Skip paused campaigns
                if not _in_sending_window(now, campaign):
                    break

                if lead.status not in ("active",):
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
                if (sequence.subject or "").strip() == "":
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
                    subject = sequence.subject.strip()

                body = render_body(sequence.body, get_lead_data(lead))
                from_addr = inbox.email
                from_name = inbox.display_name or ""
                is_html = (sequence.body or "").strip().lower().startswith("<")

                result = send_email(
                    to_email=lead.email,
                    subject=subject,
                    body=body,
                    from_email=from_addr,
                    from_name=from_name,
                    reply_to_msg_id=reply_to_msg_id,
                    references=references_chain,
                    is_html=is_html,
                    provider=inbox_provider,
                    gmail_access_token=gmail_token,
                    thread_id=prev_thread_id,
                )
                if result:
                    email_log_entry = EmailLog(
                        lead_id=lead.id,
                        campaign_id=campaign.id,
                        inbox_id=inbox.id,  # Track which inbox sent this for inbox persistence
                        sequence_index=slot.sequence_index,
                        subject=subject,
                        message_id=result.message_id,
                        thread_id=result.thread_id,
                    )
                    session.add(email_log_entry)
                    await session.delete(slot)
                    sent_this_inbox += 1
                    total_sent += 1

        await session.commit()

    last_send_job_run = datetime.now()
    last_send_job_sent_count = total_sent
    log.info("Send job finished: %d email(s) sent (next run in %d min)", total_sent, settings.queue_check_interval_minutes)
