"""Test mode API: status endpoint only."""
import logging
from fastapi import APIRouter

from app.settings_manager import settings

log = logging.getLogger("campaign_engine.routes")

router = APIRouter(prefix="/api/test", tags=["test"])


@router.get("/status")
async def test_status():
    """Return whether test mode is on."""
    return {"test_mode": settings.test_mode}
