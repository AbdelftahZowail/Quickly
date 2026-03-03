# Installation Guide

This guide covers every way to deploy Quickly, from a fresh VPS to integrating with an existing web server.

---

## Table of Contents

- [Option 1: Fresh VPS (Recommended)](#option-1-fresh-vps-recommended)
- [Option 2: Existing Server with nginx](#option-2-existing-server-with-nginx)
- [Option 3: Existing Server — Migrate nginx to Caddy](#option-3-existing-server--migrate-nginx-to-caddy)
- [Option 4: Local Development](#option-4-local-development)
- [Gmail OAuth Setup](#gmail-oauth-setup)
- [Environment Variables](#environment-variables)
- [Updating](#updating)
- [Troubleshooting](#troubleshooting)

---

## Option 1: Fresh VPS (Recommended)

The simplest path. One command gets you a production instance with automatic HTTPS.

### Prerequisites

- A Linux VPS (Ubuntu 22.04+ recommended) or any machine with Docker
- [Docker Engine](https://docs.docker.com/engine/install/) + Docker Compose plugin
- Ports **80** and **443** open
- A domain name with an **A record** pointing to your server's IP

### Steps

```bash
# 1. Create a directory and download the required files
mkdir quickly && cd quickly

curl -LO https://github.com/azowail/quickly/releases/latest/download/docker-compose.yml
curl -LO https://github.com/azowail/quickly/releases/latest/download/Caddyfile
curl -LO https://github.com/azowail/quickly/releases/latest/download/.env.example
mv .env.example .env
```

```bash
# 2. Configure your environment
nano .env
```

Set these values in `.env`:

```env
# Your domain — Caddy auto-fetches a Let's Encrypt cert
CADDY_HOST=yourdomain.com

# Used for OAuth redirects and email links
BASE_URL=https://yourdomain.com

# Google OAuth (required only for Gmail inboxes)
GOOGLE_CLIENT_ID=your-client-id
GOOGLE_CLIENT_SECRET=your-client-secret
```

> `DATABASE_URL` is already set by `docker-compose.yml` to use the bundled PostgreSQL container — you don't need to change it.

```bash
# 3. Start everything
docker compose up -d
```

Caddy obtains a Let's Encrypt certificate automatically on first request. Visit `https://yourdomain.com` — Quickly is running.

### Firewall

Caddy needs ports 80 and 443 for the ACME HTTP-01 challenge:

```bash
# Ubuntu with ufw
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw reload
```

For cloud providers (DigitalOcean, Hetzner, Vultr, AWS), open these ports in your security group / firewall dashboard.

---

## Option 2: Existing Server with nginx

Use this if you already have nginx managing other sites and don't want to change your setup.

> **Note:** Custom tracking domains require Caddy and won't work with this setup. Standard open/click tracking on your app's domain works fine.

### Steps

**1. Modify `docker-compose.yml`:**

Remove the `caddy` service block and its volumes. Change the `app` service to expose port 8000 on localhost:

```yaml
services:
  db:
    image: postgres:15-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: quickly
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s
      timeout: 5s
      retries: 5

  app:
    image: azowail/quickly:latest
    restart: unless-stopped
    ports:
      - "127.0.0.1:8000:8000"
    depends_on:
      db:
        condition: service_healthy
    env_file: .env
    environment:
      DATABASE_URL: "postgresql+asyncpg://postgres:postgres@db:5432/quickly"

volumes:
  pgdata:
```

**2. Add an nginx site configuration:**

```nginx
server {
    listen 80;
    server_name yourdomain.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

**3. Get a certificate (if you don't already have one):**

```bash
sudo certbot --nginx -d yourdomain.com
```

**4. Start:**

```bash
sudo nginx -t && sudo systemctl reload nginx
docker compose up -d
```

---

## Option 3: Existing Server — Migrate nginx to Caddy

If you're open to replacing nginx, Caddy gives you automatic HTTPS for all your sites and enables custom tracking domains in Quickly.

### Steps

**1. Install Caddy on the host:**

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install caddy
```

**2. Convert your nginx sites to Caddyfile format:**

```nginx
# nginx (before)
server {
    listen 80;
    server_name example.com;
    location / { proxy_pass http://localhost:3000; }
}
```

```caddy
# Caddy (after) — HTTPS is automatic
example.com {
    reverse_proxy localhost:3000
}
```

**3. Stop nginx, start Caddy:**

```bash
sudo systemctl stop nginx
sudo systemctl disable nginx
sudo systemctl enable caddy
sudo systemctl start caddy
```

**4. Update `docker-compose.yml`:**

Remove the `caddy` service block from docker-compose.yml (since host Caddy handles everything). Change `expose: ["8000"]` to `ports: ["127.0.0.1:8000:8000"]` on the app service.

**5. Add Quickly to your host Caddyfile:**

```caddy
yourdomain.com {
    reverse_proxy localhost:8000
}
```

```bash
sudo systemctl reload caddy
docker compose up -d
```

---

## Option 4: Local Development

Run Quickly locally without Docker for development and testing.

### Prerequisites

- Python 3.12+
- Node.js 18+
- PostgreSQL 15+ (running locally or in Docker)

### Backend

```bash
cd Quickly

# Create virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux

# Install dependencies
pip install -r requirements.txt

# Set database URL (adjust credentials as needed)
set DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost/quickly    # Windows
# export DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost/quickly  # macOS/Linux

# Start the backend
uvicorn app.main:app --reload
```

The API is now available at `http://localhost:8000`.

### Frontend (hot reload)

```bash
cd frontend
npm install
npm run dev
```

The Vite dev server runs at `http://localhost:5173` and proxies API calls to the backend.

### Running Tests

```bash
# Uses an in-memory SQLite database by default
pytest

# Or run against Postgres for closer-to-production testing
set TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost/test_quickly
pytest
```

---

## Gmail OAuth Setup

Gmail is the only supported email provider. You must complete this step to send emails.

### 1. Create a Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or select an existing one)
3. Enable the **Gmail API** (`APIs & Services → Library → Gmail API`)
4. Enable the **Pub/Sub API** (for real-time inbox sync)

### 2. Create OAuth Credentials

1. Go to `APIs & Services → Credentials`
2. Click **Create Credentials → OAuth client ID**
3. Application type: **Web application**
4. Authorized redirect URI: `https://yourdomain.com/oauth/google/callback`
5. Copy the **Client ID** and **Client Secret** into your `.env`:

```env
GOOGLE_CLIENT_ID=123456789-abc.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-abcdef...
```

### 3. Configure OAuth Consent Screen

1. Go to `APIs & Services → OAuth consent screen`
2. User type: **External** (or Internal for Google Workspace)
3. Add the Gmail scopes:
   - `https://www.googleapis.com/auth/gmail.readonly`
   - `https://www.googleapis.com/auth/gmail.send`
   - `https://www.googleapis.com/auth/gmail.modify`

### 4. Set Up Pub/Sub (for Unibox)

1. Go to `Pub/Sub → Topics → Create Topic`
2. Create a subscription with push delivery to: `https://yourdomain.com/api/unibox/gmail/push`
3. Grant `gmail-api-push@system.gserviceaccount.com` the **Pub/Sub Publisher** role on the topic
4. If your organization has domain-restricted sharing, add `gmail-api-push@system.gserviceaccount.com` to the allowed list

### 5. Connect Gmail Accounts

In the Quickly UI, go to **Inboxes → Add Inbox** and click **Connect Gmail Account**. This starts the OAuth flow and creates the inbox automatically.

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `CADDY_HOST` | For HTTPS | _(empty)_ | Domain name for Caddy auto-HTTPS |
| `BASE_URL` | Yes | — | Full URL with protocol (e.g. `https://yourdomain.com`) |
| `DATABASE_URL` | Auto | Set by compose | PostgreSQL connection string |
| `GOOGLE_CLIENT_ID` | Yes | — | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | Yes | — | Google OAuth client secret |
| `QUICKLY_MODE` | No | `production` | `production` or `development` |

> All other settings (test mode, webhooks, scheduling strategy, etc.) are managed from the web UI **Settings** page and stored in the database.

---

## Updating

```bash
docker compose pull
docker compose up -d
```

Database volumes are preserved across updates. Quickly creates missing columns/tables on startup automatically.

---

## Troubleshooting

### Caddy can't get a certificate

- Confirm your A record: `dig yourdomain.com`
- Confirm ports 80/443 are reachable: `curl http://yourdomain.com` from outside
- Check logs: `docker compose logs caddy`

### App won't start

- Check all services: `docker compose ps`
- Check app logs: `docker compose logs app`
- Verify `.env` exists with correct values

### Database connection errors

- `DATABASE_URL` is set automatically by `docker-compose.yml` — don't override it unless you have a custom Postgres setup
- If overriding, make sure the hostname is `db` (the container), not `localhost`

### Gmail OAuth errors

- Ensure `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` are in your `.env`
- Verify the redirect URI in Google Cloud Console matches your domain exactly
- Check that the Gmail API is enabled in your Google Cloud project

### Running on HTTP only (no domain)

Leave `CADDY_HOST` empty or unset. Caddy will serve on port 80 over plain HTTP:

```bash
docker compose up -d
# Open http://localhost
```
