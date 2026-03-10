"""Pydantic schemas for API and validation."""
from pydantic import BaseModel, EmailStr
from typing import Optional, Dict, Any, List
from datetime import datetime, time
from app.models import WEBHOOK_EVENT_TYPES


class LeadCreate(BaseModel):
    email: str
    name: str = ""
    custom_data: Dict[str, Any] = {}


class LeadUpdate(BaseModel):
    name: Optional[str] = None
    custom_data: Optional[Dict[str, Any]] = None
    status: Optional[str] = None  # active, unsubscribed, bounced, replied


class LeadResponse(BaseModel):
    id: int
    email: str
    name: str
    custom_data: Dict[str, Any]
    status: str
    provider: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class InboxCreate(BaseModel):
    email: str
    display_name: str = ""
    max_emails_per_day: int = 50
    wait_minutes_between: int = 5
    provider: str = "gmail"  # gmail | office365
    tracking_domain: Optional[str] = None  # custom hostname for tracking links
    ramp_up_enabled: bool = False
    ramp_up_period_days: int = 42


class InboxUpdate(BaseModel):
    display_name: Optional[str] = None
    max_emails_per_day: Optional[int] = None
    wait_minutes_between: Optional[int] = None
    provider: Optional[str] = None
    tracking_domain: Optional[str] = None  # set to "" to clear
    ramp_up_enabled: Optional[bool] = None
    ramp_up_period_days: Optional[int] = None
    paused: Optional[bool] = None

class InboxResponse(BaseModel):
    id: int
    email: str
    display_name: str
    max_emails_per_day: int
    wait_minutes_between: int
    provider: str
    tracking_domain: Optional[str] = None
    created_at: datetime
    ramp_up_enabled: bool = False
    ramp_up_period_days: int = 42
    paused: bool = False
    effective_max_per_day: int = 0  # computed; 0 means use max_emails_per_day directly
    # how many emails have been sent from this inbox **today** (UTC)
    sent_today: int = 0
    # how many future queue slots are pending on this inbox right now
    pending_leads: int = 0

    class Config:
        from_attributes = True


class PauseInboxRequest(BaseModel):
    action: str  # "pause_leads" or "reassign"
    target_inbox_id: Optional[int] = None


class SequenceVariantCreate(BaseModel):
    label: str = ""
    subject: Optional[str] = None  # None = use sequence subject
    body: str
    is_html: Optional[bool] = None  # None = use sequence is_html
    preview_text: Optional[str] = None
    enabled: bool = True


class SequenceVariantUpdate(BaseModel):
    label: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    is_html: Optional[bool] = None
    preview_text: Optional[str] = None
    enabled: Optional[bool] = None


class SequenceVariantResponse(BaseModel):
    id: int
    sequence_id: int
    label: str
    subject: Optional[str]
    body: str
    is_html: Optional[bool] = None
    preview_text: Optional[str] = None
    enabled: bool
    created_at: datetime

    class Config:
        from_attributes = True


class SequenceCreate(BaseModel):
    position: int
    subject: Optional[str] = None
    body: str
    wait_days_after_previous: int = 0
    is_html: Optional[bool] = None  # None = auto-detect (legacy), True = HTML, False = plain
    preview_text: Optional[str] = None


class SequenceUpdate(BaseModel):
    subject: Optional[str] = None
    body: Optional[str] = None
    wait_days_after_previous: Optional[int] = None
    is_html: Optional[bool] = None
    preview_text: Optional[str] = None


class SequenceResponse(BaseModel):
    id: int
    campaign_id: int
    position: int
    subject: Optional[str]
    body: str
    wait_days_after_previous: int
    is_html: Optional[bool] = None
    preview_text: Optional[str] = None
    variants: List["SequenceVariantResponse"] = []

    class Config:
        from_attributes = True


class CampaignCreate(BaseModel):
    name: str
    inbox_ids: List[int]  # at least one; order = priority for slot assignment
    sending_days: List[int] = [0, 1, 2, 3, 4]  # Mon=0 .. Sun=6
    sending_hours_start: str = "09:00"
    sending_hours_end: str = "17:00"
    wait_minutes_between: int = 5  # Deprecated: wait time is now controlled by inbox settings
    stop_on_reply: bool = True
    paused: bool = False
    priority: int = 0  # Lower value = processed first in priority scheduling
    # Tracking (off by default for better deliverability)
    track_opens: bool = False
    track_clicks: bool = False
    # Unsubscribe header
    add_unsubscribe_header: bool = True
    # Plain-text options
    send_first_as_text: bool = False
    send_all_as_text: bool = False
    # Timezone (IANA name e.g. "America/New_York"); None = user's local timezone
    timezone: Optional[str] = None
    # When True, prefer inboxes matching the lead's email provider (Google → Gmail, O365 → Office 365)
    match_lead_provider: bool = True


class CampaignUpdate(BaseModel):
    name: Optional[str] = None
    inbox_ids: Optional[List[int]] = None
    sending_days: Optional[List[int]] = None
    sending_hours_start: Optional[str] = None
    sending_hours_end: Optional[str] = None
    wait_minutes_between: Optional[int] = None
    stop_on_reply: Optional[bool] = None
    paused: Optional[bool] = None
    priority: Optional[int] = None  # Lower value = processed first in priority scheduling
    track_opens: Optional[bool] = None
    track_clicks: Optional[bool] = None
    add_unsubscribe_header: Optional[bool] = None
    send_first_as_text: Optional[bool] = None
    send_all_as_text: Optional[bool] = None
    timezone: Optional[str] = None
    match_lead_provider: Optional[bool] = None


class CampaignStats(BaseModel):
    """Aggregated metrics that help the frontend display progress/analytics.

    The fields here are intentionally very basic today (lead count, emails
    sent, replies, number of sequences) but they give a single place to grow
    later when we want open rates, positive replies, click rate, etc.  They
    default to zero when no data exists.  ``open_rate`` and ``click_rate``
    are expressed as floats between 0.0 and 1.0 and currently always zero.

    ``scheduled`` is the number of outstanding queue slots for the campaign.
    When a lead replies we delete its remaining slots; including this value
    allows the frontend to compute progress based on the sum of sent +
    scheduled emails rather than assuming every enrolled lead will receive
    every sequence.  Without it the progress bar would still show "incomplete"
    after a reply even though no further messages will be sent.
    """
    total_leads: int = 0
    emails_sent: int = 0
    replies: int = 0
    sequences: int = 0
    # ``scheduled`` counts pending QueueSlot rows for this campaign.  The
    # frontend uses it along with ``emails_sent`` to calculate completion
    # percent, which ensures replied leads (whose slots are deleted) no
    # longer drag down the progress bar.
    scheduled: int = 0
    open_rate: float = 0.0
    click_rate: float = 0.0

    class Config:
        from_attributes = True


class CampaignResponse(BaseModel):
    id: int
    public_id: str
    name: str
    inbox_ids: List[int]
    sending_days: List[int]
    sending_hours_start: str
    sending_hours_end: str
    wait_minutes_between: int
    stop_on_reply: bool
    paused: bool
    priority: int
    track_opens: bool = False
    track_clicks: bool = False
    add_unsubscribe_header: bool = True
    send_first_as_text: bool = False
    send_all_as_text: bool = False
    timezone: Optional[str] = None
    match_lead_provider: bool = True
    created_at: datetime

    # new stats object; the frontend can always rely on ``stats`` being
    # present and it initially contains zeros.
    stats: CampaignStats = CampaignStats()

    class Config:
        from_attributes = True


class AddLeadToCampaign(BaseModel):
    lead_id: int


class CampaignLeadAdd(BaseModel):
    """Used for adding (and optionally creating) leads directly from a campaign."""
    email: str
    name: str = ""
    custom_data: Dict[str, Any] = {}


class QueueSlotResponse(BaseModel):
    id: int
    campaign_lead_id: int
    sequence_index: int
    scheduled_date: datetime
    position_in_day: int

    class Config:
        from_attributes = True


class EmailLogResponse(BaseModel):
    id: int
    lead_id: int
    campaign_id: int
    sequence_index: int
    sent_at: datetime
    subject: str

    class Config:
        from_attributes = True


class MarkReplied(BaseModel):
    lead_id: int
    campaign_id: int


# ---------------------------------------------------------------------------
# Webhook schemas
# ---------------------------------------------------------------------------

class WebhookCreate(BaseModel):
    """Create a new outbound webhook endpoint."""
    url: str
    secret: str = ""
    events: List[str] = list(WEBHOOK_EVENT_TYPES)  # subscribe to all by default
    active: bool = True
    description: str = ""


class WebhookUpdate(BaseModel):
    """Partial update for an existing webhook."""
    url: Optional[str] = None
    secret: Optional[str] = None
    events: Optional[List[str]] = None
    active: Optional[bool] = None
    description: Optional[str] = None


class WebhookResponse(BaseModel):
    id: int
    url: str
    secret: str
    events: List[str]
    active: bool
    description: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
