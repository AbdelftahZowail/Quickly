# Quickly

<p>
  <img src="https://img.shields.io/badge/Self--Hosted-100%25-teal" alt="Self-Hosted" />
  <img src="https://img.shields.io/badge/License-MIT-blue" alt="License" />
  <img src="https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white" alt="Docker" />
</p>

**Cold email infrastructure you own.** Self-hosted email campaign platform with multi-step sequences, a smart queue, Gmail OAuth sending, open/click/bounce tracking, webhooks, and a unified inbox  all in one `docker compose up`.

---

## Features

- **Multi-step sequences**  Build campaigns with any number of follow-up emails, configurable wait days, and automatic threading.
- **Smart queue**  Emails are scheduled across your inboxes respecting daily limits, business days, sending windows, and per-inbox cooldowns.
- **Multiple inboxes**  Spread sends across as many Gmail accounts as you want. Add more accounts at any time.
- **Open & click tracking**  Built-in pixel tracking and link wrapping with optional custom tracking domains (automatic HTTPS via Caddy).
- **Bounce detection**  Permanent send failures (bounced, invalid address, auth errors) are detected automatically. Affected leads are marked and further sends stop.
- **Unified inbox (Unibox)**  Real-time Gmail sync with lead-reply detection, threaded view, and compose/reply inside the app.
- **Analytics**  Aggregated metrics, per-campaign progress, timeline charts, open/click/reply rates.
- **Priority scheduling**  Drag campaigns to set send priority. Choose between priority-first or round-robin strategies.
- **Webhooks**  Subscribe multiple endpoints to any of 10 event types: `email.sent`, `email.opened`, `email.clicked`, `email.bounced`, `lead.replied`, `lead.unsubscribed`, `lead.status_changed`, `daily_limit`, `rate_limit`, `token_expired`.
- **Test mode**  Simulate sends without delivering real emails. Validate templates safely.
- **Full REST API**  Every UI action has an equivalent API endpoint.
- **Dark mode**  Because of course.

---

## Quick Start

### Option 1  Self-hosted (VPS + Caddy, recommended)

```bash
mkdir quickly && cd quickly

curl -LO https://github.com/azowail/quickly/releases/latest/download/docker-compose.yml
curl -LO https://github.com/azowail/quickly/releases/latest/download/Caddyfile
curl -LO https://github.com/azowail/quickly/releases/latest/download/.env.example
mv .env.example .env
```

Edit `.env`  at minimum set:

```env
CADDY_HOST=yourdomain.com
BASE_URL=https://yourdomain.com
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
```

```bash
docker compose up -d
```

Caddy obtains a Let's Encrypt certificate automatically. Open `https://yourdomain.com`.

### Option 2  PaaS (Railway, Render, Fly.io, etc.)

1. Create a new service and deploy image `azowail/quickly:latest` from Docker Hub.
2. Add a managed PostgreSQL database and copy the connection string to `DATABASE_URL`.
3. Set the environment variables from `.env.example` in your platform's dashboard.
4. Done  you get a public HTTPS URL automatically.

### Option 3  Local development

```bash
git clone https://github.com/azowail/quickly.git
cd quickly

# Backend
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux
pip install -r requirements.txt

set DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost/quickly
set GOOGLE_CLIENT_ID=...
set GOOGLE_CLIENT_SECRET=...
set BASE_URL=http://localhost:8000
uvicorn app.main:app --reload

# Frontend (separate terminal)
cd frontend
npm install
npm run dev   # Vite on localhost:5173, proxies /api to localhost:8000
```

Or use the dev compose stack:

```bash
docker compose -f docker-compose.dev.yml up
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | Auto (docker-compose sets it) | PostgreSQL connection string |
| `BASE_URL` | Yes | Full URL including protocol (e.g. `https://yourdomain.com`) |
| `GOOGLE_CLIENT_ID` | Yes | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | Yes | Google OAuth client secret |
| `CADDY_HOST` | For HTTPS | Domain name for Caddy's auto-TLS (e.g. `yourdomain.com`) |
| `QUICKLY_MODE` | No | `production` (default in Docker) or `development` |

See `.env.example` for the full list with comments.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | React 18, Vite, Tailwind CSS |
| Backend | Python 3.12, FastAPI, SQLAlchemy (async) |
| Database | PostgreSQL 15 |
| Email | Gmail API (OAuth2) |
| Deployment | Docker, Caddy (auto HTTPS) |
| Scheduling | APScheduler (in-process) |

---

## Template Variables

Use these in email subject and body:

| Variable | Source |
|---|---|
| `{{name}}` | Lead name |
| `{{email}}` | Lead email |
| `{{company}}` | Lead `custom_data.company` |
| `{{title}}` | Lead `custom_data.title` |
| Any other key | Lead `custom_data.*` |

---

## Running Tests

```bash
# Fast  in-memory SQLite (default)
pytest

# Full  PostgreSQL
set TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost/test_quickly
pytest
```

---

## Documentation

| Document | Description |
|---|---|
| [docs/INSTALL.md](docs/INSTALL.md) | Full installation & deployment guide |
| [docs/API.md](docs/API.md) | Complete API reference |
| [docs/WEBHOOKS.md](docs/WEBHOOKS.md) | Webhook events, payloads & examples |
| [docs/CONTRIBUTORS.md](docs/CONTRIBUTORS.md) | Contributing guidelines |

---

## License

MIT
