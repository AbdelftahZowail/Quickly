# Custom Tracking Domains

Each inbox can serve its open and click tracking links from its own custom
hostname instead of the app's own URL.  This lets you brand tracking pixels and
redirect URLs under a domain you control — for example
`mail.yourclient.com/o/…` rather than `yourapp.com/o/…`.

---

## How it works

```
Lead opens the email
        │
        ▼
Image loads: https://mail.yourclient.com/o/<email_log_id>
        │
        ▼
Server records the open → returns 1×1 transparent pixel
        │
Lead clicks a link
        │
        ▼
Redirect: https://mail.yourclient.com/c/<token>
        │
        ▼
Server records the click → 302 redirects to the original URL
```

The `tracking_domain` field lives on each **inbox**.  When the send job
processes an e-mail, it inspects the sending inbox:

- If `inbox.tracking_domain` is set → pixel + redirect URLs use
  `https://<tracking_domain>`
- Otherwise → URLs fall back to `settings.base_url` (your app's domain)

---

## DNS setup (one-time per inbox)

Add a **CNAME** record at your domain registrar pointing from your chosen
subdomain to the server hostname shown in the Inboxes UI:

```
mail.yourclient.com.   CNAME   yourapp.com.
```

Only a single DNS record is needed.  The server hostname is automatically
fetched from the backend (`GET /api/settings/server-info`) and shown in the
inbox add/edit form alongside a real-time DNS snippet.

> **Tip:** use a subdomain like `mail.`, `track.`, or `em.` — avoid
> top-level domains as CNAMEs on bare/apex domains are not universally
> supported.

---

## SSL certificates (Caddy self-hosting only)

Caddy's **on-demand TLS** provisions a Let's Encrypt certificate the first
time a request arrives for a new hostname.  No manual cert management is
needed.

Before issuing a certificate Caddy calls the approval gate:

```
GET /api/caddy/ask?domain=<hostname>
```

The app returns **200** if the hostname matches either:

1. The app's own primary hostname (from `BASE_URL` in `.env`)
2. Any inbox's configured `tracking_domain`

Otherwise it returns **403** and Caddy rejects the certificate request,
preventing certificate abuse.

---

## PaaS / no Caddy

If you're not self-hosting with Caddy (e.g. Render, Railway, Fly.io), custom
tracking domains are **not supported** — HTTPS certificate provisioning
requires direct control of the reverse proxy.

Tracking still works fully on your app's own domain out of the box; simply
leave `tracking_domain` blank on every inbox.

---

## Setting up a custom tracking domain

1. **Open Inboxes** → click **Add Inbox** or **Edit** an existing one.
2. Scroll to the **Tracking domain** section.
3. Select **Custom domain** and type the hostname (e.g. `mail.yourclient.com`).
4. The form shows the exact CNAME record to add:
   ```
   mail.yourclient.com  CNAME  yourapp.com.
   ```
5. Add the record at your registrar, then click **Save**.
6. Wait for DNS propagation (usually < 5 minutes, may take up to 24 h).
7. Send a test email — pixel and link URLs will now use your custom domain.
   Caddy will auto-provision an SSL cert on the first real HTTPS request.

---

## Developer testing (docker-compose.dev.yml)

The dev Docker Compose stack includes a Caddy service for testing tracking
domains without deploying to production.

```bash
docker compose -f docker-compose.dev.yml up
```

Services:

| Service    | Host port | Purpose                                  |
|------------|-----------|------------------------------------------|
| `db`       | 5433      | PostgreSQL                               |
| `backend`  | 8000      | FastAPI (also directly accessible)       |
| `caddy`    | 80, 443   | Reverse proxy + on-demand TLS            |
| `frontend` | 5173      | Vite dev server                          |

### Local-only testing (no real domain)

For pure functional testing (tracking injection, pixel return, click redirect)
the custom domain does **not** need to be reachable from the internet.  You
can fake it:

1. Set `CADDY_HOST=` (empty) in `.env` — Caddy binds to HTTP `:80`.
2. Set `BASE_URL=http://localhost:8000` in `.env`.
3. In the inbox form, enter any hostname such as `track.local`.
4. In `/etc/hosts` (or `C:\Windows\System32\drivers\etc\hosts`):
   ```
   127.0.0.1   track.local
   ```
5. Send from that inbox — URLs in the email body will read
   `https://track.local/o/…`, which will resolve to localhost.

### Real domain with HTTPS

To test the full on-demand TLS flow:

1. Set `BASE_URL=https://yourdomain.com` (the server must be reachable on
   ports 80/443 from the internet — e.g. via a VPS or Cloudflare Tunnel).
2. Set `CADDY_HOST=yourdomain.com` so the primary site block also uses TLS.
3. Add the inbox's CNAME (see above).
4. Start the stack.  Caddy will provision certs automatically on first request.

---

## Caddyfile reference

The production `Caddyfile` and `Caddyfile.dev` both include:

```caddyfile
{
    on_demand_tls {
        ask http://app:8000/api/caddy/ask   # "backend" in dev
    }
}

{$CADDY_HOST::80} {
    reverse_proxy app:8000
}

:443 {
    tls {
        on_demand
    }
    reverse_proxy app:8000
}
```

The `ask` endpoint (`/api/caddy/ask?domain=<hostname>`) is the security gate
that prevents Caddy from issuing certificates for arbitrary domains.

---

## Database schema

```sql
-- Inbox table (new column added by Alembic migration)
ALTER TABLE inbox ADD COLUMN tracking_domain VARCHAR(255);

-- TrackedLink table (created at first startup via init_db)
CREATE TABLE tracked_link (
    id              SERIAL PRIMARY KEY,
    email_log_id    INTEGER NOT NULL REFERENCES email_log(id) ON DELETE CASCADE,
    token           VARCHAR(64) NOT NULL UNIQUE,
    original_url    TEXT NOT NULL,
    created_at      TIMESTAMP
);
```

> **Note:** `init_db()` calls `Base.metadata.create_all(…)` on startup, so
> `tracked_link` and the new `inbox.tracking_domain` column are created
> automatically when the app first starts.  No manual migration is required
> for fresh installs.  Existing databases need an `ALTER TABLE` —
> see the Alembic migration guide in [SELF_HOSTING.md](SELF_HOSTING.md).
