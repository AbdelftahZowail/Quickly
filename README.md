# Campaign Engine

Minimal email campaign tool for personal use: leads, campaigns with email sequences, and a smart queue that sends at the right time. No login.

## Features

- **Leads**: Add via API (email, name, custom data). Status: active, unsubscribed, bounced, replied. Assign to campaigns on create or later.
- **Inboxes**: Add sending addresses (email, display name, max emails per day) via the **Inboxes** page or API. A campaign can use one or more inboxes; the queue spreads sends across them by capacity.
- **Campaigns**: Multiple sequences per campaign; each campaign has one or more inboxes. Each sequence: subject (optional = reply in thread), body (text/HTML), wait days after previous. Settings: sending days/hours, wait between emails, stop on reply.
- **Queue**: When a lead joins, all sequence slots are reserved immediately. Each slot is assigned to an inbox that has capacity that day (round-robin across campaign inboxes). Business-day math and per-inbox daily limits. Add leads anytime; changing wait days recalculates pending slots.
- **Sending**: A scheduled job (runs inside the same process as the server) sends due emails with template substitution (`{{name}}`, `{{email}}`, `{{company}}`, etc.) and threading (empty subject = reply in same thread).

## Quick start

```bash
cd "Campaign Engine 2"
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000 for the web UI. Configure all settings from the **Settings** page in the navigation menu.

**When do emails send?** The send job runs only while the server is running (`uvicorn app.main:app`). By default it runs every 1 minute (configurable in Settings). To confirm it's running: check the server console for log lines like `Send job running at ...` and `Send job finished: N email(s) sent`, or call **GET /api/status** to see `scheduler_running`, `last_send_job_run`, and `next_send_job_run`.

## API (no auth)

- **Inboxes**: `GET/POST /api/inboxes`, `GET /api/inboxes/{id}`
- **Leads**: `GET/POST /api/leads`, `GET/PATCH /api/leads/{id}`, `GET /api/leads/{id}/history`, `POST /api/leads/{id}/campaigns/{campaign_id}`, `POST /api/leads/mark-replied` (body: `{"lead_id", "campaign_id"}`)
- **Status**: `GET /api/status` — scheduler running, last/next send job run, interval.
- **Campaigns**: `GET/POST /api/campaigns`, `GET/PATCH /api/campaigns/{id}`, `GET/POST /api/campaigns/{id}/sequences`, `PATCH /api/campaigns/{id}/sequences/{seq_id}`, `GET /api/campaigns/{id}/leads`, `GET /api/campaigns/{id}/queue`, `POST /api/campaigns/{id}/leads/{lead_id}`

Use the **Inboxes** page to add sending addresses, then create a campaign and select one or more inboxes. Add sequences (position 0, 1, 2… with subject/body/wait_days_after_previous), then add leads and assign to campaigns; slots are reserved automatically across the campaign's inboxes.

## Configuration

**All settings are now managed from the web UI:** Navigate to the **Settings** page (http://127.0.0.1:8000/settings) to configure:

- **General Settings:** Base URL, queue check interval, test mode
- **Email Provider:** Choose between Resend, SMTP, or Gmail OAuth
  - **Resend:** Enter your API key from [resend.com/api-keys](https://resend.com/api-keys)
  - **SMTP:** Configure host, port, username, password, TLS
  - **Gmail OAuth:** Enter Google Client ID and Secret from Google Cloud Console

Settings are stored in the database and changes take effect immediately (no restart required).

**Legacy `.env` file:** No longer used. The old environment variable approach has been replaced with database storage for easier management. See [SETTINGS_MIGRATION.md](SETTINGS_MIGRATION.md) for migration details.

**Test mode**: Enable from the Settings page. The send job will not send emails; it moves due emails into a pending-approval queue. The frontend shows the queue and a banner. Approve from the **backend Python console**: `python -m app.approval --list` to list pending, `python -m app.approval` to send all, `python -m app.approval <id>` to send one by id.
