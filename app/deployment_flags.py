"""Deployment hints derived from environment (UI, docs alignment).

The production Docker image sets ``QUICKLY_PREBUILT_IMAGE`` so the inbox UI
steers operators toward Quickly Beacon for custom tracking hostnames instead
of CNAME + on-demand TLS to the main app. Advanced self-hosters can re-enable
the CNAME workflow or run the host-Caddy layout (see docs).
"""
from __future__ import annotations

import os


def is_prebuilt_image_deployment() -> bool:
    return os.environ.get("QUICKLY_PREBUILT_IMAGE", "").lower() in (
        "1",
        "true",
        "yes",
    )


def custom_tracking_cname_ui_enabled() -> bool:
    """Whether the Inboxes UI should offer CNAME-based custom tracking domains."""
    if os.environ.get("QUICKLY_TRACKING_CNAME_UI", "").lower() in (
        "1",
        "true",
        "yes",
    ):
        return True
    if is_prebuilt_image_deployment():
        return False
    return True
