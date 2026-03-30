"""HTTP client for Quickly → Beacon registration (no shared code with /beacon)."""
from __future__ import annotations

# // BEACON SYNC: This file handles tracking logic that Beacon mirrors.
# // Any changes to tracking behavior, event types, metadata collected, or URL structure
# // must also be reflected in the Beacon service (/beacon or the separate Beacon repo).
# // If you are an AI agent, add a TODO at the end of your response listing what needs to be updated in Beacon.

import logging
from typing import Any

import httpx

from app.models import Inbox

log = logging.getLogger("quickly.beacon_client")

# Large inboxes: Beacon register accepts batches to avoid huge single POST bodies.
BEACON_REGISTER_BATCH_SIZE = 250


async def post_beacon_register_items(
    base_url: str,
    setup_token: str,
    items: list[dict[str, Any]],
) -> None:
    """POST /api/v1/register on Beacon. Raises on HTTP error."""
    if not items:
        return
    base = (base_url or "").strip().rstrip("/")
    token = (setup_token or "").strip()
    if not base or not token:
        raise RuntimeError("inbox missing beacon_base_url or beacon_setup_token")

    url = f"{base}/api/v1/register"
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=45.0) as client:
        resp = await client.post(url, json={"items": items}, headers=headers)
        if resp.status_code >= 400:
            log.warning("beacon register HTTP %s: %s", resp.status_code, resp.text[:500])
            resp.raise_for_status()


async def register_beacon_mappings_batched_explicit(
    base_url: str,
    setup_token: str,
    items: list[dict[str, Any]],
) -> None:
    """POST register in batches using explicit Beacon URL and setup token (post-connect sync)."""
    if not items:
        return
    for i in range(0, len(items), BEACON_REGISTER_BATCH_SIZE):
        await post_beacon_register_items(
            base_url, setup_token, items[i : i + BEACON_REGISTER_BATCH_SIZE]
        )


async def register_beacon_mappings_batched(inbox: Inbox, items: list[dict[str, Any]]) -> None:
    """POST /api/v1/register in chunks of ``BEACON_REGISTER_BATCH_SIZE``."""
    if not items:
        return
    for i in range(0, len(items), BEACON_REGISTER_BATCH_SIZE):
        await register_beacon_mappings(inbox, items[i : i + BEACON_REGISTER_BATCH_SIZE])


async def register_beacon_mappings(inbox: Inbox, items: list[dict[str, Any]]) -> None:
    """POST /api/v1/register on Beacon. Raises on HTTP error."""
    if not items:
        return
    if not getattr(inbox, "beacon_connected", False):
        return
    base = (getattr(inbox, "beacon_base_url", None) or "").strip().rstrip("/")
    token = getattr(inbox, "beacon_setup_token", None)
    await post_beacon_register_items(base, token, items)
