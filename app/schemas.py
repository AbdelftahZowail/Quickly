"""Pydantic schemas for API and validation."""
from pydantic import BaseModel, EmailStr
from typing import Optional, Dict, Any, List
from datetime import datetime, time


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
    created_at: datetime

    class Config:
        from_attributes = True


class InboxCreate(BaseModel):
    email: str
    display_name: str = ""
    max_emails_per_day: int = 50
    wait_minutes_between: int = 5
    provider: str = "resend"  # resend | smtp | gmail


class InboxUpdate(BaseModel):
    display_name: Optional[str] = None
    max_emails_per_day: Optional[int] = None
    wait_minutes_between: Optional[int] = None
    provider: Optional[str] = None


class InboxResponse(BaseModel):
    id: int
    email: str
    display_name: str
    max_emails_per_day: int
    wait_minutes_between: int
    provider: str
    created_at: datetime
    # how many emails have been sent from this inbox **today** (UTC)
    sent_today: int = 0

    class Config:
        from_attributes = True


class SequenceCreate(BaseModel):
    position: int
    subject: Optional[str] = None
    body: str
    wait_days_after_previous: int = 0


class SequenceUpdate(BaseModel):
    subject: Optional[str] = None
    body: Optional[str] = None
    wait_days_after_previous: Optional[int] = None


class SequenceResponse(BaseModel):
    id: int
    campaign_id: int
    position: int
    subject: Optional[str]
    body: str
    wait_days_after_previous: int

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


class CampaignStats(BaseModel):
    """Aggregated metrics that help the frontend display progress/analytics.

    The fields here are intentionally very basic today (lead count, emails
    sent, replies, number of sequences) but they give a single place to grow
    later when we want open rates, positive replies, click rate, etc.  They
    default to zero when no data exists.  ``open_rate`` and ``click_rate``
    are expressed as floats between 0.0 and 1.0 and currently always zero.
    """
    total_leads: int = 0
    emails_sent: int = 0
    replies: int = 0
    sequences: int = 0
    open_rate: float = 0.0
    click_rate: float = 0.0

    class Config:
        from_attributes = True


class CampaignResponse(BaseModel):
    id: int
    name: str
    inbox_ids: List[int]
    sending_days: List[int]
    sending_hours_start: str
    sending_hours_end: str
    wait_minutes_between: int
    stop_on_reply: bool
    paused: bool
    priority: int
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
