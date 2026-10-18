"""Notices API routes.

Proxies the public hub announcement/version feeds to the logged-in UI so the
browser never has to call the hub directly (CORS). Authentication is applied
when this router is registered in ``app/main.py`` (like other protected
routers), so no auth decorator is needed here.
"""
from __future__ import annotations

from fastapi import APIRouter, Response

from app.notices import get_notices

router = APIRouter(prefix="/api/notices", tags=["notices"])


@router.get("")
async def list_notices(response: Response):
    """Announcements, release versions, and whether an update is available."""
    data = await get_notices()
    # Auth-gated route: keep the 5-minute cache but never let a shared proxy
    # cache the response across users.
    response.headers["Cache-Control"] = "private, max-age=300"
    return data
