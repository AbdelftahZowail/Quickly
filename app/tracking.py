"""Tracking link injection and open-pixel utilities.

This module is intentionally free of database or async dependencies so it
can be called synchronously from the send job and tested in isolation.
"""
from __future__ import annotations

# // BEACON SYNC: This file handles tracking logic that Beacon mirrors.
# // Any changes to tracking behavior, event types, metadata collected, or URL structure
# // must also be reflected in the Beacon service (/beacon or the separate Beacon repo).
# // If you are an AI agent, add a TODO at the end of your response listing what needs to be updated in Beacon.

import base64
import re
import secrets
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# Pending tracking-domain allowlist
# ---------------------------------------------------------------------------
# When a user clicks "Check" on a custom tracking domain before it is saved
# to an inbox, we temporarily whitelist the domain so that Caddy's
# on_demand_tls ask endpoint approves it and can provision an SSL cert.
# Each entry expires after 24 hours.
_pending_domains: dict[str, datetime] = {}
_PENDING_TTL = timedelta(hours=24)


def register_pending_domain(domain: str) -> None:
    """Add *domain* to the short-lived allowlist for Caddy cert provisioning.

    Safe to call from any thread/coroutine — dict operations are GIL-protected.
    Opportunistically removes expired entries on each call.
    """
    now = datetime.utcnow()
    _pending_domains[domain] = now
    # Lazy cleanup of expired entries to avoid unbounded growth.
    cutoff = now - _PENDING_TTL
    expired = [d for d, ts in list(_pending_domains.items()) if ts < cutoff]
    for d in expired:
        _pending_domains.pop(d, None)


def is_domain_pending(domain: str) -> bool:
    """Return True if *domain* is in the pending allowlist and not yet expired."""
    ts = _pending_domains.get(domain)
    if ts is None:
        return False
    if datetime.utcnow() - ts > _PENDING_TTL:
        _pending_domains.pop(domain, None)
        return False
    return True

# ── 1×1 transparent GIF ──────────────────────────────────────────────────────
# Standard minimal GIF89a pixel used universally for email open tracking.
PIXEL_GIF: bytes = base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
)

# Match href="https://..." or href='https://...' inside any tag.
# We only rewrite absolute http/https URLs; mailto: and anchor (#) links are
# left untouched.
_HREF_RE = re.compile(
    r'(<a\b[^>]*?\s)href=(["\'])(https?://[^"\'>\s]+)\2',
    re.IGNORECASE | re.DOTALL,
)

# Tokeniser: splits HTML into tag tokens vs text nodes.
_HTML_TOKEN_RE = re.compile(r'(<[^>]+>)', re.DOTALL)
# Bare URL inside a text node (not wrapped in an <a> tag yet).
_BARE_URL_RE = re.compile(r'(https?://[^\s<>"\']+)', re.IGNORECASE)


def make_tracking_token() -> str:
    """Return a 22-char URL-safe random token for one tracked link."""
    return secrets.token_urlsafe(16)


def _inject_bare_url_tracking(
    html: str,
    tracking_base: str,
    link_pairs: list[tuple[str, str]],
) -> str:
    """Wrap plain-text URLs (not already inside ``<a>`` tags) with click-tracking links.

    Gmail auto-linkifies bare URLs so recipients can click them, but they
    bypass the ``_HREF_RE`` rewrite above because they have no ``href``
    attribute.  This function tokenises the HTML into tag vs text nodes,
    then rewrites bare ``http(s)://`` URLs found in *text* nodes only,
    leaving tag attributes untouched.
    """
    tokens = _HTML_TOKEN_RE.split(html)
    result: list[str] = []
    in_anchor = False

    def _replace_url(m: re.Match) -> str:
        url = m.group(1)
        # Strip trailing punctuation that is almost certainly not part of the URL.
        stripped = url.rstrip(".,;:!?)")
        trailing = url[len(stripped):]
        if stripped.startswith(tracking_base):
            return m.group(0)
        token = make_tracking_token()
        link_pairs.append((token, stripped))
        return f'<a href="{tracking_base}/c/{token}">{stripped}</a>{trailing}'

    for token in tokens:
        if _HTML_TOKEN_RE.fullmatch(token):
            # It is an HTML tag — track <a> depth so we don't double-wrap.
            tag_inner = token[1:-1].strip()
            tag_name = tag_inner.split()[0].lower() if tag_inner else ""
            if tag_name == "a":
                in_anchor = True
            elif tag_name == "/a":
                in_anchor = False
            result.append(token)
        else:
            # Text node — only rewrite when we are *not* inside an anchor.
            if in_anchor:
                result.append(token)
            else:
                result.append(_BARE_URL_RE.sub(_replace_url, token))

    return "".join(result)


def inject_tracking_html(
    html: str,
    email_log_id: int,
    tracking_base: str,
    track_opens: bool = True,
    track_clicks: bool = True,
    open_token: str | None = None,
) -> tuple[str, list[tuple[str, str]]]:
    """Rewrite every http(s) href in *html* to a click-tracking redirect URL
    and append a 1×1 open-tracking pixel just before ``</body>`` (or at end).

    Links whose URL already starts with *tracking_base* (e.g. the unsubscribe
    link ``/u/{token}``) are left untouched — they are already served by this
    same server and need no extra redirection.

    The open pixel URL uses *open_token* when provided (non-incremental ID),
    falling back to *email_log_id* for backwards compatibility.

    Returns:
        ``(new_html, link_pairs)`` where *link_pairs* is a list of
        ``(token, original_url)`` tuples that the caller must persist as
        ``TrackedLink`` rows before sending the email.
    """
    tracking_base = tracking_base.rstrip("/")
    link_pairs: list[tuple[str, str]] = []

    def _replace(m: re.Match) -> str:
        prefix = m.group(1)
        quote = m.group(2)
        original_url = m.group(3)
        # Don't rewrite URLs already served by this tracking server
        # (e.g. the unsubscribe endpoint /u/<token>)
        if original_url.startswith(tracking_base):
            return m.group(0)
        token = make_tracking_token()
        link_pairs.append((token, original_url))
        return f"{prefix}href={quote}{tracking_base}/c/{token}{quote}"

    if track_clicks:
        new_html = _HREF_RE.sub(_replace, html)
        # Also wrap bare URLs in text nodes that Gmail auto-linkifies but
        # which would otherwise bypass click tracking entirely.
        new_html = _inject_bare_url_tracking(new_html, tracking_base, link_pairs)
    else:
        new_html = html

    if track_opens:
        # Append open-tracking pixel ─────────────────────────────────────────────
        pixel_id = open_token if open_token else str(email_log_id)
        pixel_url = f"{tracking_base}/o/{pixel_id}"
        pixel = (
            f'<img src="{pixel_url}" width="1" height="1" alt="" />'
        )
        lower = new_html.lower()
        idx = lower.rfind("</body>")
        if idx != -1:
            new_html = new_html[:idx] + pixel + "\n" + new_html[idx:]
        else:
            new_html += "\n" + pixel

    return new_html, link_pairs
