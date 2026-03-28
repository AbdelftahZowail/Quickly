"""Aggregate stats stored in backup manifest (at backup time)."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backup_package import build_manifest_dict
from app.models import Campaign, Inbox, Lead, User


async def collect_backup_manifest(
    db: AsyncSession,
    *,
    encrypted: bool,
) -> dict:
    lead_count = (
        await db.execute(select(func.count()).select_from(Lead))
    ).scalar_one()
    inbox_count = (
        await db.execute(select(func.count()).select_from(Inbox))
    ).scalar_one()
    campaign_count = (
        await db.execute(select(func.count()).select_from(Campaign))
    ).scalar_one()
    user_count = (
        await db.execute(select(func.count()).select_from(User))
    ).scalar_one()
    admin_rows = (
        await db.execute(
            select(User.email).where(User.role == "admin").order_by(User.id)
        )
    ).all()
    admin_emails = [r[0] for r in admin_rows]

    return build_manifest_dict(
        lead_count=int(lead_count or 0),
        inbox_count=int(inbox_count or 0),
        campaign_count=int(campaign_count or 0),
        user_count=int(user_count or 0),
        admin_emails=admin_emails,
        encrypted=encrypted,
    )
