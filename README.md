# Campaign Engine

Minimal email campaign tool for personal use: leads, campaigns with email sequences, and a smart queue that sends at the right time. No authentication required.

## Features

- **Leads**: Add via web UI or API (email, name, custom data). Status tracking: active, unsubscribed, bounced, replied. Assign to campaigns on create or later.
- **Inboxes**: Add sending addresses (email, display name, max emails per day) via the **Inboxes** page or API. Support for Resend, SMTP, or Gmail OAuth. Campaigns can use multiple inboxes; the queue spreads sends across them by capacity.
- **Campaigns**: Multiple sequences per campaign; each campaign has one or more inboxes. Each sequence: subject (optional = reply in thread), body (text/HTML), wait days after previous. Settings: sending days/hours, wait between emails, stop on reply, pause/resume.
- **Queue**: When a lead joins, all sequence slots are reserved immediately. Each slot is assigned to an inbox that has capacity that day (round-robin across campaign inboxes). Business-day math and per-inbox daily limits. Add leads anytime; changing wait days recalculates pending slots.
- **Sending**: A scheduled job (runs inside the same process as the server) sends due emails with template substitution (`{{name}}`, `{{email}}`, `{{company}}`, etc.) and threading (empty subject = reply in same thread).
- **Calendar**: View all sent and scheduled emails across campaigns in one unified timeline with stats.

## Quick Start

```bash
cd "Campaign Engine 2"
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000 for the web UI. Navigate between pages:
- **Home** (`/`) - Dashboard and status
- **Campaigns** (`/campaigns`) - Manage campaigns and sequences
- **Leads** (`/leads`) - View and manage leads
- **Inboxes** (`/inboxes`) - Configure sending addresses
- **Calendar** (`/calendar`) - View sent and scheduled emails
- **Settings** (`/settings`) - Configure email provider and app settings

**When do emails send?** The send job runs only while the server is running (`uvicorn app.main:app`). By default it runs every 1 minute (configurable in Settings). To confirm it's running: check the server console for log lines like `Send job running at ...` and `Send job finished: N email(s) sent`, or call **GET /api/status** to see `scheduler_running`, `last_send_job_run`, and `next_send_job_run`.

## Configuration

**All settings are managed from the web UI:** Navigate to the **Settings** page (http://127.0.0.1:8000/settings) to configure:

- **General Settings**: Base URL, queue check interval
- **Email Provider**: Choose between Resend, SMTP, or Gmail OAuth
  - **Resend**: Enter your API key from [resend.com/api-keys](https://resend.com/api-keys)
  - **SMTP**: Configure host, port, username, password, TLS
  - **Gmail OAuth**: Enter Google Client ID and Secret from Google Cloud Console, then link Gmail accounts from the Inboxes page
- **Test Mode**: Enable to preview emails before sending (see Test Mode section below)

Settings are stored in the database and changes take effect immediately (no restart required).

**Legacy `.env` file**: No longer used. The old environment variable approach has been replaced with database storage for easier management. See [SETTINGS_MIGRATION.md](SETTINGS_MIGRATION.md) for migration details.

## Test Mode

Enable test mode from the Settings page to safely test email sending without sending to real recipients:

- For **Resend/SMTP**: All emails are redirected to `delivered+{tag}@resend.dev` (tag based on original recipient)
- For **Gmail**: Emails are simulated (not actually sent, but logged as if they were)
- Check status: `GET /api/test/status`

The web UI shows a banner when test mode is enabled.

## API Reference

No authentication required on any endpoint.

### Status
- `GET /api/status` - Scheduler status, last/next send job run, test mode state

### Leads
- `GET /api/leads` - List all leads
- `POST /api/leads` - Create lead (body: `{email, name?, custom_data?}`)
- `GET /api/leads/{id}` - Get single lead
- `PATCH /api/leads/{id}` - Update lead
- `DELETE /api/leads/{id}` - Delete lead
- `GET /api/leads/{id}/history` - Email history for lead
- `POST /api/leads/{id}/campaigns/{campaign_id}` - Add lead to campaign
- `POST /api/leads/mark-replied` - Mark lead as replied (body: `{lead_id, campaign_id}`)

### Inboxes
- `GET /api/inboxes` - List all inboxes
- `POST /api/inboxes` - Create inbox (body: `{email, display_name?, max_emails_per_day?, provider?}`)
- `GET /api/inboxes/{id}` - Get single inbox
- `PATCH /api/inboxes/{id}` - Update inbox
- `DELETE /api/inboxes/{id}` - Delete inbox

### Campaigns
- `GET /api/campaigns` - List all campaigns
- `POST /api/campaigns` - Create campaign (body: `{name, inbox_ids, sending_days?, sending_hours_start?, sending_hours_end?, wait_minutes_between?, stop_on_reply?}`)
- `GET /api/campaigns/{id}` - Get single campaign
- `PATCH /api/campaigns/{id}` - Update campaign
- `DELETE /api/campaigns/{id}` - Delete campaign
- `POST /api/campaigns/{id}/duplicate` - Duplicate campaign with sequences
- `GET /api/campaigns/{id}/leads` - List leads in campaign with progress
- `POST /api/campaigns/{id}/leads/{lead_id}` - Add lead to campaign
- `DELETE /api/campaigns/{id}/leads/{lead_id}` - Remove lead from campaign
- `GET /api/campaigns/{id}/queue` - View scheduled queue for campaign
- `POST /api/campaigns/{id}/recalculate-queue` - Recalculate queue after sequence changes
- `GET /api/campaigns/{id}/sent` - View sent emails for campaign

### Sequences
- `GET /api/campaigns/{id}/sequences` - List sequences for campaign
- `POST /api/campaigns/{id}/sequences` - Create sequence (body: `{position, subject?, body, wait_days_after_previous}`)
- `PATCH /api/campaigns/{id}/sequences/{seq_id}` - Update sequence
- `DELETE /api/campaigns/{id}/sequences/{seq_id}` - Delete sequence

### Calendar
- `GET /api/calendar/sent` - All sent emails across campaigns
- `GET /api/calendar/scheduled` - All scheduled emails across campaigns
- `GET /api/calendar/stats` - Aggregate statistics (sent today, scheduled today, total leads, etc.)

### Settings
- `GET /api/settings` - Get all settings
- `PUT /api/settings` - Update settings (body: full settings object)
- `GET /api/settings/test-mode` - Get test mode status
- `POST /api/settings/test-mode` - Update test mode (body: `{enabled: boolean}`)
- `GET /api/settings/google-oauth` - Get Google OAuth credentials
- `POST /api/settings/google-oauth` - Update Google OAuth credentials (body: `{client_id, client_secret}`)

### Gmail OAuth
- `GET /api/gmail/status` - Gmail OAuth configuration status
- `GET /api/gmail/accounts` - List connected Gmail accounts
- `GET /api/gmail/permissions` - Check Gmail API permissions
- `GET /oauth/google/authorize?inbox_id={id}` - Start OAuth flow for inbox
- `GET /oauth/google/callback` - OAuth callback (redirect)
- `DELETE /api/gmail/accounts/{id}` - Disconnect Gmail account

### Test Mode
- `GET /api/test/status` - Check if test mode is enabled

## Workflow Example

1. Configure email provider in **Settings** page
2. Add sending addresses in **Inboxes** page (or via `POST /api/inboxes`)
3. Create campaign in **Campaigns** page, select inboxes
4. Add sequences to campaign (position 0, 1, 2... with subject, body, wait days)
5. Add leads via **Leads** page or API
6. Assign leads to campaign - slots are automatically reserved across campaign's inboxes
7. Monitor progress in **Calendar** page or via `/api/calendar/sent` and `/api/calendar/scheduled`
8. Check `/api/status` to verify scheduler is running

## Template Variables

Use these in email subject and body:
- `{{name}}` - Lead's name
- `{{email}}` - Lead's email
- `{{company}}` - From lead's custom_data
- `{{title}}` - From lead's custom_data
- Any other key from lead's `custom_data` object
