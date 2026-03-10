"""Email provider lookup endpoint.

This router exposes a simple utility for clients to perform the same MX-based
provider detection that the background job uses when leads are created.  The
endpoint is intentionally lightweight and public; DNS MX lookups do not touch
any of the application's private data.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.email_provider import detect_provider_for_email


router = APIRouter(prefix="/api/email-provider", tags=["email-provider"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ProviderResponse(BaseModel):
    email: str = Field(..., description="The address that was checked")
    provider: str | None = Field(
        None, description="Human‑readable provider name or null if lookup failed"
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/detect", response_model=ProviderResponse)
async def detect(email: str = Query(..., min_length=3, description="Email to inspect")):
    """Return the hosting provider for ``email`` based on its MX records.

    The database function ``detect_provider_for_email`` is reused; any errors
    during the DNS lookup result in a ``provider`` of ``None``.  A missing
    ``@`` symbol is treated as a client error (400).
    """
    if "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid email address")

    provider = await detect_provider_for_email(email)
    return ProviderResponse(email=email, provider=provider)
