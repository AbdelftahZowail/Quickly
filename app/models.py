"""SQLAlchemy ORM models."""
# // BEACON SYNC: This file handles tracking logic that Beacon mirrors.
# // Any changes to tracking behavior, event types, metadata collected, or URL structure
# // must also be reflected in the Beacon service (/beacon or the separate Beacon repo).
# // If you are an AI agent, add a TODO at the end of your response listing what needs to be updated in Beacon.

import secrets
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    JSON,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import relationship
from app.time import utcnow as _utcnow

from app.database import Base


def _make_public_id() -> str:
    """Generate a short random public ID (11 chars, URL-safe)."""
    return secrets.token_urlsafe(8)


def _make_open_token() -> str:
    """Generate a random token for open-tracking URLs (~11 chars, URL-safe)."""
    return secrets.token_urlsafe(8)


# ---------------------------------------------------------------------------
# Authentication models
# ---------------------------------------------------------------------------

class User(Base):
    """Application user for authentication."""
    __tablename__ = "app_user"
    __table_args__ = (
        UniqueConstraint("oauth_provider", "oauth_sub", name="uq_user_oauth"),
    )
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(150), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=True)  # nullable for OAuth-only users
    # OAuth identity (app login)
    oauth_provider = Column(String(32), nullable=True)   # "google" or "microsoft"
    oauth_sub = Column(String(255), nullable=True)        # provider's unique subject ID
    # Notification email sending credentials (from OAuth login, separate from campaign inboxes)
    notif_access_token = Column(Text, nullable=True)
    notif_refresh_token = Column(Text, nullable=True)
    notif_token_expiry = Column(DateTime, nullable=True)
    role = Column(String(32), default="user", nullable=False)  # admin or user (first user set explicitly to admin in router)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
    api_keys = relationship("APIKey", back_populates="user", cascade="all, delete-orphan")
    notification_config = relationship("EmailNotificationConfig", back_populates="user", uselist=False, cascade="all, delete-orphan")
    notifications = relationship("Notification", back_populates="user", cascade="all, delete-orphan", order_by="Notification.created_at.desc()")


class APIKey(Base):
    """HMAC-SHA256 hashed API keys for programmatic access.

    The raw key is shown once at creation; only the HMAC hash is persisted.
    The ``prefix`` (first 12 chars) lets users identify keys without
    exposing the secret. Keys are soft-deleted via ``revoked`` flag to
    preserve audit trails.
    """
    __tablename__ = "api_key"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False, default="")
    key_hash = Column(String(512), nullable=False, unique=True, index=True)
    prefix = Column(String(16), nullable=False, default="")
    scopes = Column(JSON, default=list)
    revoked = Column(Boolean, default=False, nullable=False)
    last_used_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    user = relationship("User", back_populates="api_keys")


class Inbox(Base):
    __tablename__ = "inbox"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    display_name = Column(String(255), default="")
    max_emails_per_day = Column(Integer, default=50, nullable=False)
    wait_minutes_between = Column(Integer, default=5, nullable=False)  # Minutes between emails from this inbox
    max_jitter_seconds = Column(Integer, default=180, nullable=False)   # Max random seconds added to each send time (0 = disabled)
    provider = Column(String(32), default="gmail")  # gmail | office365
    # Custom tracking domain for this inbox (hostname only, e.g. "mail.client.com").
    # When set, open/click tracking URLs for emails sent from this inbox will use
    # https://<tracking_domain>/o/... instead of the app's own base URL.
    # Leave NULL to use the app's base URL (default / PaaS-friendly behaviour).
    tracking_domain = Column(String(255), nullable=True, default=None)
    # Beacon (standalone tracking proxy): when connected, tracking URLs use beacon_base_url.
    beacon_base_url = Column(String(512), nullable=True, default=None)
    beacon_setup_token = Column(String(128), nullable=True, default=None)
    beacon_webhook_secret = Column(String(256), nullable=True, default=None)
    beacon_connected = Column(Boolean, default=False, nullable=False)
    connect_token = Column(String(256), nullable=True, unique=True)
    connect_token_expires_at = Column(DateTime, nullable=True, default=None)
    created_at = Column(DateTime, default=_utcnow)
    ramp_up_enabled = Column(Boolean, default=False, nullable=False)
    ramp_up_period_days = Column(Integer, default=42, nullable=False)  # kept for legacy compat; not used in formula
    ramp_up_start = Column(Integer, default=1, nullable=False)  # starting emails per day for ramp-up
    ramp_up_step_size = Column(Integer, default=1, nullable=False)  # emails added per day during warm-up
    ramp_up_started_at = Column(DateTime, nullable=True, default=None)  # when ramp-up was (last) enabled
    paused = Column(Boolean, default=False, nullable=False)
    ramp_up_paused_at = Column(DateTime, nullable=True, default=None)  # when ramp-up was paused (for freezing warm-up)
    campaign_inboxes = relationship("CampaignInbox", back_populates="inbox")
    gmail_account = relationship("GmailAccount", back_populates="inbox", uselist=False, cascade="all, delete-orphan")
    gmail_sync_state = relationship("GmailSyncState", uselist=False, cascade="all, delete-orphan")
    gmail_threads = relationship("GmailThread", back_populates="inbox", cascade="all, delete-orphan")
    office365_account = relationship("Office365Account", back_populates="inbox", uselist=False, cascade="all, delete-orphan")
    office365_sync_state = relationship("Office365SyncState", uselist=False, cascade="all, delete-orphan")
    office365_threads = relationship("Office365Thread", back_populates="inbox", cascade="all, delete-orphan")
    office365_graph_subscription = relationship("Office365GraphSubscription", back_populates="inbox", uselist=False, cascade="all, delete-orphan")


class Lead(Base):
    __tablename__ = "lead"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), nullable=False, index=True)
    name = Column(String(255), default="")
    custom_data = Column(JSON, default=dict)  # e.g. {"company": "...", "title": "..."}
    status = Column(String(32), default="active")  # active, unsubscribed, bounced, replied, invalid
    # Email verification: pending, valid, invalid, catch_all, unknown, risky, or null (not verified)
    email_verification_status = Column(String(32), nullable=True, default=None, index=True)
    # Raw JSON result from the verification provider
    email_verification_result = Column(JSON, nullable=True, default=None)
    # Detected email hosting provider (e.g. "Google Workspace", "Office 365") via MX lookup
    provider = Column(String(64), nullable=True, default=None, index=True)
    created_at = Column(DateTime, default=_utcnow)
    campaign_leads = relationship("CampaignLead", back_populates="lead", cascade="all, delete-orphan")
    email_logs = relationship("EmailLog", back_populates="lead")
    replies = relationship("LeadReply", back_populates="lead")
    unsubscribe_tokens = relationship(
        "LeadUnsubscribeToken",
        back_populates="lead",
        cascade="all, delete-orphan",
    )


class Campaign(Base):
    __tablename__ = "campaign"
    id = Column(Integer, primary_key=True, index=True)
    public_id = Column(String(16), unique=True, nullable=False, index=True, default=_make_public_id)
    name = Column(String(255), nullable=False)
    paused = Column(Boolean, default=False)  # If True, skip sending from this campaign
    priority = Column(Integer, default=0, nullable=False)  # Lower = higher priority in priority-based scheduling
    # sending_days: 0=Mon .. 6=Sun, stored as JSON array e.g. [0,1,2,3,4]
    sending_days = Column(JSON, default=[0, 1, 2, 3, 4])  # Mon-Fri default
    sending_hours_start = Column(String(5), default="09:00")  # 9am
    sending_hours_end = Column(String(5), default="17:00")   # 5pm
    wait_minutes_between = Column(Integer, default=5)  # Deprecated: wait time now controlled by Inbox.wait_minutes_between
    stop_on_reply = Column(Boolean, default=True)
    # Tracking toggles (default OFF for better deliverability)
    track_opens = Column(Boolean, default=False, nullable=False)
    track_clicks = Column(Boolean, default=False, nullable=False)
    # Unsubscribe
    add_unsubscribe_header = Column(Boolean, default=True, nullable=False)
    # Plain-text sending options
    send_first_as_text = Column(Boolean, default=False, nullable=False)  # Force seq 0 to plain text
    send_all_as_text = Column(Boolean, default=False, nullable=False)    # Force every sequence to plain text
    # Timezone for scheduling (IANA timezone name, e.g. "America/New_York")
    timezone = Column(String(64), nullable=True, default=None)
    # Provider matching: when True, prefer inboxes whose provider matches the lead's email provider
    # (Google leads → Gmail inboxes, Office 365 leads → Office 365 inboxes; falls back to any inbox)
    match_lead_provider = Column(Boolean, default=True, nullable=False)
    # Custom sequence mode: wait_for_all (default) = don't send until all personalized
    # emails are written; asap = start sending each personalized email as soon as it's written
    custom_sequence_mode = Column(String(32), default="wait_for_all", nullable=False)
    created_at = Column(DateTime, default=_utcnow)
    campaign_inboxes = relationship(
        "CampaignInbox",
        back_populates="campaign",
        order_by="CampaignInbox.position",
        cascade="all, delete-orphan",
    )
    sequences = relationship("Sequence", back_populates="campaign", order_by="Sequence.position", cascade="all, delete-orphan")
    campaign_leads = relationship("CampaignLead", back_populates="campaign", cascade="all, delete-orphan")
    email_logs = relationship("EmailLog", back_populates="campaign", cascade="all, delete-orphan")
    replies = relationship("LeadReply", back_populates="campaign", cascade="all, delete-orphan")
    # tokens for unsubscribe links; deleted when the campaign is removed
    unsubscribe_tokens = relationship(
        "LeadUnsubscribeToken",
        back_populates="campaign",
        cascade="all, delete-orphan",
    )


class CampaignInbox(Base):
    """Many-to-many: campaign can use multiple inboxes. Order = priority when assigning slots."""
    __tablename__ = "campaign_inbox"
    __table_args__ = (UniqueConstraint("campaign_id", "inbox_id", name="uq_campaign_inbox"),)
    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaign.id"), nullable=False)
    inbox_id = Column(Integer, ForeignKey("inbox.id"), nullable=False)
    position = Column(Integer, default=0)  # 0, 1, 2... for round-robin order
    campaign = relationship("Campaign", back_populates="campaign_inboxes")
    inbox = relationship("Inbox", back_populates="campaign_inboxes")


class Sequence(Base):
    __tablename__ = "sequence"
    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaign.id"), nullable=False)
    position = Column(Integer, nullable=False)  # 0, 1, 2...
    subject = Column(String(512), default=None)  # None = reply in same thread
    body = Column(Text, nullable=False)
    # Explicit HTML flag.  None = legacy auto-detect; True = HTML; False = plain text.
    is_html = Column(Boolean, nullable=True, default=None)
    # Optional preheader/preview text injected as hidden div when is_html is True.
    preview_text = Column(String(512), nullable=True, default=None)
    wait_days_after_previous = Column(Integer, default=0)  # days after previous sequence
    sequence_type = Column(String(32), default="standard", nullable=False)  # standard | personalized
    fallback_subject = Column(String(512), nullable=True, default=None)  # fallback for personalized sequences
    fallback_body = Column(Text, nullable=True, default=None)  # fallback for personalized sequences
    campaign = relationship("Campaign", back_populates="sequences")
    variants = relationship(
        "SequenceVariant",
        back_populates="sequence",
        order_by="SequenceVariant.id",
        cascade="all, delete-orphan",
    )


class SequenceVariant(Base):
    """A/B test variant for a sequence step.

    When a step has one or more enabled variants, the sender picks one at
    random (uniform distribution) instead of the step's default content.
    Both the default content AND named variants participate equally in the
    random draw (i.e., default + N variants → N+1 options).
    """
    __tablename__ = "sequence_variant"
    id = Column(Integer, primary_key=True, index=True)
    sequence_id = Column(
        Integer, ForeignKey("sequence.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label = Column(String(64), nullable=False, default="")  # e.g. "A", "B", or a descriptive name
    subject = Column(String(512), default=None)        # None → use sequence subject
    body = Column(Text, nullable=False, default="")
    is_html = Column(Boolean, nullable=True, default=None)  # None → use sequence is_html
    preview_text = Column(String(512), nullable=True, default=None)
    enabled = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=_utcnow)
    sequence = relationship("Sequence", back_populates="variants")


class CampaignLead(Base):
    __tablename__ = "campaign_lead"
    __table_args__ = (
        # Single: bulk analytics and recalculation scans by campaign
        Index("ix_campaign_lead_campaign_id", "campaign_id"),
        # Composite: inbox-persistence lookup and duplicate-enrollment check
        Index("ix_campaign_lead_lead_campaign", "lead_id", "campaign_id"),
    )
    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaign.id"), nullable=False)
    lead_id = Column(Integer, ForeignKey("lead.id"), nullable=False)
    enrolled_at = Column(DateTime, default=_utcnow)
    # Per-campaign pipeline: active, contacted, completed, bounced, unsubscribed, wrong_person
    enrollment_status = Column(String(32), nullable=False, default="active")
    # Reply intent (per campaign): interested, not_interested, out_of_office, auto_reply, or null
    interest_status = Column(String(32), nullable=True, default=None)
    # Whether sending is paused for this specific campaign-lead pair
    # (e.g. auto-paused by AI classifier when marked not_interested)
    sending_paused = Column(Boolean, default=False, nullable=False)
    campaign = relationship("Campaign", back_populates="campaign_leads")
    lead = relationship("Lead", back_populates="campaign_leads")
    queue_slots = relationship("QueueSlot", back_populates="campaign_lead", cascade="all, delete-orphan", order_by="QueueSlot.sequence_index")
    custom_email_overrides = relationship("CustomEmailOverride", back_populates="campaign_lead", cascade="all, delete-orphan")


class CustomEmailOverride(Base):
    """Per-lead custom content for a personalized sequence step.

    When a sequence has ``sequence_type = 'personalized'``, each lead enrolled
    in the campaign must have a matching ``CustomEmailOverride`` row before
    sending can begin.  The sender uses this row's subject/body instead of the
    sequence defaults.
    """
    __tablename__ = "custom_email_override"
    __table_args__ = (
        UniqueConstraint("campaign_lead_id", "sequence_id", name="uq_campaign_lead_seq_override"),
        Index("ix_custom_email_override_cl", "campaign_lead_id"),
    )
    id = Column(Integer, primary_key=True, index=True)
    campaign_lead_id = Column(
        Integer, ForeignKey("campaign_lead.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence_id = Column(
        Integer, ForeignKey("sequence.id", ondelete="CASCADE"), nullable=False
    )
    subject = Column(String(512), nullable=True, default=None)
    body = Column(Text, nullable=True, default=None)
    is_html = Column(Boolean, nullable=True, default=None)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
    campaign_lead = relationship("CampaignLead", back_populates="custom_email_overrides")
    sequence = relationship("Sequence")


class QueueSlot(Base):
    __tablename__ = "queue_slot"
    __table_args__ = (
        UniqueConstraint("campaign_lead_id", "sequence_index", name="uq_campaign_lead_sequence"),
        Index("ix_queue_slot_scheduled_date", "scheduled_date"),
        Index("ix_queue_slot_scheduled_date_pos", "scheduled_date", "position_in_day"),
        # Composite: send job filters by (inbox_id, scheduled_date) on every tick
        Index("ix_queue_slot_inbox_date", "inbox_id", "scheduled_date"),
    )
    id = Column(Integer, primary_key=True, index=True)
    campaign_lead_id = Column(Integer, ForeignKey("campaign_lead.id"), nullable=False)
    inbox_id = Column(Integer, ForeignKey("inbox.id"), nullable=False)  # which inbox sends this slot
    sequence_index = Column(Integer, nullable=False)  # 0, 1, 2 matching sequence position
    scheduled_date = Column(DateTime, nullable=False)  # date part used for "which day"
    position_in_day = Column(Integer, nullable=False)  # 1, 2, 3... for send order that day (per inbox)
    # Pre-assigned A/B variant (set during recalculation; None = default sequence content)
    variant_id = Column(
        Integer, ForeignKey("sequence_variant.id", ondelete="SET NULL"), nullable=True, default=None
    )
    campaign_lead = relationship("CampaignLead", back_populates="queue_slots")
    inbox = relationship("Inbox")
    variant = relationship("SequenceVariant", foreign_keys=[variant_id])


class EmailLog(Base):
    __tablename__ = "email_log"
    __table_args__ = (
        Index("ix_email_log_sent_at", "sent_at"),
        # Composite: send job queries last-sent-time and daily count per inbox
        Index("ix_email_log_inbox_sent_at", "inbox_id", "sent_at"),
        # Composite: follow-up thread-building and inbox-persistence checks
        Index("ix_email_log_lead_campaign", "lead_id", "campaign_id"),
        # Single: campaign-level aggregate queries (list_campaigns analytics)
        Index("ix_email_log_campaign", "campaign_id"),
    )
    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("lead.id"), nullable=False)
    campaign_id = Column(Integer, ForeignKey("campaign.id"), nullable=False)
    inbox_id = Column(Integer, ForeignKey("inbox.id"), nullable=True)  # Track which inbox sent this email
    # sequence_index indicates which position in a campaign sequence this
    # log entry corresponds to.  Historically tests assumed a default of 0
    # when unspecified, so include that default here to avoid failures when
    # callers omit the value.
    sequence_index = Column(Integer, nullable=False, default=0)
    # A/B variant that was sent (NULL = default / no variant used)
    variant_id = Column(
        Integer, ForeignKey("sequence_variant.id", ondelete="SET NULL"), nullable=True, default=None
    )
    open_token = Column(String(32), unique=True, nullable=True, index=True, default=_make_open_token)
    format_override = Column(String(64), nullable=True, default=None)  # why format was overridden
    sent_at = Column(DateTime, default=_utcnow)
    subject = Column(String(512), default="")
    message_id = Column(String(512), default=None)  # RFC 822 Message-ID for In-Reply-To threading
    thread_id = Column(String(512), default=None)   # Gmail threadId for thread continuity
    opened = Column(Boolean, default=False, nullable=False)
    clicked = Column(Boolean, default=False, nullable=False)
    lead = relationship("Lead", back_populates="email_logs")
    campaign = relationship("Campaign", back_populates="email_logs")
    inbox = relationship("Inbox")
    variant = relationship("SequenceVariant", foreign_keys=[variant_id])
    # ensure clicks/opens are removed when a log is deleted rather than
    # nullifying the FK (which would cause integrity errors since the
    # column is non-nullable).  our campaign deletion path cascades into
    # EmailLog, so we must also cascade these sub-objects.
    opens = relationship(
        "EmailOpen",
        back_populates="email_log",
        cascade="all, delete-orphan",
    )
    clicks = relationship(
        "EmailClick",
        back_populates="email_log",
        cascade="all, delete-orphan",
    )
    tracked_links = relationship(
        "TrackedLink",
        back_populates="email_log",
        cascade="all, delete-orphan",
    )


class LeadUnsubscribeToken(Base):
    """Stores a persistent unsubscribe token for each (lead, campaign) pair.

    The token is embedded in the ``{{unsubscribe_link}}`` placeholder and
    in the ``List-Unsubscribe`` header.  Hitting ``GET /u/<token>`` marks the
    lead as unsubscribed and removes their remaining queue slots.
    """
    __tablename__ = "lead_unsubscribe_token"
    __table_args__ = (UniqueConstraint("lead_id", "campaign_id", name="uq_lead_campaign_unsubscribe"),)
    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("lead.id", ondelete="CASCADE"), nullable=False)
    campaign_id = Column(Integer, ForeignKey("campaign.id", ondelete="CASCADE"), nullable=False)
    token = Column(String(64), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=_utcnow)
    # relationships for convenient cascades & joins
    lead = relationship("Lead", back_populates="unsubscribe_tokens")
    campaign = relationship("Campaign", back_populates="unsubscribe_tokens")


class LeadReply(Base):
    __tablename__ = "lead_reply"
    __table_args__ = (
        # Composite: stop_on_reply check runs inside the per-slot send loop
        Index("ix_lead_reply_lead_campaign", "lead_id", "campaign_id"),
    )
    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("lead.id"), nullable=False)
    campaign_id = Column(Integer, ForeignKey("campaign.id"), nullable=False)
    replied_at = Column(DateTime, default=_utcnow)
    lead = relationship("Lead", back_populates="replies")
    campaign = relationship("Campaign", back_populates="replies")


class EmailOpen(Base):
    __tablename__ = "email_open"
    id = Column(Integer, primary_key=True, index=True)
    # cascade at the database level too so that orphan rows are removed even
    # if someone issues a DELETE directly via SQL.  SQLite ignores ondelete
    # but Postgres will enforce it.
    email_log_id = Column(
        Integer,
        ForeignKey("email_log.id", ondelete="CASCADE"),
        nullable=False,
    )
    ip_address = Column(String(45), nullable=True)  # IPv4/IPv6
    opened_at = Column(DateTime, default=_utcnow)
    email_log = relationship("EmailLog", back_populates="opens")


class EmailClick(Base):
    __tablename__ = "email_click"
    id = Column(Integer, primary_key=True, index=True)
    email_log_id = Column(
        Integer,
        ForeignKey("email_log.id", ondelete="CASCADE"),
        nullable=False,
    )
    ip_address = Column(String(45), nullable=True)
    clicked_at = Column(DateTime, default=_utcnow)
    email_log = relationship("EmailLog", back_populates="clicks")


class TrackedLink(Base):
    """One row per rewritten href in a sent HTML email.

    ``token`` is a random URL-safe string embedded in the click-tracking
    redirect URL (``/c/<token>``).  The redirect logs the click and sends
    the recipient to ``original_url``.
    """

    __tablename__ = "tracked_link"

    id = Column(Integer, primary_key=True, index=True)
    email_log_id = Column(
        Integer,
        ForeignKey("email_log.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token = Column(String(64), unique=True, nullable=False, index=True)
    original_url = Column(Text, nullable=False)
    created_at = Column(DateTime, default=_utcnow)

    email_log = relationship("EmailLog", back_populates="tracked_links")


class AppSetting(Base):
    """Key-value store for application settings (e.g. OAuth credentials).

    Keeps sensitive config in the database instead of .env so the
    frontend never needs direct filesystem access.
    """
    __tablename__ = "app_setting"
    key = Column(String(255), primary_key=True, nullable=False)
    value = Column(Text, nullable=False, default="")
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class GmailAccount(Base):
    """Stores Gmail/G Suite OAuth 2.0 tokens linked to an Inbox."""
    __tablename__ = "gmail_account"
    id = Column(Integer, primary_key=True, index=True)
    inbox_id = Column(Integer, ForeignKey("inbox.id"), nullable=False, unique=True)
    google_email = Column(String(255), nullable=False)
    access_token = Column(Text, nullable=False)
    refresh_token = Column(Text, nullable=False)
    token_expiry = Column(DateTime, nullable=True)
    scopes = Column(String(1024), default="https://www.googleapis.com/auth/gmail.send")
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
    inbox = relationship("Inbox", back_populates="gmail_account")


class GmailSyncState(Base):
    """Tracks Gmail history sync checkpoints and watch expiry per inbox."""
    __tablename__ = "gmail_sync_state"
    id = Column(Integer, primary_key=True, index=True)
    inbox_id = Column(Integer, ForeignKey("inbox.id"), nullable=False, unique=True)
    anchor_history_id = Column(String(64), default="")
    latest_history_id = Column(String(64), default="")
    oldest_internal_date = Column(BigInteger, nullable=True)
    last_history_id = Column(String(64), default="")
    watch_expiration = Column(DateTime, nullable=True)
    last_sync_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
    inbox = relationship("Inbox", back_populates="gmail_sync_state")


class GmailThread(Base):
    """Local authoritative metadata mirror for Gmail threads."""

    __tablename__ = "gmail_thread"
    __table_args__ = (
        Index("ix_gmail_thread_inbox_last_internal", "inbox_id", "last_internal_date"),
    )

    inbox_id = Column(Integer, ForeignKey("inbox.id"), primary_key=True)
    thread_id = Column(String(128), primary_key=True)
    history_id = Column(String(64), nullable=False, default="")
    snippet = Column(Text, default="")
    last_internal_date = Column(BigInteger, nullable=True)
    # Lead notification tracking
    is_lead_thread = Column(Boolean, default=False, nullable=False)
    unread_lead_reply = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    inbox = relationship("Inbox", back_populates="gmail_threads")
    messages = relationship("GmailMessage", back_populates="thread", cascade="all, delete-orphan")


class GmailMessage(Base):
    """Local Gmail message mirror (metadata eager, bodies lazy)."""

    __tablename__ = "gmail_message"
    __table_args__ = (
        ForeignKeyConstraint(
            ["inbox_id", "thread_id"],
            ["gmail_thread.inbox_id", "gmail_thread.thread_id"],
            name="fk_gmail_message_thread",
            ondelete="CASCADE",
        ),
        Index("ix_gmail_message_inbox_thread_date", "inbox_id", "thread_id", "internal_date"),
        Index("ix_gmail_message_inbox_internal_date", "inbox_id", "internal_date"),
    )

    inbox_id = Column(Integer, ForeignKey("inbox.id"), primary_key=True)
    message_id = Column(String(128), primary_key=True)
    thread_id = Column(String(128), nullable=False)
    internal_date = Column(BigInteger, nullable=True)
    snippet = Column(Text, default="")
    headers_json = Column(Text, default="[]")
    label_ids_json = Column(Text, default="[]")

    body_fetched = Column(Boolean, default=False, nullable=False)
    body_plain = Column(Text, default="")
    body_html = Column(Text, default="")
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    inbox = relationship("Inbox")
    thread = relationship("GmailThread", back_populates="messages")
    attachments = relationship("GmailAttachment", back_populates="message", cascade="all, delete-orphan")


class GmailAttachment(Base):
    """Attachment metadata and optional downloaded bytes (lazy)."""

    __tablename__ = "gmail_attachment"
    __table_args__ = (
        UniqueConstraint(
            "inbox_id",
            "message_id",
            "attachment_id",
            name="uq_gmail_attachment_message_attachment",
        ),
        ForeignKeyConstraint(
            ["inbox_id", "message_id"],
            ["gmail_message.inbox_id", "gmail_message.message_id"],
            name="fk_gmail_attachment_message",
            ondelete="CASCADE",
        ),
        Index("ix_gmail_attachment_inbox_message", "inbox_id", "message_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    inbox_id = Column(Integer, ForeignKey("inbox.id"), nullable=False)
    attachment_id = Column(String(256), nullable=False)
    message_id = Column(String(128), nullable=False)
    filename = Column(String(1024), default="")
    mime_type = Column(String(255), default="")
    size = Column(Integer, default=0)
    data = Column(LargeBinary, nullable=True)
    downloaded = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    message = relationship("GmailMessage", back_populates="attachments")


# ---------------------------------------------------------------------------
# Office 365 / Microsoft Graph integration models
# ---------------------------------------------------------------------------

class Office365Account(Base):
    """Stores Microsoft Office 365 OAuth 2.0 tokens linked to an Inbox."""
    __tablename__ = "office365_account"
    id = Column(Integer, primary_key=True, index=True)
    inbox_id = Column(Integer, ForeignKey("inbox.id"), nullable=False, unique=True)
    microsoft_email = Column(String(255), nullable=False)
    access_token = Column(Text, nullable=False)
    refresh_token = Column(Text, nullable=False)
    token_expiry = Column(DateTime, nullable=True)
    scopes = Column(String(1024), default="Mail.ReadWrite Mail.Send User.Read offline_access")
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
    inbox = relationship("Inbox", back_populates="office365_account")


class Office365SyncState(Base):
    """Tracks Microsoft Graph delta sync checkpoints per inbox."""
    __tablename__ = "office365_sync_state"
    id = Column(Integer, primary_key=True, index=True)
    inbox_id = Column(Integer, ForeignKey("inbox.id"), nullable=False, unique=True)
    delta_link = Column(Text, default="")            # Graph API delta link for Inbox
    sent_delta_link = Column(Text, default="")       # Graph API delta link for SentItems
    junk_delta_link = Column(Text, default="")        # Graph API delta link for JunkEmail
    last_sync_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
    inbox = relationship("Inbox", back_populates="office365_sync_state")


class Office365Thread(Base):
    """Local metadata mirror for Office 365 conversation threads."""
    __tablename__ = "office365_thread"
    __table_args__ = (
        Index("ix_o365_thread_inbox_last_date", "inbox_id", "last_received_at"),
    )
    inbox_id = Column(Integer, ForeignKey("inbox.id"), primary_key=True)
    conversation_id = Column(String(256), primary_key=True)
    subject = Column(Text, default="")
    last_received_at = Column(DateTime, nullable=True)
    is_lead_thread = Column(Boolean, default=False, nullable=False)
    unread_lead_reply = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
    inbox = relationship("Inbox", back_populates="office365_threads")
    messages = relationship("Office365Message", back_populates="thread", cascade="all, delete-orphan")


class Office365Message(Base):
    """Local Office 365 message mirror (metadata eager, bodies lazy)."""
    __tablename__ = "office365_message"
    __table_args__ = (
        ForeignKeyConstraint(
            ["inbox_id", "conversation_id"],
            ["office365_thread.inbox_id", "office365_thread.conversation_id"],
            name="fk_o365_message_thread",
            ondelete="CASCADE",
        ),
        Index("ix_o365_message_inbox_conv_date", "inbox_id", "conversation_id", "received_at"),
        Index("ix_o365_message_inbox_received", "inbox_id", "received_at"),
    )
    inbox_id = Column(Integer, ForeignKey("inbox.id"), primary_key=True)
    message_id = Column(String(256), primary_key=True)  # Graph API message id
    conversation_id = Column(String(256), nullable=False)
    internet_message_id = Column(String(512), nullable=True)  # RFC 822 Message-ID
    received_at = Column(DateTime, nullable=True)
    subject = Column(Text, default="")
    from_address = Column(String(255), default="")
    to_addresses = Column(Text, default="")  # JSON array of recipients
    body_plain = Column(Text, default="")
    body_html = Column(Text, default="")
    is_read = Column(Boolean, default=False, nullable=False)
    has_attachments = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
    inbox = relationship("Inbox")
    thread = relationship("Office365Thread", back_populates="messages")


class Office365GraphSubscription(Base):
    """Microsoft Graph change notification subscription for an Office 365 inbox.

    Subscriptions notify Quickly in real time when new mail arrives,
    eliminating the polling delay for reply detection and unibox updates.
    Subscriptions are valid for up to ~3 days and must be periodically renewed.
    """
    __tablename__ = "office365_graph_subscription"
    id = Column(Integer, primary_key=True, index=True)
    inbox_id = Column(Integer, ForeignKey("inbox.id"), nullable=False, unique=True)
    subscription_id = Column(String(256), nullable=False, index=True)  # Microsoft-assigned GUID
    client_state = Column(String(128), nullable=False)  # Random secret for notification validation
    resource = Column(String(512), default="me/mailFolders/Inbox/messages")
    change_type = Column(String(64), default="created")
    expiry = Column(DateTime, nullable=False)  # UTC expiry from Microsoft Graph
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
    inbox = relationship("Inbox", back_populates="office365_graph_subscription")


class Webhook(Base):
    """User-defined outbound webhook endpoints.

    Each webhook can subscribe to specific event types. When an event occurs,
    all active webhooks subscribed to that event type receive a POST request
    with a JSON payload containing the event name and data.
    """
    __tablename__ = "webhook"
    id = Column(Integer, primary_key=True, index=True)
    url = Column(String(1024), nullable=False)
    secret = Column(String(512), default="")  # Bearer token for authentication
    events = Column(JSON, default=list)  # e.g. ["email.sent", "email.opened"]
    active = Column(Boolean, default=True, nullable=False)
    description = Column(String(255), default="")  # optional human-readable label
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class KnownIP(Base):
    """IP addresses belonging to the app user (collected from sessions).

    Opens/clicks from these IPs are silently ignored to avoid inflating
    analytics.  Addresses expire after ``expires_at`` unless ``permanent``
    is ``True``.
    """
    __tablename__ = "known_ip"
    id = Column(Integer, primary_key=True, index=True)
    ip_address = Column(String(45), nullable=False, index=True)
    permanent = Column(Boolean, default=False, nullable=False)
    last_seen_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
    expires_at = Column(DateTime, nullable=True)  # NULL for permanent entries
    created_at = Column(DateTime, default=_utcnow)


class OAuthState(Base):
    """CSRF nonce for OAuth flows (app login + inbox connections).

    Each record is single-use: validated once then deleted.  Records
    expire after 10 minutes to prevent stale state replay.
    """
    __tablename__ = "oauth_state"
    id = Column(Integer, primary_key=True, index=True)
    state_token = Column(String(64), unique=True, nullable=False, index=True)
    purpose = Column(String(32), nullable=False)  # app_login | inbox_google | inbox_microsoft
    metadata_json = Column(Text, default="{}")
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=_utcnow)


class PendingOAuthConnect(Base):
    """Temporary storage for a one-time OAuth connect URL (new inbox flow).

    A token is generated and stored here when an authenticated user requests a
    connect URL for a *new* inbox (whose email is not yet known).  The public
    ``GET /oauth/connect/{token}`` handler reads this record, marks it used, and
    redirects to the appropriate OAuth provider with the stored inbox parameters
    encoded in the CSRF state.
    """
    __tablename__ = "pending_oauth_connect"
    id = Column(Integer, primary_key=True, index=True)
    token = Column(String(256), unique=True, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    metadata_json = Column(Text, nullable=False)
    used = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=_utcnow)


class Notification(Base):
    """Persisted in-app notification for a user.

    Each row represents one event the user should be notified about.
    When ``read_at`` is NULL the notification is unread.
    Foreign keys to lead / campaign / inbox enable deep-linking in the UI.
    """
    __tablename__ = "notification"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = Column(String(64), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    data_json = Column(JSON, default=dict)  # raw payload snapshot
    lead_id = Column(Integer, ForeignKey("lead.id", ondelete="SET NULL"), nullable=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaign.id", ondelete="SET NULL"), nullable=True, index=True)
    inbox_id = Column(Integer, ForeignKey("inbox.id", ondelete="SET NULL"), nullable=True, index=True)
    read_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=_utcnow)
    user = relationship("User", back_populates="notifications")


class EmailNotificationConfig(Base):
    """Per-user notification preferences (channels: in-app + optional email).

    Controls which event types generate notifications and whether an
    email is also sent for each event type.
    """
    __tablename__ = "email_notification_config"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False, unique=True)
    enabled = Column(Boolean, default=False, nullable=False)  # master toggle for email channel
    notification_email = Column(String(255), default="")  # override recipient; empty = user.email
    events = Column(JSON, default=list)  # empty list = all events
    rate_limit_per_hour = Column(Integer, default=10, nullable=False)
    notifications_sent_this_hour = Column(Integer, default=0, nullable=False)
    rate_window_start = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
    user = relationship("User", back_populates="notification_config")


# All supported webhook event types
WEBHOOK_EVENT_TYPES = [
    "email.sent",          # An email was successfully sent to a lead
    "email.opened",        # A lead opened an email (tracking pixel loaded)
    "email.clicked",       # A lead clicked a link in an email
    "email.bounced",       # An email bounced (permanent delivery failure)
    "lead.replied",        # A lead replied to a campaign email
    "lead.unsubscribed",   # A lead clicked the unsubscribe link
    "lead.status_changed", # A lead's status was changed (any status transition)
    "lead.interested",     # AI classified a lead's reply as interested
    "lead.not_interested", # AI classified a lead's reply as not interested
    "lead.out_of_office",  # AI classified a lead's reply as out-of-office
    "lead.wrong_person",   # AI classified a lead's reply as wrong person
    "lead.auto_reply",     # AI classified a lead's reply as an automated reply
    "daily_limit",         # An inbox hit its daily sending limit
    "rate_limit",          # A rate limit violation was detected
    "token_expired",       # A Gmail OAuth token could not be refreshed
]
