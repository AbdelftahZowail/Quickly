"""
add_bulk_leads.py

Small utility (run from project root) to test the "bulk add leads to campaign"
API endpoint. Behaves like `populate_test_data.py` but uses the HTTP API
POST /api/campaigns/{campaign_id}/leads and lets you choose a campaign and
how many test leads to create and add.

Usage examples:
  python add_bulk_leads.py               # interactive campaign selection, 20 leads
  python add_bulk_leads.py --campaign-id 2 --count 50
  python add_bulk_leads.py --count 100 --prefix test_lead --domain example.com

Notes:
- Defaults to the application's configured base URL (`app.settings_manager.settings.base_url`). Override with `--base-url`.
- Uses `requests` if installed; otherwise falls back to the Python stdlib.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
from datetime import datetime
from typing import List, Dict, Any
try:
    from app.settings_manager import settings
except Exception:
    # Allow running this helper without the full app/deps installed.
    settings = None

# Try to use `requests` if available (convenience); otherwise use urllib
try:
    import requests  # type: ignore
    _HAS_REQUESTS = True
except Exception:
    import urllib.request
    _HAS_REQUESTS = False

DEFAULT_BASE = settings.base_url if settings and getattr(settings, "base_url", None) else "http://127.0.0.1:8000"
TEST_PREFIX = "test_lead"


def http_get_json(url: str) -> Any:
    if _HAS_REQUESTS:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        return r.json()
    else:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.load(resp)


def http_post_json(url: str, data: Any) -> Any:
    payload = json.dumps(data).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if _HAS_REQUESTS:
        r = requests.post(url, json=data, timeout=30)
        r.raise_for_status()
        return r.json()
    else:
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)


def http_delete(url: str) -> Any:
    """HTTP DELETE helper (requests if available, otherwise urllib).
    Returns parsed JSON when present, otherwise None."""
    if _HAS_REQUESTS:
        r = requests.delete(url, timeout=10)
        r.raise_for_status()
        try:
            return r.json()
        except Exception:
            return None
    else:
        req = urllib.request.Request(url, method="DELETE")
        with urllib.request.urlopen(req, timeout=10) as resp:
            try:
                return json.load(resp)
            except Exception:
                return None


def choose_campaign(base_url: str, campaign_id: int | None) -> int:
    campaigns = http_get_json(f"{base_url.rstrip('/')}/api/campaigns")
    if not campaigns:
        print("No campaigns found on the server. Create a campaign first.")
        sys.exit(1)

    if campaign_id:
        # validate provided id exists
        for c in campaigns:
            if c.get("id") == campaign_id:
                return campaign_id
        print(f"Campaign id {campaign_id} not found on server.")
        sys.exit(1)

    # Interactive selection
    print("Available campaigns:")
    for idx, c in enumerate(campaigns, start=1):
        print(f"  {idx}) id={c['id']} name={c['name']}")

    while True:
        choice = input("Choose a campaign by number or id (q to quit): ").strip()
        if not choice:
            continue
        if choice.lower() in ("q", "quit", "exit"):
            sys.exit(0)
        # accept either numeric index or campaign id
        if choice.isdigit():
            n = int(choice)
            # if it's an index in the printed list
            if 1 <= n <= len(campaigns):
                return campaigns[n - 1]["id"]
            # otherwise treat as id
            for c in campaigns:
                if c.get("id") == n:
                    return n
        print("Invalid choice, try again.")


def determine_start_index(base_url: str, prefix: str) -> int:
    """Find highest existing email index for this prefix and return next one.
    Looks for emails that match prefix_#### or prefix#### patterns and returns
    max + 1. If none found, returns 1."""
    try:
        leads = http_get_json(f"{base_url.rstrip('/')}/api/leads")
    except Exception:
        # If the server isn't available or /api/leads fails, just start at 1
        return 1

    pattern = re.compile(re.escape(prefix) + r"[_-]?(\d+)")
    max_i = 0
    for L in leads:
        m = pattern.search(L.get("email", ""))
        if m:
            try:
                val = int(m.group(1))
                if val > max_i:
                    max_i = val
            except Exception:
                pass
    return max_i + 1


def make_leads(prefix: str, domain: str, start: int, count: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for i in range(start, start + count):
        email = f"{prefix}_{i:03d}@{domain}"
        out.append({"email": email, "name": f"Test User {i}", "custom_data": {}})
    return out


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Add bulk test leads to a campaign via API")
    p.add_argument("--base-url", default=DEFAULT_BASE, help="API base URL (default: %(default)s)")
    p.add_argument("--campaign-id", type=int, help="Campaign id to add leads to (if omitted you'll be prompted)")
    p.add_argument("--count", type=int, default=20, help="How many leads to add (default: 20)")
    p.add_argument("--prefix", default=TEST_PREFIX, help="Email prefix for test leads (default: %(default)s)")
    p.add_argument("--domain", default="example.com", help="Email domain for test leads (default: %(default)s)")
    p.add_argument("--start", type=int, help="Start index for numbering (auto-detected by default)")
    p.add_argument("--dry-run", action="store_true", help="Show what would be sent but don't call API")
    p.add_argument("--save-file", help="If provided, save the list of created emails to this file")
    p.add_argument("--delete", action="store_true", help="Delete leads matching the prefix/domain (destructive). If used with --campaign-id only deletes leads enrolled in that campaign.")
    p.add_argument("-y", "--yes", action="store_true", help="Skip confirmation for destructive operations")

    args = p.parse_args(argv)
    base_url = args.base_url.rstrip("/")

    # ---- Deletion flow (destructive) ----
    if args.delete:
        prefix = args.prefix
        domain = args.domain
        pattern = re.compile(rf"^{re.escape(prefix)}[_-]?\d+@{re.escape(domain)}$", re.IGNORECASE)

        if args.campaign_id:
            campaign_id = choose_campaign(base_url, args.campaign_id)  # validates
            leads_data = http_get_json(f"{base_url}/api/campaigns/{campaign_id}/leads")
        else:
            leads_data = http_get_json(f"{base_url}/api/leads")

        matching = [l for l in leads_data if pattern.match(l.get("email", ""))]
        if not matching:
            print("No matching leads found to delete.")
            return 0

        print(f"Found {len(matching)} leads matching prefix '{prefix}' and domain '{domain}'.")
        if not args.yes:
            confirm = input(f"Type 'yes' to delete these {len(matching)} leads: ").strip().lower()
            if confirm != "yes":
                print("Aborted.")
                return 0

        deleted = 0
        failures: list[tuple[int, str, str]] = []
        for l in matching:
            lead_id = l.get("id")
            email = l.get("email")
            try:
                http_delete(f"{base_url}/api/leads/{lead_id}")
                print(f"Deleted lead {email} (id={lead_id})")
                deleted += 1
            except Exception as exc:
                print(f"Failed to delete lead {email} (id={lead_id}): {exc}")
                failures.append((lead_id, email, str(exc)))

        print(f"Deleted {deleted}/{len(matching)} leads; {len(failures)} failures.")
        return 0

    # ---- Add leads flow ----
    campaign_id = choose_campaign(base_url, args.campaign_id)

    start = args.start or determine_start_index(base_url, args.prefix)
    leads = make_leads(args.prefix, args.domain, start, args.count)

    print(f"Will add {len(leads)} leads to campaign id={campaign_id} (start={start})")
    if args.dry_run:
        print(json.dumps(leads, indent=2)[:2000])
        return 0

    try:
        url = f"{base_url}/api/campaigns/{campaign_id}/leads"
        resp = http_post_json(url, leads)
    except Exception as exc:
        print(f"Error calling API: {exc}")
        return 2

    # Print summary
    print("\nAPI response:")
    print(json.dumps(resp, indent=2))

    created_emails = [r.get("email") for r in resp.get("results", []) if r.get("status") == "added"]
    if args.save_file and created_emails:
        try:
            with open(args.save_file, "w", encoding="utf-8") as fh:
                for e in created_emails:
                    fh.write(e + "\n")
            print(f"Saved {len(created_emails)} created emails to {args.save_file}")
        except Exception as exc:
            print(f"Failed to save file: {exc}")

    print(f"Added: {resp.get('added', 0)}, already_enrolled: {resp.get('already_enrolled', 0)}, errors: {resp.get('errors', 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
