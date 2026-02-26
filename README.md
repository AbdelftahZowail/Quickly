# Quickly

This project previously used server-rendered HTML templates. A new modern frontend has been added using React and Tailwind CSS. See the **Frontend** section below for details.


Minimal email campaign tool for personal use: leads, campaigns with email sequences, and a smart queue that sends at the right time. No authentication required.

## Features

- **Leads**: Create and enroll leads from a campaign page or the campaign API (`POST /api/campaigns/{id}/leads`). Standalone lead creation (`POST /api/leads`) is not supported. Status tracking: active, unsubscribed, bounced, replied. Enroll leads into campaigns during create or later.
- **Inboxes**: Add sending addresses (email, display name, max emails per day) via the **Inboxes** page or API. Support for Resend, SMTP, or Gmail OAuth. Campaigns can use multiple inboxes; the queue spreads sends across them by capacity.
- **Campaigns**: Multiple sequences per campaign; each campaign has one or more inboxes. Each sequence: subject (optional = reply in thread), body (text/HTML), wait days after previous. Settings: sending days/hours, wait between emails, stop on reply, pause/resume.
- **Queue**: When a lead joins, all sequence slots are reserved immediately. Each slot is assigned to an inbox that has capacity that day (round-robin across campaign inboxes). Business-day math and per-inbox daily limits. Add leads anytime; changing wait days recalculates pending slots.
- **Sending**: A scheduled job (runs inside the same process as the server) sends due emails with template substitution (`{{name}}`, `{{email}}`, `{{company}}`, etc.) and threading (empty subject = reply in same thread).
- **Calendar**: View all sent and scheduled emails across campaigns in one unified timeline with stats. Includes validation checks ("Validate Queue") to detect scheduling issues.

## Quick Start

This project now uses **PostgreSQL only** for its datastore.  Since the
codebase is pre‑release and no real data exists, the SQLite support has been
removed and all old migration code deleted – starting the server will create
a clean Postgres schema.

You need a Postgres server and a database (e.g. `quickly`).  Provide the
connection URI either via the web UI settings after the first launch or by
setting the `DATABASE_URL` environment variable before starting the server.

### Running the test suite

By default the test harness will use an **in‑memory SQLite database**.  This
is controlled via `TEST_DATABASE_URL`, which is automatically set to
`sqlite+aiosqlite:///:memory:?cache=shared` by the `conftest.py` fixture.  You
may override this with a Postgres connection string if you want to exercise the
full backend:

```bash
export TEST_DATABASE_URL="postgresql+asyncpg://user:pass@localhost/test_db"
pytest
```

The suite will create/drop tables on every run, so point it at a disposable
database.  SQLite is convenient for local development; Postgres gives a closer
match to production but is not required.

If you do use SQLite the project still requires `aiosqlite` (listed in
`requirements.txt`) to support the fallback.  Production deployments may omit
that dependency.

```bash
cd "Quickly"
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
# Example env var for local Postgres (replace user/password/host as needed):
set DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost/quickly
uvicorn app.main:app --reload
```


Open http://127.0.0.1:8000 for the web UI. Navigate between pages:
- **Home** (`/`) - Dashboard and status
- **Campaigns** (`/campaigns`) - Manage campaigns and sequences
- **Inboxes** (`/inboxes`) - Configure sending addresses
- **Calendar** (`/calendar`) - View sent and scheduled emails
- **Settings** (`/settings`) - Configure app settings and defaults (individual inboxes determine their own send provider)

**When do emails send?** The send job runs only while the server is running (`uvicorn app.main:app`). By default it runs every 1 minute (configurable in Settings). To confirm it's running: check the server console for log lines like `Send job running at ...` and `Send job finished: N email(s) sent`, or call **GET /api/status** to see `scheduler_running`, `last_send_job_run`, and `next_send_job_run`.

## Configuration

**All settings are managed from the web UI:** Navigate to the **Settings** page (http://127.0.0.1:8000/settings) to configure (including your `database_url` if you didn't set it via environment):

- **General Settings**: Base URL, queue check interval
- **Email Provider**: Choose between Resend, SMTP, or Gmail OAuth  (this value is used as a default; each inbox has its own provider setting)
  - **Resend**: Enter your API key from [resend.com/api-keys](https://resend.com/api-keys)
  - **SMTP**: Configure host, port, username, password, TLS
  - **Gmail OAuth**: Enter Google Client ID and Secret from Google Cloud Console, then link Gmail accounts from the Inboxes page
- **Test Mode**: Enable to preview emails before sending (see Test Mode section below)

Settings are stored in the database and changes take effect immediately (no restart required).

**Environment variables and `.env` files**: the application still reads a handful of
settings from the environment at startup (primarily `DATABASE_URL`). A
`.env` file in the project root will be loaded automatically via
`python-dotenv`, so you can keep your connection string and other initial
values there. On initial startup the values are written to the database;
thereafter the web UI (or the settings API) is the normal way to make
changes. **Test mode is managed from the Settings page and is stored in the
database — environment variables are no longer required.** See
[SETTINGS_MIGRATION.md](SETTINGS_MIGRATION.md) for migration details.

## Test Mode

Enable test mode from the Settings page to safely test email sending without sending to real recipients:

- For **Resend/SMTP**: All emails are redirected to `delivered+{tag}@resend.dev` (tag based on original recipient)
- For **Gmail**: Emails are simulated (not actually sent, but logged as if they were)
- Check status: `GET /api/test/status`

The web UI shows a banner when test mode is enabled.

## Developer utilities

- `add_bulk_leads.py` — CLI helper to create/add many test leads to a campaign via the API. Usage: `python add_bulk_leads.py --campaign-id 2 --count 50` (see file header for full options).
- `populate_test_data.py` — Create sample inboxes, campaigns, sequences and leads for testing. Run with `python populate_test_data.py` (use `--delete` to remove test data).

## API Reference

No authentication required on any endpoint.

### Status
- `GET /api/status` - Scheduler status, last/next send job run, test mode state

### Leads
- `GET /api/leads` - List all leads
- `GET /api/leads/{id}` - Get single lead
- `PATCH /api/leads/{id}` - Update lead
- `DELETE /api/leads/{id}` - Delete lead
- `GET /api/leads/{id}/history` - Email history for lead
- `POST /api/leads/{id}/campaigns/{campaign_id}` - Add lead to campaign
- `POST /api/leads/mark-replied` - Mark lead as replied (body: `{lead_id, campaign_id}`)

- Note: Creating standalone leads via `POST /api/leads` is not supported — use `POST /api/campaigns/{id}/leads` to create and enroll leads.

### Inboxes
- `GET /api/inboxes` - List all inboxes (each entry now includes a `sent_today` count reflecting how many emails have been sent from that inbox so far today).
- `POST /api/inboxes` - Create inbox (body: `{email, display_name?, max_emails_per_day?, provider?}`)
- `GET /api/inboxes/{id}` - Get single inbox
- `PATCH /api/inboxes/{id}` - Update inbox
- `DELETE /api/inboxes/{id}` - Delete inbox

### Campaigns
- `GET /api/campaigns` - List all campaigns (response now includes `stats` object containing totals for leads, emails sent, replies and sequence count)
- `POST /api/campaigns` - Create campaign (body: `{name, inbox_ids, sending_days?, sending_hours_start?, sending_hours_end?, wait_minutes_between?, stop_on_reply?}`)
- `GET /api/campaigns/{id}` - Get single campaign (response includes a `stats` object with aggregated progress info)

The frontend now includes an **Analytics** page (`/analytics`) which uses
`GET /api/campaigns` to display per-campaign progress, reply rates, and will
later surface open/click metrics.  A multi-select dropdown at the top lets
you add campaigns to the view (leave blank to see all); selected names appear
as tags that can be removed.  An area chart above the filter shows progress
percentages for the displayed campaigns, and summary bar graphs show
combined progress and reply rate.  Each row in the table also contains a
mini bar for progress and reply rate for easier visual comparison.
- `PATCH /api/campaigns/{id}` - Update campaign
- `DELETE /api/campaigns/{id}` - Delete campaign
- `POST /api/campaigns/{id}/duplicate` - Duplicate campaign with sequences
- `GET /api/campaigns/{id}/leads` - List leads in campaign with progress
- `POST /api/campaigns/{id}/leads` - Add one or more leads to campaign (body: list of `{email, name?, custom_data?}`); creates missing leads and schedules queue slots
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
- `POST /api/calendar/validate-queue` - Run scheduled-emails validation checks (returns issues list)
- `GET /api/calendar/stats` - Aggregate statistics (sent today, scheduled today, total leads, etc.)

### Settings
Most configuration is now managed via environment variables (see `.env.example`).
The web UI no longer allows editing provider credentials, API keys, SMTP details,
or other sensitive values; those should be defined in your `.env` file or
exported in the environment before starting the server.  Only the
scheduling strategy is adjustable at runtime, and dark/light mode is purely a
client‑side preference.

- `GET /api/settings/scheduling-strategy` – retrieve current strategy
- `POST /api/settings/scheduling-strategy` – update strategy (body `{scheduling_strategy}`)

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
5. Add leads via campaign page or API (POST /api/campaigns/{id}/leads)
6. Assign leads to campaign - slots are automatically reserved across campaign's inboxes
7. Monitor progress in **Calendar** page or via `/api/calendar/sent` and `/api/calendar/scheduled`
8. Check `/api/status` to verify scheduler is running

## Frontend development

A new single‑page application is located in the `frontend/` directory. It was scaffolded with Vite, React 18, React Router and Tailwind CSS. The backend serves the production build as static files so everything can run under one domain (see **Hosting** below).

### Getting started

```bash
cd frontend
npm install          # or yarn
npm run dev          # starts Vite dev server on localhost:5173
```

During development the React app provides the following routes:

- `/` – dashboard with status and quick links
- `/campaigns` – list, reorder, pause/duplicate/delete
- `/campaigns/add` – form to create a campaign
- `/campaigns/:id` – campaign details (sequences, leads, etc.; under construction)
- `/inboxes` – manage sending inboxes with add/edit/delete, including Gmail OAuth flow
- `/unibox` – (placeholder)
- `/calendar` – (placeholder)
- `/settings` – (placeholder)

As you build new functionality you can add new React components under `frontend/src/pages`.

During development the app proxies `/api` requests to `http://localhost:8000`, so you can run the FastAPI server side‑by‑side.

### Building for production

```bash
cd frontend
npm run build        # outputs static files to frontend/dist
```

Copy or serve the contents of `frontend/dist` from the backend's `static/` directory (see hosting instructions).

### Hosting (single domain)

1. **Build the frontend** as shown above.
2. **Move the build**: either copy `frontend/dist/*` into `static/` at the project root or configure the backend to mount `frontend/dist` directly:

   ```python
   # app/main.py
   from fastapi.staticfiles import StaticFiles
   BASE_DIR = Path(__file__).resolve().parent.parent
   app.mount("/", StaticFiles(directory=str(BASE_DIR / "frontend/dist"), html=True), name="frontend")
   ```

   the `html=True` flag makes FastAPI return `index.html` for unknown routes (SPA fallback). Remove or update the existing template routes if you migrate fully to React.

3. **Run the backend**: `uvicorn app.main:app --host 0.0.0.0 --port 8000` (or via Gunicorn/Uvicorn for production). All UI traffic and API traffic share the same origin (`https://yourdomain.com`).

4. **Reverse proxy (optional)**: In production you can put Nginx, Caddy, or Cloudflare Tunnel in front of the FastAPI process. Configure a catch‑all that forwards `/api` to the FastAPI app and serves static files for all other requests. Example Nginx snippet:

   ```nginx
   server {
       listen 80;
       server_name example.com;

       location /api/ {
           proxy_pass http://127.0.0.1:8000/api/;
           proxy_set_header Host $host;
       }

       location / {
           root /path/to/project/frontend/dist;
           try_files $uri $uri/ /index.html;
       }
   }
   ```

5. **Single domain benefit**: cookies, OAuth callbacks and relative links all work without cross‑origin complications.

You can continue serving the legacy Jinja templates during the migration by keeping both `app.mount("/static", ...)` and the template routes, or you may delete them once the React UI covers all pages.

## Template Variables

Use these in email subject and body:
- `{{name}}` - Lead's name
- `{{email}}` - Lead's email
- `{{company}}` - From lead's custom_data
- `{{title}}` - From lead's custom_data
- Any other key from lead's `custom_data` object.
