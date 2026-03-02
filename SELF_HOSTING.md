# Self-Hosting Quickly

Quickly runs as a single Docker image (`azowail/quickly`) fronted by [Caddy](https://caddyserver.com/) for automatic HTTPS. The whole stack — app, database, and web server — is managed by a single `docker compose up` command.

---

## Prerequisites

- A Linux VPS (Ubuntu 22.04+ recommended) or any machine with Docker installed
- [Docker Engine](https://docs.docker.com/engine/install/) and the Docker Compose plugin (`docker compose`, not the legacy `docker-compose`)
- Ports **80** and **443** open in your firewall/security group (see [Firewall](#firewall))
- A domain name with an **A record** pointing to your server's public IP (only needed for HTTPS)

---

## Quick Start (Fresh VPS)

### 1. Download the required files

From the [latest release](https://github.com/azowail/quickly/releases/latest), download:

- `docker-compose.yml`
- `Caddyfile`
- `.env.example`

Or on the server directly:

```bash
mkdir quickly && cd quickly

curl -LO https://github.com/azowail/quickly/releases/latest/download/docker-compose.yml
curl -LO https://github.com/azowail/quickly/releases/latest/download/Caddyfile
curl -LO https://github.com/azowail/quickly/releases/latest/download/.env.example
mv .env.example .env
```

### 2. Configure your environment

Open `.env` and fill in your values. The key ones to set right away:

```env
# Your domain — Caddy will auto-fetch a Let's Encrypt cert on first request.
# Leave blank to run on http://localhost (no HTTPS, no domain needed).
CADDY_HOST=yourdomain.com

# Used for OAuth redirect URLs and any links sent in emails.
BASE_URL=https://yourdomain.com

# Google OAuth (required for Gmail inboxes)
GOOGLE_CLIENT_ID=your-client-id
GOOGLE_CLIENT_SECRET=your-client-secret
```

`DATABASE_URL` is already overridden by `docker-compose.yml` to point at the bundled Postgres container — you don't need to change it.

### 3. Start everything

```bash
docker compose up -d
```

On first boot Caddy will contact Let's Encrypt and obtain a certificate for your domain. This takes a few seconds. After that, visit `https://yourdomain.com` and Quickly is running.

Certs are stored in the `caddy_data` Docker volume and renewed automatically — you never have to think about them again.

### Updating to a newer version

```bash
docker compose pull
docker compose up -d
```

Your database and Caddy cert data live in named volumes and are preserved across updates.

---

## Running Locally (No Domain / HTTP Only)

Leave `CADDY_HOST` blank or unset in `.env`. Caddy will listen on port 80 over plain HTTP.

```bash
docker compose up -d
# open http://localhost
```

---

## Firewall

Caddy uses the [HTTP-01 ACME challenge](https://letsencrypt.org/docs/challenge-types/) to obtain certificates, which requires ports 80 and 443 to be reachable from the internet.

**Ubuntu with ufw:**

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw reload
```

**DigitalOcean / Hetzner / Vultr:** Open ports 80 and 443 in your cloud firewall/security group dashboard.

---

## Already Running nginx on This VPS?

If you have nginx managing other sites on the same server, ports 80 and 443 are already taken. You have two options:

---

### Option A — Migrate nginx to Caddy (Recommended)

This gives you automatic HTTPS for all your sites *and* unlocks the custom tracking domain feature in Quickly. Caddy's config syntax is much simpler than nginx.

#### 1. Install Caddy on the host

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install caddy
```

#### 2. Convert your nginx sites

For each nginx site, the equivalent Caddyfile block is usually:

```nginx
# nginx (before)
server {
    listen 80;
    server_name example.com;
    location / {
        proxy_pass http://localhost:3000;
    }
}
```

```caddy
# Caddy (after) — HTTPS is automatic, no extra config needed
example.com {
    reverse_proxy localhost:3000
}
```

Caddy has an [unofficial nginx-to-caddy converter](https://github.com/caddyserver/nginx2caddy) that handles most cases automatically.

#### 3. Stop nginx, start Caddy

```bash
sudo systemctl stop nginx
sudo systemctl disable nginx
sudo systemctl enable caddy
sudo systemctl start caddy
```

Caddy will obtain fresh Let's Encrypt certs for all your domains on first start.

#### 4. Remove the `caddy` service from Quickly's `docker-compose.yml`

Since the host Caddy is now managing everything, you don't want a second Caddy inside Docker competing for ports. Edit `docker-compose.yml`:

- Remove the entire `caddy:` service block
- Remove `caddy_data` and `caddy_config` from the `volumes:` section
- Change `expose: ["8000"]` on the `app` service to `ports: ["127.0.0.1:8000:8000"]` so it's reachable from the host but not publicly

Then add a site block to your host Caddyfile for Quickly:

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

### Option B — Keep nginx, Skip Caddy

Use this if you don't want to touch your existing nginx setup and don't need custom tracking domains.

Edit `docker-compose.yml`:

1. Remove the entire `caddy:` service block and its volumes
2. Change the `app` service to expose port 8000 on localhost:

```yaml
app:
  image: azowail/quickly:latest
  restart: unless-stopped
  ports:
    - "127.0.0.1:8000:8000"
  ...
```

Then add an nginx site config for Quickly:

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

Get a cert with certbot (if you don't already have one):

```bash
sudo certbot --nginx -d yourdomain.com
```

```bash
sudo nginx -t && sudo systemctl reload nginx
docker compose up -d
```

> **Note:** Custom tracking domains (a future feature) require Caddy and will not work with this setup.

---

## Troubleshooting

**Caddy can't get a cert / site shows "no certificate"**
- Confirm your domain's A record points to this server's IP: `dig yourdomain.com`
- Confirm ports 80 and 443 are open: `curl http://yourdomain.com` from outside the server
- Check Caddy logs: `docker compose logs caddy`

**App is unreachable after `docker compose up`**
- Check all services started: `docker compose ps`
- Check app logs: `docker compose logs app`
- Make sure `.env` exists and `CADDY_HOST` is set correctly

**Database connection errors**
- `DATABASE_URL` is automatically set by `docker-compose.yml` — you don't need to change it for the self-hosted setup
- If you overrode it in `.env`, make sure it points to `db` (the container name), not `localhost`
