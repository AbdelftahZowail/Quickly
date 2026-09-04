# Quickly — Installation Guide

> **How to use this guide:** Start at [Step 1](#step-1-choose-your-deployment-path) and follow the path that matches your situation. Every path leads back to the shared steps at the end.

---

## Table of Contents

- [Step 1: Choose Your Deployment Path](#step-1-choose-your-deployment-path)
    - [Option A: Railway / PaaS (No server needed)](#option-a-railway--paas-no-server-needed)
    - [Option B: VPS with Docker Compose](#option-b-vps-with-docker-compose)
    - [Beacon-only (recommended): `docker-compose.no-caddy.yml`](#beacon-only-docker-compose-no-caddy)
    - [Already running Caddy on the host (not-host)](#already-running-caddy-on-the-host-not-host)
    - [Option C: Existing Server with nginx](#option-c-existing-server-with-nginx)
    - [Option D: Local Development](#option-d-local-development)
- [Step 2: First Login & User Setup](#step-2-first-login--user-setup)
- [Step 3A: Connect Gmail Inboxes](#step-3a-connect-gmail-inboxes)
- [Step 3B: Connect Office 365 / Outlook Inboxes](#step-3b-connect-office-365--outlook-inboxes)
- [Step 3C: Connect Amazon SES (via SMTP)](#step-3c-connect-amazon-ses-via-smtp)
- [Step 4: Create Your First Campaign](#step-4-create-your-first-campaign)
- [Optional: AI Reply Classification](#optional-ai-reply-classification)
- [Quickly Beacon (recommended custom tracking hostnames)](#quickly-beacon-recommended-custom-tracking-hostnames)
- [Optional: Advanced — CNAME custom domains to Quickly](#optional-advanced--cname-custom-domains-to-quickly)
- [Updating Quickly](#updating-quickly)
- [Environment Variables Reference](#environment-variables-reference)
- [Troubleshooting](#troubleshooting)

---

## Step 1: Choose Your Deployment Path

|Path|Best for|Difficulty|
|---|---|---|
|[**A — Railway / PaaS**](#option-a-railway--paas-no-server-needed)|Fastest setup, no DevOps experience needed|⭐ Easiest|
|[**B — VPS + Docker Compose**](#option-b-vps-with-docker-compose)|Your own server: Postgres + app; **recommended** [`docker-compose.no-caddy.yml`](#beacon-only-docker-compose-no-caddy) if you use **only Beacon** for tracking, or all-in-one with Caddy|⭐⭐ Easy|
|[**C — Existing server with nginx**](#option-c-existing-server-with-nginx)|You already have other sites on the same server|⭐⭐⭐ Medium|
|[**D — Local development**](#option-d-local-development)|Contributing, testing, or hacking on the code|⭐⭐ Easy|

**Custom tracking links on your own domain:** use [**Quickly Beacon**](#quickly-beacon-recommended-custom-tracking-hostnames) (recommended for the prebuilt image and Railway). The inbox UI hides the legacy “CNAME to Quickly” flow on those deployments; advanced single-VPS setups with **host Caddy** can still use [CNAME + on-demand TLS](#optional-advanced--cname-custom-domains-to-quickly) if you know the moving parts.

---

## Option A: Railway / PaaS (No server needed)

> **Best for:** Getting a running instance as fast as possible without managing any infrastructure. Railway has a free tier and provisions HTTPS automatically.

### What you'll need

- A [Railway](https://railway.com/) account (or Render, Fly.io, etc.)
- Google OAuth credentials (if you want Gmail inboxes) — you'll set these up in [Step 3A](#step-3a-connect-gmail-inboxes)
- Microsoft OAuth credentials (if you want Office 365 inboxes) — you'll set these up in [Step 3B](#step-3b-connect-office-365--outlook-inboxes)

> **Note:** You need at least one of Google or Microsoft credentials for the app to be functional. You don't need both.

### Steps

**1. Create a new project and deploy from Docker Hub.**

In Railway: **New Project → Deploy a Docker image**

- Image: `azowail/quickly:latest`

**2. Add a PostgreSQL database.**

In your Railway project, click **+ New → Database → Add PostgreSQL**.

Railway automatically injects `DATABASE_URL` as an environment variable — Quickly picks this up with no extra configuration.

**3. Set your environment variables.**

In your Railway service, go to **Variables** and add:

```env
BASE_URL=https://<your-railway-domain>.up.railway.app
```

If you already have your Google credentials (see [Step 3A](#step-3a-connect-gmail-inboxes) for how to get them):

```env
GOOGLE_CLIENT_ID=<your-google-client-id>
GOOGLE_CLIENT_SECRET=<your-google-client-secret>
```

If you already have your Microsoft credentials (see [Step 3B](#step-3b-connect-office-365--outlook-inboxes) for how to get them):

```env
OFFICE365_CLIENT_ID=<your-client-id>
OFFICE365_CLIENT_SECRET=<your-client-secret>
OFFICE365_TENANT_ID=common
```

**4. Deploy.**

Railway deploys automatically when you save variables. The Docker image pull takes about 1–2 minutes.

**5. Confirm your public URL.**

In the Railway dashboard, go to **Settings → Domains** and copy the generated URL (e.g. `https://quickly-production.up.railway.app`). If this differs from the `BASE_URL` you set above, update `BASE_URL` to match exactly, then redeploy.

**6. Custom tracking hostname (optional).** The prebuilt image is meant to use [**Quickly Beacon**](#quickly-beacon-recommended-custom-tracking-hostnames) on a separate URL for `track.yourbrand.com`–style links. Run Beacon there, paste its setup URL under **Inboxes → Connect Beacon**. You do not CNAME a tracking host directly to Railway’s Quickly service for that workflow.

---

**→ Continue to [Step 2: First Login & User Setup](#step-2-first-login--user-setup)**

---

## Option B: VPS with Docker Compose

> **Best for:** **Your own VPS** with Docker and PostgreSQL.

### Which Compose file?

| File | When to use |
|---|---|
| **`docker-compose.no-caddy.yml`** | **Recommended** if you plan to use **only [Quickly Beacon](#quickly-beacon-recommended-custom-tracking-hostnames)** for custom tracking hostnames (the usual path for the prebuilt image). Runs **Postgres + Quickly** on port **8000** with **no Caddy** in the stack — terminate HTTPS with nginx, Traefik, **host Caddy**, a cloud load balancer, or another reverse proxy in front. Creates its own Postgres volume automatically; no `docker volume create` step. |
| **`docker-compose.yml`** | You want **Caddy inside Docker** to obtain and renew Let's Encrypt certificates for your main Quickly domain (single stack, minimal moving parts). |
| **`docker-compose-not-host.yml`** | You already run **Caddy on the host** and may need the **legacy CNAME-to-Quickly** tracking flow with on-demand TLS — see [Already running Caddy on the host](#already-running-caddy-on-the-host-not-host). Not required for Beacon-only setups. |

---

<a id="beacon-only-docker-compose-no-caddy"></a>

### Beacon-only (recommended): docker-compose.no-caddy.yml

**What you'll need**

- A Linux VPS (or any host) with [Docker Engine](https://docs.docker.com/engine/install/) and the Docker Compose plugin  
  Quick install: `curl -fsSL https://get.docker.com | sh`
- A way to expose the app over **HTTPS** in production (reverse proxy, tunnel, or TLS at the edge) — Compose publishes Quickly on **8000** by default
- A domain is typical: point DNS at your proxy, which forwards to Quickly on the host (e.g. `127.0.0.1:8000`)

**Steps**

**1. SSH into your server** (if applicable) and create a working directory.

```bash
mkdir quickly && cd quickly
```

**2. Download Compose and env template.**

```bash
curl -LO https://github.com/azowail/quickly/releases/latest/download/docker-compose.no-caddy.yml
curl -LO https://github.com/azowail/quickly/releases/latest/download/.env.example
mv .env.example .env
```

**3. Edit `.env`.** Set at minimum **`BASE_URL`** (the full public URL users open — must match your reverse proxy), **`QUICKLY_SECRET_KEY`**, and OAuth variables when you connect inboxes (same idea as below in the all-in-one path). **`CADDY_HOST`** can stay empty; this stack does not run the project’s Caddy container.

> `DATABASE_URL` is set in `docker-compose.no-caddy.yml` for the bundled PostgreSQL service.

**4. Start Quickly.**

```bash
docker compose -f docker-compose.no-caddy.yml up -d
```

**5. Point your reverse proxy** at `127.0.0.1:8000` (or change the published port in the file if you prefer). Reload the proxy.

**6. Custom tracking:** run [Quickly Beacon](#quickly-beacon-recommended-custom-tracking-hostnames) on its own origin and connect it under **Inboxes → Connect Beacon**.

> **Next step (Beacon-only path):** [Step 2: First Login & User Setup](#step-2-first-login--user-setup). Skip the all-in-one Caddy section below if this file is what you deployed.

---

### All-in-one: Postgres + Quickly + Caddy (`docker-compose.yml`)

> **Best for:** One Compose stack where **Caddy** obtains and renews HTTPS certificates (Let's Encrypt) for your main Quickly domain — no Certbot.

### What you'll need

- A Linux VPS running Ubuntu 22.04+ (or any Docker-capable Linux distro)
- [Docker Engine](https://docs.docker.com/engine/install/) with the Docker Compose plugin
    - Quick install: `curl -fsSL https://get.docker.com | sh`
- Ports **80** and **443** open in your firewall (see [Firewall Setup](#firewall-setup) below)
- A domain name with an **A record** pointing to your server's public IP

### Steps

**1. SSH into your server.**

```bash
ssh user@your-server-ip
```

**2. Create a Docker volume for PostgreSQL (once, before the first start).**

The bundled `docker-compose.yml` keeps Postgres data in a **named Docker volume** so it survives container restarts and image updates.

```bash
docker volume create quickly_pgdata
```

If this volume does not exist yet, `docker compose up` will fail — the file expects `quickly_pgdata` to be there already.

**3. Download the required files.**

```bash
mkdir quickly && cd quickly

curl -LO https://github.com/azowail/quickly/releases/latest/download/docker-compose.yml
curl -LO https://github.com/azowail/quickly/releases/latest/download/Caddyfile
curl -LO https://github.com/azowail/quickly/releases/latest/download/.env.example
mv .env.example .env
```

**4. Edit your `.env` file.**

```bash
nano .env
```

Set these values at minimum:

```env
# Your domain — Caddy will automatically obtain a Let's Encrypt certificate for it
CADDY_HOST=yourdomain.com

# The full public URL — used for OAuth redirects and email links
BASE_URL=https://yourdomain.com

# Generate with: python3 -c "import secrets; print(secrets.token_urlsafe(64))"
QUICKLY_SECRET_KEY=your-generated-secret
```

For your inbox credentials, you can add them now or later via the Settings page:

```env
# Google credentials — see Step 3A for how to get these
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=

# Microsoft credentials — see Step 3B for how to get these
OFFICE365_CLIENT_ID=
OFFICE365_CLIENT_SECRET=
OFFICE365_TENANT_ID=common
```

> `DATABASE_URL` is already pre-configured in `docker-compose.yml` to use the bundled PostgreSQL container. You don't need to change it.

**5. Open the required firewall ports.**

Caddy needs ports 80 and 443 for the Let's Encrypt domain challenge:

```bash
# Ubuntu/Debian with ufw
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw reload
```

If you're on a cloud provider, also open these ports in your cloud dashboard:

|Provider|Where to open ports|
|---|---|
|DigitalOcean|Networking → Firewalls|
|Hetzner|Firewall settings for the server|
|Vultr|Settings → Firewall|
|AWS EC2|Security Groups → Inbound Rules|
|Google Cloud|VPC Network → Firewall rules|

**6. Start everything.**

```bash
docker compose up -d
```

On first start, Caddy contacts Let's Encrypt and obtains a TLS certificate for your domain (takes a few seconds). Visit `https://yourdomain.com` — Quickly is live.

Certificates are stored in the `caddy_data` Docker volume and **renew automatically**. You never need to manage them again.

**7. Verify all services are running.**

```bash
docker compose ps        # all services should show "running"
docker compose logs app  # check for any startup errors
```

### Already running Caddy on the host (not-host)

Choose this if **Caddy is already on your VPS** for other sites and you **do not** want another Caddy container in Docker.

> **Planning to use only Beacon for tracking?** Use **`docker-compose.no-caddy.yml`** instead ([Beacon-only path](#beacon-only-docker-compose-no-caddy)) — that is the **recommended** Compose layout; you do not need this not-host file unless you want **host Caddy** plus the legacy flow below.

> **Not the default path for tracking.** Prefer [**Quickly Beacon**](#quickly-beacon-recommended-custom-tracking-hostnames) for custom tracking domains so Quickly and TLS stay simple. Use **this** not-host layout only if you intentionally run **one** Quickly stack behind **host** Caddy (see `docker-compose-not-host.yml` and the sample `Caddyfile.host` in the repo) and you understand on-demand TLS and DNS — for example **legacy CNAME-to-Quickly** tracking. The Compose file sets `QUICKLY_PREBUILT_IMAGE=0` so the legacy **CNAME to Quickly** controls stay available in the inbox UI.

**Compared to the Compose + Caddy layout above**

| | Compose + Caddy (`docker-compose.yml`) | Not-host (`docker-compose-not-host.yml`) |
|---|---|---|
| Caddy | Runs **inside** Docker with Quickly | Only your **existing host Caddy** |
| What you download | `docker-compose.yml` + `Caddyfile` | `docker-compose-not-host.yml` (no Caddyfile for Docker) |
| HTTPS | The Compose Caddy gets certificates | Your **host** Caddy keeps handling TLS as it does today |

**What to do**

1. **Same PostgreSQL volume as above** — create it once if you have not already:

   ```bash
   docker volume create quickly_pgdata
   ```

2. Download **`docker-compose-not-host.yml`** from the [releases page](https://github.com/azowail/quickly/releases/latest) (bundled beside `docker-compose.yml`), plus **`.env.example`**:

   ```bash
   mkdir quickly && cd quickly
   curl -LO https://github.com/azowail/quickly/releases/latest/download/docker-compose-not-host.yml
   curl -LO https://github.com/azowail/quickly/releases/latest/download/.env.example
   mv .env.example .env
   ```

3. Edit **`.env`**: set **`BASE_URL`**, **`QUICKLY_SECRET_KEY`**, and your OAuth variables — same as in the **Compose + Caddy** steps above. You can leave **`CADDY_HOST`** empty; this stack does not run the project’s Caddy container.

4. Start Quickly:

   ```bash
   docker compose -f docker-compose-not-host.yml up -d
   ```

5. Tell **host Caddy** to proxy to Quickly. The not-host compose publishes Quickly on **`127.0.0.1:5050`** (and also on `8000`). Add a site block like this to **`/etc/caddy/Caddyfile`** (use your real domain):

   ```caddy
   quickly.example.com {
       reverse_proxy 127.0.0.1:5050
   }
   ```

   Reload Caddy on the host (for example: `sudo systemctl reload caddy`). That is all — your other sites are unchanged.

> **Tip:** If you prefer to maintain one edited `docker-compose.yml` by hand, you can instead follow [Option C](#option-c-existing-server-with-nginx) and remove the in-compose Caddy service yourself. The not-host file is the same idea in a ready-made form.

---

**→ Continue to [Step 2: First Login & User Setup](#step-2-first-login--user-setup)**

---

## Option C: Existing Server with nginx

> **Best for:** You already have nginx running on this server and don't want to disrupt your current setup.

The recommended approach is to migrate from nginx to Caddy. This gives you automatic HTTPS for all your existing sites _and_ enables Quickly's custom tracking domain feature.

### Steps

**1. Install Caddy on the host.**

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install caddy
```

**2. Convert your existing nginx sites to Caddyfile format.**

For most sites, the conversion is simple:

```nginx
# nginx — before
server {
    listen 80;
    server_name example.com;
    location / { proxy_pass http://localhost:3000; }
}
```

```caddy
# Caddy — after (HTTPS is automatic, no extra config needed)
example.com {
    reverse_proxy localhost:3000
}
```

> **Have a complex nginx config?** If your existing config uses rewrites, custom headers, caching rules, or multiple `location` blocks, the conversion isn't always straightforward. Search for "nginx to Caddy migration" for your specific use case, or paste your nginx config into any AI chatbot and ask it to convert it to Caddyfile format — it handles this well.

**3. Stop nginx and start Caddy.**

```bash
sudo systemctl stop nginx
sudo systemctl disable nginx
sudo systemctl enable caddy
sudo systemctl start caddy
```

Caddy will automatically obtain Let's Encrypt certificates for all your domains on first request.

**4. Update `docker-compose.yml`.**

Since the host Caddy now handles everything, remove the Caddy Docker service:

- Remove the `caddy:` service block and its volumes from `docker-compose.yml`
- Change `expose: ["8000"]` on the `app` service to `ports: ["127.0.0.1:8000:8000"]`

**5. Add Quickly to your host Caddyfile.**

Edit `/etc/caddy/Caddyfile` and add:

```caddy
yourdomain.com {
    reverse_proxy localhost:8000
}
```

Then reload and start:

```bash
sudo systemctl reload caddy
docker compose up -d
```

---

**→ Continue to [Step 2: First Login & User Setup](#step-2-first-login--user-setup)**

---

## Option D: Local Development

> **Best for:** Contributing to Quickly, testing features, or running it on your own machine.

### Prerequisites

- Python 3.12+
- Node.js 18+
- PostgreSQL 15+ (local install or via Docker)
- **PostgreSQL client** (`pg_dump`, `pg_restore`) if you use **Settings → Backup** while running the backend on the host — e.g. `postgresql-client` on Debian/Ubuntu. Docker-based dev images include these tools.
- **Saving backups on disk** from Settings needs **`QUICKLY_LOCAL_DISK_BACKUPS=1`** and a writable **`backups`** mount (all **Docker Compose** files in this repo set both). Hosted platforms without a persistent volume should use **webhook** delivery instead.

---

### Quick Option: Docker Dev Stack

The fastest way to run everything locally with hot-reload:

```bash
git clone https://github.com/azowail/quickly.git
cd quickly
cp .env.example .env
# Edit .env and set: BASE_URL=http://localhost:8000
docker compose -f docker-compose.dev.yml up
```

Open `http://localhost:5173` — the frontend hot-reloads on changes; the backend reloads on Python changes.

**No Caddy in Compose (prebuilt-style UI):** `docker compose -f docker-compose.no-caddy.dev.yml up` uses **`.env_dev`**, Postgres on host **5435**, API on **8002**, Vite on **5175**, and **`QUICKLY_PREBUILT_IMAGE=1`** like the Docker Hub image. **`docker-compose-not-host.dev.yml`** is similar but keeps port **5051** for a local host Caddy `reverse_proxy` and uses the external **`quickly_pgdata_dev`** volume.

---

### Manual Setup (Recommended for backend development)

**1. Clone the repo and set up Python.**

```bash
git clone https://github.com/azowail/quickly.git
cd quickly

python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux

pip install -r requirements.txt
```

**2. Start a PostgreSQL instance.**

**Option A — Docker (no local install required):**

```bash
docker run -d --name quickly-db \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=quickly \
  -p 5432:5432 \
  postgres:15-alpine
```

**Option B — Native install:** See [postgresql.org/download](https://www.postgresql.org/download/).


**3. Configure your environment.**

```bash
cp .env.example .env
```

Minimum values for local development:

```env
BASE_URL=http://localhost:8000
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost/quickly
```

Add `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` if you want to test Gmail connections locally (see [Step 3A](#step-3a-connect-gmail-inboxes) for how to get these).

**4. Start the backend.**

```bash
uvicorn app.main:app --reload
# API available at http://localhost:8000
```

**5. Start the frontend (in a separate terminal).**

```bash
cd frontend
npm install
npm run dev
# UI available at http://localhost:5173 — proxies /api calls to localhost:8000
```

### Running Tests

```bash
# Fast — in-memory SQLite, no PostgreSQL required
pytest

# Against PostgreSQL — closer to production
set TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost/test_quickly  # Windows
# export TEST_DATABASE_URL=...  # macOS/Linux
pytest
```

---

**→ Continue to [Step 2: First Login & User Setup](#step-2-first-login--user-setup)**

---

## Step 2: First Login & User Setup

**1. Open your Quickly URL in a browser.**

**2. Create your admin account.**

Click **Register** and sign in with your Google or Microsoft account. Make sure you've added credentials for whichever provider you want to use before attempting this (see [Step 3A](#step-3a-connect-gmail-inboxes) or [Step 3B](#step-3b-connect-office-365--outlook-inboxes)).

**3. Log in.**

Use the same account you registered with.

**4. (Optional) Connect an account for email notifications.**

From the **Settings** page, you can connect your personal Google or Microsoft account to receive email notifications (e.g. "a lead replied as interested"). This is separate from the inboxes used for outbound sending.

### API Keys

For programmatic access (n8n, scripts, custom integrations), generate API keys from **Settings → API Keys**. Keys are shown only once at creation — store them securely.

> **Important:** Set `QUICKLY_SECRET_KEY` in your `.env` to a stable random string. If omitted, a new key is generated on every restart, which invalidates all existing login sessions.

---

**→ Connect your sending inboxes:**

- → [Step 3A: Connect Gmail Inboxes](#step-3a-connect-gmail-inboxes)
- → [Step 3B: Connect Office 365 / Outlook Inboxes](#step-3b-connect-office-365--outlook-inboxes)
- → [Step 3C: Connect Amazon SES (via SMTP)](#step-3c-connect-amazon-ses-via-smtp)
- → [Connect both Gmail and Office 365](#connecting-both-gmail-and-office-365)

---

## Step 3A: Connect Gmail Inboxes

> You can connect as many Gmail accounts as you want. Each becomes a sending inbox that Quickly rotates emails across.

### 1. Create a Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Click the project selector → **New Project**
3. Give it a name (e.g. "Quickly Email") and click **Create**

### 2. Enable Required APIs

1. Go to **APIs & Services → Library**
2. Search for and enable **Gmail API**
3. Search for and enable **Cloud Pub/Sub API** _(required for real-time reply detection in Unibox)_

### 3. Create OAuth Credentials

1. Go to **APIs & Services → Credentials → Create Credentials → OAuth client ID**
2. Application type: **Web application**
3. Under **Authorized redirect URIs**, add both:
    - `https://yourdomain.com/oauth/app/google/callback` _(for login/registration)_
    - `https://yourdomain.com/oauth/google/callback` _(for inboxes)_
4. Click **Create** and copy your **Client ID** and **Client Secret**

> **Stuck in the Google Cloud Console?** The UI can be confusing if this is your first time. Try searching "create Google OAuth client ID for web app", or paste these instructions into any AI chatbot and ask it to walk you through them — just mention you need OAuth credentials for a self-hosted web app with specific redirect URIs.

### 4. Add Credentials to Your Environment

Add the values you just copied to your `.env` file (or your PaaS environment variables):

```env
GOOGLE_CLIENT_ID=123456789-abc.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-abcdef...
```

Then restart your containers:

```bash
docker compose up -d
```

### 5. Configure the OAuth Consent Screen

1. Go to **APIs & Services → OAuth consent screen**
2. User type: **External** (or **Internal** for Google Workspace organizations)
3. Fill in app name, support email, and developer contact
4. Under **Scopes**, add: `https://mail.google.com/`
5. Under **Test users**, add the Gmail addresses you plan to use

> **About publishing:** While in "Testing" mode, only listed test users can authorize. For personal use, this is perfectly fine — just add your own addresses. To allow any Google account, click **Publish App** and follow the verification process.

### 6. (Optional) Set Up Pub/Sub for Real-Time Reply Detection

Skip this if you're happy with scheduled polling for reply detection.

1. Go to **Pub/Sub → Topics → Create Topic**
2. Topic ID: `quickly-gmail-push` (or any name you prefer)
3. Create a **Push subscription** on the topic pointing to: `https://yourdomain.com/api/unibox/gmail/push`
4. In the topic's **Permissions** tab, grant `gmail-api-push@system.gserviceaccount.com` the **Pub/Sub Publisher** role
5. In Quickly, go to **Settings → Gmail Sync** and enter the full topic name (e.g. `projects/your-project/topics/quickly-gmail-push`)

> **This is the most involved step in the whole guide.** IAM roles, push subscriptions, and topic naming are easy to get wrong. If you're hitting errors, search "Google Cloud Pub/Sub push subscription setup" or ask an AI chatbot to walk you through it — describe that you need a push subscription that forwards to a webhook URL, with the Gmail push service account as publisher.

### 7. Connect Gmail Accounts in Quickly

1. In Quickly, go to **Inboxes → Add Inbox → Connect Gmail Account**
2. Complete the Google OAuth flow
3. The inbox appears automatically and is ready to use

Repeat for as many Gmail accounts as you want.

---

**→ Also setting up Office 365? See [Step 3B](#step-3b-connect-office-365--outlook-inboxes)** **→ Ready to send? Skip to [Step 4: Create Your First Campaign](#step-4-create-your-first-campaign)**

---

## Step 3B: Connect Office 365 / Outlook Inboxes

> Quickly supports Microsoft 365, Office 365, and personal Outlook.com accounts. Gmail and Microsoft inboxes can be mixed freely in the same campaign.

> **The Azure portal can be overwhelming.** If you've never registered an app there before, the navigation is dense and the permission/consent flow is easy to get wrong. If you get stuck at any point below, search "Azure app registration OAuth web app" or ask an AI chatbot to guide you through registering an app in Azure AD with delegated Microsoft Graph permissions — it's a very common setup and well-documented.

### Quick Summary

**1. Register an app in Azure.**

Go to [Azure Portal](https://portal.azure.com/) → **Azure Active Directory → App registrations → New registration**

**2. Add redirect URIs.**

Under **Authentication**, set the redirect URIs to:

- `https://yourdomain.com/oauth/office365/callback`
- `https://yourdomain.com/oauth/app/office365/callback`

**3. Add required API permissions.**

Go to **API permissions → Add a permission → Microsoft Graph → Delegated permissions** and add:

- `Mail.ReadWrite`
- `Mail.Send`
- `User.Read`
- `offline_access`

**4. Create a client secret.**

Go to **Certificates & secrets → New client secret**. Copy the **Value** immediately — it won't be shown again.

**5. Copy your credentials.**

From the app's **Overview** page, copy:

- **Application (client) ID** → this is your `OFFICE365_CLIENT_ID`
- **Directory (tenant) ID** → use `common` for multi-tenant, or your specific ID for single-tenant

**6. Add credentials to your environment.**

Add the values you just copied to your `.env` file (or your PaaS environment variables):

```env
OFFICE365_CLIENT_ID=<application-client-id>
OFFICE365_CLIENT_SECRET=<client-secret-value>
OFFICE365_TENANT_ID=common   # or your specific tenant ID for single-tenant setups
```

Then restart your containers:

```bash
docker compose up -d
```

**7. Connect accounts in Quickly.**

Go to **Inboxes → Add Inbox → Connect Office 365 Account** and complete the OAuth flow.

---

## Step 3C: Connect Amazon SES (via SMTP)

> Amazon SES (Simple Email Service) works with Quickly's generic **SMTP provider** — no extra integration code is needed. You use SES's SMTP interface, so Quickly treats it like any other SMTP relay.

### 1. Get SES SMTP credentials

1. In the AWS console, go to **SES → SMTP settings → Create SMTP credentials**
2. This creates an IAM user and shows you an **SMTP username / password pair** (shown once — save them). These are *not* regular AWS access keys and can't be interchanged with them.
3. Note your region's SMTP endpoint, e.g. `email-smtp.us-east-1.amazonaws.com` (replace `us-east-1` with your region)

> SMTP credentials are **unique to each AWS region** — sending from a second region needs a second set. Also note some newer/smaller regions have no SMTP endpoint at all (only the API); if yours doesn't, pick a region that does.

### 2. Verify your sending identity

In **SES → Verified identities**, verify the domain or email address you will send from. Quickly's inbox email must match a verified identity, otherwise SES rejects every send.

> **Sandbox warning:** new SES accounts start in the **sandbox**: you can only send *to* verified addresses and throughput is capped. Request **production access** (SES → Account dashboard) before running real campaigns.

### 3. Add the inbox in Quickly

Go to **Inboxes → Add Inbox → SMTP (any provider)** and fill in:

| Field | Value |
|---|---|
| Email address | Your verified sender address |
| SMTP host | `email-smtp.<region>.amazonaws.com` |
| SMTP port | `587` with **STARTTLS** (or `465` with **SSL**) |
| SMTP username / password | The SMTP credentials from step 1 |
| IMAP | Leave empty — SES is send-only (see below) |

Quickly tests the connection automatically after creation.

### Notes and limits

- **Replies:** SES has no mailbox, so there is nothing for IMAP reply-sync to connect to. SES inboxes are **send-only**: replies won't appear in the Unibox unless you route them elsewhere (e.g. set SES to forward replies to a Gmail/IMAP mailbox you also connect).
- **Bounces:** sends rejected at SMTP time (unverified identity, sandbox violation) surface as `email.bounced` and the lead is marked bounced automatically. Delayed bounces via SNS notifications are not integrated — if you need those, configure SES to forward bounce mail to a monitored mailbox.
- **Limits:** SES enforces per-region daily quotas and send rates. Mirror them in the inbox's **Max emails per day** and **Wait between emails** so Quickly never outruns your SES limits.

---

**→ Continue to [Step 4: Create Your First Campaign](#step-4-create-your-first-campaign)**

---

## Connecting Both Gmail and Office 365

No problem — complete both guides, then continue:

1. [Step 3A: Connect Gmail Inboxes](#step-3a-connect-gmail-inboxes)
2. [Step 3B: Connect Office 365 / Outlook Inboxes](#step-3b-connect-office-365--outlook-inboxes)
3. [Step 4: Create Your First Campaign](#step-4-create-your-first-campaign)

Gmail and Microsoft inboxes can be mixed freely in any campaign.

---

## Step 4: Create Your First Campaign

> This is a brief overview. The UI includes onboarding tooltips that walk you through each screen in detail.

**1. Go to Campaigns → New Campaign.**

Give your campaign a name and an optional timezone. If set, emails will be scheduled in the recipient's local timezone.

**2. Assign inboxes.**

Select which inboxes this campaign sends from. Quickly automatically rotates sends across them, respecting per-inbox daily limits and warm-up schedules.

**3. Build your sequence.**

Click **Add Step** to create your first email:

- Write a subject and body (HTML or plain text)
- Use `{{name}}`, `{{email}}`, `{{company}}`, or any `{{custom_field}}` as template variables
- Set **wait days** — how many days after the previous step before this one sends

Add as many follow-up steps as you want.

**4. (Optional) Add A/B variants.**

On any step, click **Add Variant** to create an alternate subject or body. Quickly selects randomly at send time and tracks performance per variant.

**5. Import leads.**

Click **Import Leads → Upload CSV**. Your CSV needs at minimum an `email` column. Any additional columns (`name`, `company`, `title`, etc.) automatically become available as template variables.

**6. Start the campaign.**

Click **Start Campaign**. The queue engine reserves send slots across your inboxes and begins sending at the scheduled times.

---

**→ Explore more:**

- [Set up webhooks](docs/WEBHOOKS.md) — react to opens, clicks, and replies in real time
- [Explore the REST API](docs/API.md) — automate everything programmatically
- [Configure AI reply classification](#optional-ai-reply-classification) — auto-classify incoming replies

---

## Optional: AI Reply Classification

Quickly automatically classifies every reply into one of six categories: `interested`, `not_interested`, `out_of_office`, `wrong_person`, `auto_reply`, or `unsubscribed`.

**Supported providers:** OpenAI, Anthropic Claude, Google Gemini, Mistral, Groq, Cohere, and 13+ others — including **Ollama** for fully local/offline classification.

**To configure:**

1. Go to **Settings → AI Features**
2. Select your AI provider
3. Enter your API key (or your Ollama endpoint for local models)
4. Select the model
5. Enable the feature

All AI settings are stored in the database — no restart required.

---

## Quickly Beacon (recommended custom tracking hostnames)

Use **Quickly Beacon** when you want open, click, and unsubscribe links to use **your own hostname** (for example `track.yourbrand.com`) without pointing that DNS name at the main Quickly app.

**Self-hosted Docker:** If you plan to use **only Beacon** (and not the legacy CNAME-to-Quickly tracking flow), **`docker-compose.no-caddy.yml` is the recommended Compose file** — Postgres plus the Quickly image on port **8000**, no Caddy in the stack. Put your own reverse proxy in front for HTTPS. See [Beacon-only (recommended)](#beacon-only-docker-compose-no-caddy) under Option B.

**How it works:** Beacon is a small tracking proxy. It serves pixels and redirects on the domain you choose, then posts signed events back to Quickly. Your main app URL stays on Railway, VPS Compose, etc.

**Typical setup**

1. **Run Beacon** so it is reachable on the HTTPS origin you want for tracking (for example a small VM, Railway service, or a `reverse_proxy` on host Caddy to `127.0.0.1:8090`).
2. Set **`BEACON_PUBLIC_BASE_URL`** to that public origin (with protocol, no trailing path) if Beacon must print correct setup links in logs or HTML.
3. While Beacon is **not** connected yet, open its root URL and **copy the setup link** (includes `?token=…`).
4. In Quickly → **Inboxes** → choose an inbox → **Connect Beacon**, paste the full URL. After a successful connect, Beacon stops showing the token on `/` and behaves like a normal tracking host.

**Multiple inboxes:** One Beacon deployment and one DNS hostname can serve **many** Quickly inboxes. Use the **same** Beacon URL for each inbox; paste the setup link (or rely on Quickly’s saved token) and click **Connect** per inbox so each gets its own webhook secret and token registrations. Disconnecting one inbox in Quickly removes only that inbox’s rows on Beacon.

In the inbox **Edit** UI, expand **Use a tracker already linked to another inbox** to pick an existing Beacon or DNS setup from another inbox and connect in one click (no need to paste the setup URL again for Beacon).

That is the full loop for most operators.

**Prebuilt image / Docker Hub (`azowail/quickly:latest`)** sets `QUICKLY_PREBUILT_IMAGE=1`. The inbox UI **hides** the legacy “custom domain + CNAME to Quickly” section and steers you to Beacon instead. To show that CNAME workflow again (for example on a **host Caddy** deployment where Quickly terminates on-demand TLS for tracking hosts), set **`QUICKLY_TRACKING_CNAME_UI=1`** or **`QUICKLY_PREBUILT_IMAGE=0`** in the app environment and restart.

---

## Optional: Advanced — CNAME custom domains to Quickly

> **Legacy / advanced.** Prefer [Quickly Beacon](#quickly-beacon-recommended-custom-tracking-hostnames). This path only applies when **Quickly’s Caddy** (Compose or **host** Caddy) terminates HTTPS for the tracking hostname via **on-demand TLS** and the `/api/caddy/ask` flow. It is **not** how Railway-only or minimal PaaS installs are expected to add a tracking domain.

Requires Caddy with on-demand TLS (Option B, Option C after migrating to Caddy, or **not-host** + `Caddyfile.host`).

**To configure:**

1. In your DNS provider, add a CNAME record:
    - **Name:** `track` (or any subdomain you prefer)
    - **Value:** the hostname Caddy uses for Quickly (often your main app host), as shown in the inbox UI when the CNAME workflow is enabled
2. In Quickly → **Inboxes** → tracking section, choose **Custom domain**, verify DNS, and save
3. Enter the full subdomain (e.g. `track.yourdomain.com`)

Caddy automatically provisions a certificate for this domain on the first request.

---

## Updating Quickly

**`docker-compose.yml` (Caddy in Docker):**

```bash
docker compose pull
docker compose up -d
```

**`docker-compose-not-host.yml`** (host Caddy):

```bash
docker compose -f docker-compose-not-host.yml pull
docker compose -f docker-compose-not-host.yml up -d
```

**`docker-compose.no-caddy.yml`** (Beacon-only / no in-compose Caddy):

```bash
docker compose -f docker-compose.no-caddy.yml pull
docker compose -f docker-compose.no-caddy.yml up -d
```

The PostgreSQL Docker volume keeps your data across updates (`quickly_pgdata` for the Caddy and not-host stacks; a Compose-managed volume for **`docker-compose.no-caddy.yml`**). Quickly applies schema changes on startup — no manual migrations needed.

---

## Environment Variables Reference

> The `.env` file is intentionally minimal. Most runtime settings (send windows, warm-up schedules, AI providers, etc.) are configured from the **Settings page** in the UI and stored in the database.

|Variable|Required|Default|Description|
|---|---|---|---|
|`BASE_URL`|**Yes**|—|Full URL with protocol, e.g. `https://yourdomain.com`|
|`CADDY_HOST`|For Caddy HTTPS|_(empty = HTTP on :80)_|Domain for Caddy auto-HTTPS|
|`DATABASE_URL`|Auto|Set by docker-compose|PostgreSQL connection string|
|`GOOGLE_CLIENT_ID`|For Gmail|—|Google OAuth 2.0 client ID — [see Step 3A](#step-3a-connect-gmail-inboxes)|
|`GOOGLE_CLIENT_SECRET`|For Gmail|—|Google OAuth 2.0 client secret — [see Step 3A](#step-3a-connect-gmail-inboxes)|
|`OFFICE365_CLIENT_ID`|For Office 365|—|Microsoft Entra app (client) ID — [see Step 3B](#step-3b-connect-office-365--outlook-inboxes)|
|`OFFICE365_CLIENT_SECRET`|For Office 365|—|Microsoft Entra app client secret — [see Step 3B](#step-3b-connect-office-365--outlook-inboxes)|
|`OFFICE365_TENANT_ID`|No|`common`|Use `common` for multi-tenant, or your specific tenant ID — [see Step 3B](#step-3b-connect-office-365--outlook-inboxes)|
|`QUICKLY_SECRET_KEY`|No|auto-generated|JWT signing key — **set this** or sessions reset on every restart|
|`CORS_ORIGINS`|No|`http://localhost:5173,...`|Comma-separated allowed CORS origins|
|`QUICKLY_LOCAL_DISK_BACKUPS`|No|_(off)_|Set to `1` or `true` to allow saving backups under a folder in **Settings → Setup → Backup** (default `backups/` under the app directory). The **docker-compose\*.yml** files in this repo (including **`docker-compose.no-caddy.yml`**) set this and mount **`./backups:/app/backups`**. The app keeps the **10** newest backup files (`.qbk` wrapper format). PaaS without a volume: use **webhook** instead.|
|`QUICKLY_PREBUILT_IMAGE`|No|`1` in Docker image|When `1`/`true`/`yes`, the inbox UI hides CNAME-to-Quickly custom tracking setup so operators use **Beacon** instead. **`docker-compose.dev.yml`** sets `0` (CNAME UI visible while hacking). Production **`docker-compose-not-host.yml`** sets `0` for legacy host-Caddy + CNAME tracking. **`docker-compose.no-caddy.yml`**, **`docker-compose.no-caddy.dev.yml`**, and **`docker-compose-not-host.dev.yml`** set `1` to mirror the prebuilt image.|
|`QUICKLY_TRACKING_CNAME_UI`|No|_(off)_|Set to `1`/`true`/`yes` to **show** the CNAME custom tracking UI even when `QUICKLY_PREBUILT_IMAGE=1` (advanced / host-Caddy setups).|

**Backup and restore:** Backups are packaged as **`.qbk`** files (current format embeds a manifest plus a PostgreSQL custom-format dump). **Encrypted** backups include a small **plaintext preview** of the manifest (admin emails masked) so the UI can show what you are restoring before you enter the password; the dump itself stays encrypted. Older `.qbk` files from previous Quickly versions are not accepted — export a new backup from a current instance. You can download an encrypted backup from Settings (password required to restore; losing the password makes the file unrecoverable). If you enable **Encrypt automatic backups** in Settings, scheduled and “run now” backups use the same saved password (stored in the database, like webhook secrets). Restore uses **read metadata → verify password (if encrypted) → confirm** steps. The app uses `pg_dump` / `pg_restore` internally. The production Docker image includes the PostgreSQL client tools. If you run the backend directly on the host (e.g. `uvicorn` without Docker), install the client package for your OS (e.g. `postgresql-client` on Debian/Ubuntu). Compose dev files mount `./backups` to `/app/backups` so the default folder is persisted on the host. For multi-process deployments, set **`QUICKLY_RESTORE_STAGING_DIR`** to a shared directory so restore confirmation tokens work across workers.

**Generate a secret key:**

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

---

## Troubleshooting

### Caddy can't get a TLS certificate

- Verify your A record resolves to your server: `dig yourdomain.com`
- Verify ports 80 and 443 are reachable from the internet: `curl http://yourdomain.com` from another machine
- Check Caddy logs: `docker compose logs caddy`
- Ensure `CADDY_HOST` in `.env` matches your DNS record exactly — no `https://` prefix, no trailing slash

### App is unreachable after `docker compose up`

- Check that all services started: `docker compose ps`
- Check app logs: `docker compose logs app`
- Verify `.env` exists and `BASE_URL` is correctly set

### Database connection errors

- `DATABASE_URL` is set automatically by `docker-compose.yml` — don't override it unless you have a custom PostgreSQL setup
- If you did override it, make sure the hostname is `db` (the Docker service name), not `localhost`

### Gmail OAuth errors

- Verify `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` are set in your environment — [how to get them](#step-3a-connect-gmail-inboxes)
- Verify the **Authorized redirect URI** in Google Cloud Console exactly matches `https://yourdomain.com/oauth/google/callback`
- Confirm the **Gmail API** is enabled in your Google Cloud project
- If the app is in "Testing" mode, make sure the Gmail address is listed as a **test user** in the OAuth consent screen

### Office 365 OAuth errors

- Verify `OFFICE365_CLIENT_ID` and `OFFICE365_CLIENT_SECRET` are set in your environment — [how to get them](#step-3b-connect-office-365--outlook-inboxes)
- Verify the redirect URI in the Azure portal exactly matches `https://yourdomain.com/oauth/office365/callback`
- Confirm all required delegated permissions (`Mail.ReadWrite`, `Mail.Send`, `User.Read`, `offline_access`) are granted with admin consent
- For single-tenant setups, set `OFFICE365_TENANT_ID` to your specific tenant ID instead of `common`

### Login sessions expire on every restart

Set a stable `QUICKLY_SECRET_KEY` in your `.env`. Without it, a new random key is generated on every startup, which invalidates all JWT tokens.

### Running without HTTPS (local or HTTP-only)

Leave `CADDY_HOST` unset in `.env`. Caddy will serve on port 80 over plain HTTP:

```bash
docker compose up -d
# open http://localhost  (or http://your-server-ip)
```

---

## Additional Resources

| Document                                       | What's inside                                                   |
| ---------------------------------------------- | --------------------------------------------------------------- |
| [API.md](docs/API.md)                         | Complete REST API reference (90+ endpoints)                     |
| [WEBHOOKS.md](docs/WEBHOOKS.md)                | All 15 webhook event types, payload schemas, and authentication |
| [OFFICE365_SETUP.md](docs/OFFICE365_SETUP.md) | Full Azure portal walkthrough for Office 365 setup              |
| [CONTRIBUTORS.md](docs/CONTRIBUTORS.md)       | Dev environment setup and contribution guidelines               |
