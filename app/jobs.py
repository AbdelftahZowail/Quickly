"""Background job: send due emails from the queue (or enqueue for approval in test mode)."""
import logging
from datetime import datetime, date, time
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
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
    PendingSend,
    GmailAccount,
)
from app.sender import send_email, render_body, get_lead_data
from app.routers.gmail_oauth import refresh_access_token

log = logging.getLogger(__name__)

# Updated each time run_send_job finishes (for /api/status)
last_send_job_run: datetime | None = None
last_send_job_sent_count: int = 0


def _parse_time(s: str) -> time:
    parts = s.strip().split(":")
    if len(parts) != 2:
        return time(9, 0)
    return time(int(parts[0]), int(parts[1]))


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
                        refreshed = refresh_access_token(ga)
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
                if (sequence.subject or "").strip() == "":
                    prev_log = await session.execute(
                        select(EmailLog).where(
                            EmailLog.lead_id == lead.id,
                            EmailLog.campaign_id == campaign.id,
                        ).order_by(EmailLog.sequence_index.desc()).limit(1)
                    )
                    prev = prev_log.scalar_one_or_none()
                    if prev and prev.message_id:
                        reply_to_msg_id = prev.message_id

                subject = (sequence.subject or "").strip() or "(no subject)"
                body = render_body(sequence.body, get_lead_data(lead))
                from_addr = inbox.email
                from_name = inbox.display_name or ""
                is_html = (sequence.body or "").strip().lower().startswith("<")

                # if settings.test_mode:
                #     # Enqueue for manual approval; do not send
                #     pending = PendingSend(
                #         lead_id=lead.id,
                #         campaign_id=campaign.id,
                #         sequence_index=slot.sequence_index,
                #         to_email=lead.email,
                #         subject=subject,
                #         body=body,
                #         is_html=is_html,
                #         from_email=from_addr,
                #         from_name=from_name or "",
                #         reply_to_msg_id=reply_to_msg_id,
                #     )
                #     session.add(pending)
                #     await session.delete(slot)
                #     sent_this_inbox += 1
                # else:
                message_id = send_email(
                    to_email=lead.email,
                    subject=subject,
                    body=body,
                    from_email=from_addr,
                    from_name=from_name,
                    reply_to_msg_id=reply_to_msg_id,
                    is_html=is_html,
                    provider=inbox_provider,
                    gmail_access_token=gmail_token,
                )
                if message_id:
                    email_log_entry = EmailLog(
                        lead_id=lead.id,
                        campaign_id=campaign.id,
                        sequence_index=slot.sequence_index,
                        subject=subject,
                        message_id=message_id,
                    )
                    session.add(email_log_entry)
                    await session.delete(slot)
                    sent_this_inbox += 1
                    total_sent += 1

        await session.commit()

    last_send_job_run = datetime.now()
    last_send_job_sent_count = total_sent
    log.info("Send job finished: %d email(s) sent (next run in %d min)", total_sent, settings.queue_check_interval_minutes)
