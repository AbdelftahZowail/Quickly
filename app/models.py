"""SQLAlchemy ORM models."""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, JSON, UniqueConstraint
from sqlalchemy.orm import relationship
from app.time import utcnow as _utcnow

from app.database import Base


class Inbox(Base):
    __tablename__ = "inbox"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    display_name = Column(String(255), default="")
    max_emails_per_day = Column(Integer, default=50, nullable=False)
    wait_minutes_between = Column(Integer, default=5, nullable=False)  # Minutes between emails from this inbox
    provider = Column(String(32), default="resend")  # resend | smtp | gmail
    created_at = Column(DateTime, default=_utcnow)
    campaign_inboxes = relationship("CampaignInbox", back_populates="inbox")
    gmail_account = relationship("GmailAccount", back_populates="inbox", uselist=False, cascade="all, delete-orphan")


class Lead(Base):
    __tablename__ = "lead"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), nullable=False, index=True)
    name = Column(String(255), default="")
    custom_data = Column(JSON, default=dict)  # e.g. {"company": "...", "title": "..."}
    status = Column(String(32), default="active")  # active, unsubscribed, bounced, replied
    created_at = Column(DateTime, default=_utcnow)
    campaign_leads = relationship("CampaignLead", back_populates="lead", cascade="all, delete-orphan")
    email_logs = relationship("EmailLog", back_populates="lead")
    replies = relationship("LeadReply", back_populates="lead")


class Campaign(Base):
    __tablename__ = "campaign"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    paused = Column(Boolean, default=False)  # If True, skip sending from this campaign
    priority = Column(Integer, default=0, nullable=False)  # Lower = higher priority in priority-based scheduling
    # sending_days: 0=Mon .. 6=Sun, stored as JSON array e.g. [0,1,2,3,4]
    sending_days = Column(JSON, default=[0, 1, 2, 3, 4])  # Mon-Fri default
    sending_hours_start = Column(String(5), default="09:00")  # 9am
    sending_hours_end = Column(String(5), default="17:00")   # 5pm
    wait_minutes_between = Column(Integer, default=5)  # Deprecated: wait time now controlled by Inbox.wait_minutes_between
    stop_on_reply = Column(Boolean, default=True)
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
    wait_days_after_previous = Column(Integer, default=0)  # days after previous sequence
    campaign = relationship("Campaign", back_populates="sequences")


class CampaignLead(Base):
    __tablename__ = "campaign_lead"
    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaign.id"), nullable=False)
    lead_id = Column(Integer, ForeignKey("lead.id"), nullable=False)
    enrolled_at = Column(DateTime, default=_utcnow)
    campaign = relationship("Campaign", back_populates="campaign_leads")
    lead = relationship("Lead", back_populates="campaign_leads")
    queue_slots = relationship("QueueSlot", back_populates="campaign_lead", cascade="all, delete-orphan", order_by="QueueSlot.sequence_index")


class QueueSlot(Base):
    __tablename__ = "queue_slot"
    __table_args__ = (UniqueConstraint("campaign_lead_id", "sequence_index", name="uq_campaign_lead_sequence"),)
    id = Column(Integer, primary_key=True, index=True)
    campaign_lead_id = Column(Integer, ForeignKey("campaign_lead.id"), nullable=False)
    inbox_id = Column(Integer, ForeignKey("inbox.id"), nullable=False)  # which inbox sends this slot
    sequence_index = Column(Integer, nullable=False)  # 0, 1, 2 matching sequence position
    scheduled_date = Column(DateTime, nullable=False)  # date part used for "which day"
    position_in_day = Column(Integer, nullable=False)  # 1, 2, 3... for send order that day (per inbox)
    campaign_lead = relationship("CampaignLead", back_populates="queue_slots")
    inbox = relationship("Inbox")


class EmailLog(Base):
    __tablename__ = "email_log"
    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("lead.id"), nullable=False)
    campaign_id = Column(Integer, ForeignKey("campaign.id"), nullable=False)
    inbox_id = Column(Integer, ForeignKey("inbox.id"), nullable=True)  # Track which inbox sent this email
    sequence_index = Column(Integer, nullable=False)
    sent_at = Column(DateTime, default=_utcnow)
    subject = Column(String(512), default="")
    message_id = Column(String(512), default=None)  # RFC 822 Message-ID for In-Reply-To threading
    thread_id = Column(String(512), default=None)   # Gmail threadId for thread continuity
    lead = relationship("Lead", back_populates="email_logs")
    campaign = relationship("Campaign", back_populates="email_logs")
    inbox = relationship("Inbox")


class LeadReply(Base):
    __tablename__ = "lead_reply"
    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("lead.id"), nullable=False)
    campaign_id = Column(Integer, ForeignKey("campaign.id"), nullable=False)
    replied_at = Column(DateTime, default=_utcnow)
    lead = relationship("Lead", back_populates="replies")
    campaign = relationship("Campaign", back_populates="replies")


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
