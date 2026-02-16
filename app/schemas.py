"""Pydantic schemas for API and validation."""
from pydantic import BaseModel, EmailStr
from typing import Optional, Dict, Any, List
from datetime import datetime, time


class LeadCreate(BaseModel):
    email: str
    name: str = ""
    custom_data: Dict[str, Any] = {}
    campaign_id: Optional[int] = None


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


class InboxResponse(BaseModel):
    id: int
    email: str
    display_name: str
    max_emails_per_day: int
    created_at: datetime

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
    wait_minutes_between: int = 5
    stop_on_reply: bool = True


class CampaignUpdate(BaseModel):
    name: Optional[str] = None
    inbox_ids: Optional[List[int]] = None
    sending_days: Optional[List[int]] = None
    sending_hours_start: Optional[str] = None
    sending_hours_end: Optional[str] = None
    wait_minutes_between: Optional[int] = None
    stop_on_reply: Optional[bool] = None


class CampaignResponse(BaseModel):
    id: int
    name: str
    inbox_ids: List[int]
    sending_days: List[int]
    sending_hours_start: str
    sending_hours_end: str
    wait_minutes_between: int
    stop_on_reply: bool
    created_at: datetime

    class Config:
        from_attributes = True


class AddLeadToCampaign(BaseModel):
    lead_id: int


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
