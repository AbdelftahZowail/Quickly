"""Email provider detection via DNS MX record lookups.

Detects the hosting provider for an email address by querying MX records.
Used to support provider-matched sending: Google leads → Gmail inboxes,
Office 365 leads → Office 365 inboxes.
"""
import asyncio
import logging
from typing import Optional

log = logging.getLogger("quickly.email_provider")

# Maps MX hostname patterns to a human-readable provider name.
# Patterns are matched case-insensitively as substrings of the MX hostname.
PROVIDER_PATTERNS: dict[str, list[str]] = {
    "Google Workspace": ["google.com", "googlemail.com"],
    "Office 365": ["mail.protection.outlook.com", "outlook.com"],
    "ProtonMail": ["protonmail.ch", "proton.me"],
    "Zoho Mail": ["zoho.com", "zohomail.com"],
    "Yahoo Mail": ["yahoo.com", "yahoodns.net"],
    "Mimecast": ["mimecast.com"],
    "Proofpoint": ["pphosted.com", "proofpoint.com"],
    "Barracuda": ["barracudanetworks.com"],
    "Fastmail": ["fastmail.com", "fastmailbox.net"],
    "Mandrill": ["mandrill.com"],
    "SendGrid": ["sendgrid.net"],
    "Amazon SES": ["amazonses.com"],
    "iCloud": ["icloud.com", "apple.com"],
    "Rackspace": ["emailsrvr.com"],
    "GoDaddy": ["secureserver.net"],
    "OVH": ["ovh.net", "ovhcloud.com"],
    "Namecheap": ["privateemail.com"],
}

# Maps a lead's detected provider name → the Inbox.provider slug it matches.
# Providers not in this mapping have no preferred inbox type (any inbox is fine).
LEAD_PROVIDER_TO_INBOX_PROVIDER: dict[str, str] = {
    "Google Workspace": "gmail",
    "Office 365": "office365",
}


def detect_provider_from_mx(mx_hostnames: list[str]) -> str:
    """Detect provider from a list of MX hostname strings (already resolved).

    Returns the provider name string, or ``"Unknown"`` if no pattern matches.
    """
    for hostname in mx_hostnames:
        h = hostname.rstrip(".").lower()
        for provider, patterns in PROVIDER_PATTERNS.items():
            if any(p in h for p in patterns):
                return provider
    return "Unknown"


def _sync_detect_provider(email: str) -> Optional[str]:
    """Blocking DNS MX lookup + provider detection. Intended for thread pool use."""
    try:
        import dns.resolver  # type: ignore[import]

        if "@" not in email:
            return None

        domain = email.split("@")[-1].strip().lower()
        answers = dns.resolver.resolve(domain, "MX")
        mx_records = sorted(answers, key=lambda r: r.preference)
        mx_hostnames = [str(r.exchange).rstrip(".") for r in mx_records]
        return detect_provider_from_mx(mx_hostnames)
    except Exception as exc:
        log.debug("Provider detection failed for %s: %s", email, exc)
        return None


async def detect_provider_for_email(email: str) -> Optional[str]:
    """Async wrapper – runs the blocking DNS lookup in a thread pool.

    Returns the provider name (e.g. ``"Google Workspace"``, ``"Office 365"``)
    or ``None`` if the lookup fails or the domain has no recognisable MX.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _sync_detect_provider, email)


def get_inbox_provider_for_lead(lead_provider: Optional[str]) -> Optional[str]:
    """Return the inbox.provider slug required for this lead, or None if any inbox is acceptable."""
    if not lead_provider:
        return None
    return LEAD_PROVIDER_TO_INBOX_PROVIDER.get(lead_provider)
