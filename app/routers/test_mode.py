"""Test mode API: status endpoint only."""
import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.settings_manager import settings
from app.database import get_db
from app.app_settings import set_test_mode

log = logging.getLogger("quickly.routes")

router = APIRouter(prefix="/api/test", tags=["test"])


@router.get("/status")
async def test_status():
    """Return whether test mode is on."""
    return {"test_mode": settings.test_mode}


# for clients that want to toggle test mode via the legacy endpoint
class _TestModePayload(BaseModel):
    test_mode: bool


@router.post("/status")
async def set_test_status(
    payload: _TestModePayload,
    db: AsyncSession = Depends(get_db),
):
    """Enable or disable test mode.

    This mirrors the functionality now exposed under
    ``/api/settings/test-mode`` and exists for backwards compatibility.
    """
    try:
        await set_test_mode(db, payload.test_mode)
        await db.commit()
        return {"test_mode": payload.test_mode}
    except Exception as e:
        raise HTTPException(500, f"Failed to update test mode: {e}")
