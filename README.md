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
# Optional: copy .env.example to .env and set SMTP_* and others
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000 for the web UI.

**When do emails send?** The send job runs only while the server is running (`uvicorn app.main:app`). By default it runs every 1 minute (set `QUEUE_CHECK_INTERVAL_MINUTES` in `.env`). To confirm it’s running: check the server console for log lines like `Send job running at ...` and `Send job finished: N email(s) sent`, or call **GET /api/status** to see `scheduler_running`, `last_send_job_run`, and `next_send_job_run`.

## API (no auth)

- **Inboxes**: `GET/POST /api/inboxes`, `GET /api/inboxes/{id}`
- **Leads**: `GET/POST /api/leads`, `GET/PATCH /api/leads/{id}`, `GET /api/leads/{id}/history`, `POST /api/leads/{id}/campaigns/{campaign_id}`, `POST /api/leads/mark-replied` (body: `{"lead_id", "campaign_id"}`)
- **Status**: `GET /api/status` — scheduler running, last/next send job run, interval.
- **Campaigns**: `GET/POST /api/campaigns`, `GET/PATCH /api/campaigns/{id}`, `GET/POST /api/campaigns/{id}/sequences`, `PATCH /api/campaigns/{id}/sequences/{seq_id}`, `GET /api/campaigns/{id}/leads`, `GET /api/campaigns/{id}/queue`, `POST /api/campaigns/{id}/leads/{lead_id}`

Use the **Inboxes** page to add sending addresses, then create a campaign and select one or more inboxes. Add sequences (position 0, 1, 2… with subject/body/wait_days_after_previous), then add leads and assign to campaigns; slots are reserved automatically across the campaign’s inboxes. If you had an existing database from before multi-inbox support, delete `campaign.db` so tables are recreated with the new schema.

## Config

Copy `.env.example` to `.env` and set:

- **Email (Resend, default):** `EMAIL_PROVIDER=resend`, `RESEND_API_KEY=re_xxx` — get a key at [resend.com/api-keys](https://resend.com/api-keys). Sending uses the Resend API; threading (In-Reply-To/References) is supported.
- **Email (SMTP):** `EMAIL_PROVIDER=smtp`, then `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_USE_TLS`.
- **Other:** `DATABASE_URL`, `QUEUE_CHECK_INTERVAL_MINUTES`, `TEST_MODE`.

**Test mode** (`TEST_MODE=true`): The send job does not send emails; it moves due emails into a pending-approval queue. The frontend shows the queue in the browser console and a banner. Approve from the **backend Python console**: `python -m app.approval --list` to list pending, `python -m app.approval` to send all, `python -m app.approval <id>` to send one by id.
