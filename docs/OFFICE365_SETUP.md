# Office 365 / Microsoft Outlook Setup Guide

This guide walks you through connecting Microsoft Office 365 (Outlook/Exchange Online) inboxes to Quickly for email sending, tracking, and reply detection — just like Google Workspace.

---

## Prerequisites

- A Microsoft 365 (formerly Office 365) account with an active mailbox
- Access to the [Azure Portal](https://portal.azure.com) to register an application
- Admin consent (or ability to self-consent) for the required Microsoft Graph API permissions

---

## Step 1: Register an App in Azure Active Directory

1. Go to the [Azure Portal](https://portal.azure.com)
2. Navigate to **Azure Active Directory** → **App registrations** → **New registration**
3. Fill in the registration form:
   - **Name**: `Quickly Email` (or any descriptive name)
   - **Supported account types**: Choose one of:
     - *Accounts in this organizational directory only* — single-tenant (your org only)
     - *Accounts in any organizational directory* — multi-tenant (any Microsoft 365 org)
     - *Accounts in any organizational directory and personal Microsoft accounts* — broadest access
   - **Redirect URI**:
     - Platform: **Web**
     - URI: `https://your-domain.com/oauth/office365/callback`
       - For local development: `http://localhost:8000/oauth/office365/callback`
4. Click **Register**

### Note the Application (client) ID and Directory (tenant) ID

After registration, you'll land on the app's **Overview** page. Copy these values:
- **Application (client) ID** → this is your `OFFICE365_CLIENT_ID`
- **Directory (tenant) ID** → this is your `OFFICE365_TENANT_ID`

> If you chose multi-tenant, you can set `OFFICE365_TENANT_ID=common` to allow any Microsoft 365 organization to connect.

---

## Step 2: Create a Client Secret

1. In your app registration, go to **Certificates & secrets** → **Client secrets** → **New client secret**
2. Add a description (e.g. `Quickly production`) and choose an expiration period
3. Click **Add**
4. **Copy the secret Value immediately** — it won't be shown again. This is your `OFFICE365_CLIENT_SECRET`

---

## Step 3: Configure API Permissions

1. In your app registration, go to **API permissions** → **Add a permission**
2. Select **Microsoft Graph** → **Delegated permissions**
3. Add the following permissions:
   - `Mail.ReadWrite` — Read and write access to user mail
   - `Mail.Send` — Send mail as the user
   - `User.Read` — Sign in and read user profile
   - `offline_access` — Maintain access to data you have given it access to (for refresh tokens)
4. Click **Add permissions**
5. If you're an admin, click **Grant admin consent for [your org]** (recommended for smoother experience)

### Required Permissions Summary

| Permission | Type | Description |
|---|---|---|
| `Mail.ReadWrite` | Delegated | Read/write mailbox messages (for inbox sync, reply detection, and Graph webhook subscriptions) |
| `Mail.Send` | Delegated | Send email on behalf of the user |
| `User.Read` | Delegated | Read user's email address and profile |
| `offline_access` | Delegated | Refresh tokens (long-lived access) |

---

## Step 4: Configure Quickly Environment Variables

Add the following environment variables to your `.env` file or server environment:

```env
# Office 365 / Microsoft OAuth 2.0
OFFICE365_CLIENT_ID=your-application-client-id
OFFICE365_CLIENT_SECRET=your-client-secret-value
OFFICE365_TENANT_ID=your-directory-tenant-id

# For multi-tenant apps, use:
# OFFICE365_TENANT_ID=common

# Make sure BASE_URL matches your redirect URI domain
BASE_URL=https://your-domain.com
```

### Environment Variable Reference

| Variable | Required | Description |
|---|---|---|
| `OFFICE365_CLIENT_ID` | Yes | Application (client) ID from Azure portal |
| `OFFICE365_CLIENT_SECRET` | Yes | Client secret value from Azure portal |
| `OFFICE365_TENANT_ID` | No | Directory (tenant) ID. Defaults to `common` for multi-tenant |
| `BASE_URL` | Yes | Your Quickly instance URL (used for OAuth redirect) |

---

## Step 5: Connect an Office 365 Account

1. Start/restart your Quickly instance so the new environment variables are loaded
2. Open the Quickly web UI and navigate to **Inboxes**
3. Click **Connect Office 365 Account** (or visit `/oauth/office365/authorize`)
4. You'll be redirected to Microsoft's login page — sign in with the Office 365 account you want to connect
5. Grant the requested permissions when prompted
6. After consent, you'll be redirected back to Quickly with the inbox automatically created

### What happens during connection:
- An OAuth token pair (access + refresh) is obtained and stored securely
- A new Inbox is created with `provider: office365`
- An initial email sync begins in the background (last 7 days)
- A Microsoft Graph webhook subscription is automatically created for the inbox so
  new mail notifications arrive in real time (no manual step required)
- The inbox is ready for sending immediately

---

## Step 6: Verify the Connection

### Check Office 365 Status
```
GET /api/office365/status
```
Returns whether Office 365 OAuth is configured and the redirect URI.

### List Connected Accounts
```
GET /api/office365/accounts
```
Returns all connected Office 365 accounts with their inbox details and token status.

---

## How It Works

### Sending
Office 365 inboxes send email via the **Microsoft Graph API** (`/me/sendMail`). This supports:
- HTML and plain-text emails
- Email threading (conversation continuity via `conversationId`)
- Custom headers (In-Reply-To, References, List-Unsubscribe)
- Open and click tracking (same as Gmail)
- A/B variant testing
- Rate limiting and daily caps
- Ramp-up / warm-up scheduling

### Inbox Sync (Unibox)
Reply detection and inbox sync work via the Microsoft Graph Messages API:
- **Initial sync**: Fetches messages from the last 7 days
- **Incremental sync**: Uses Graph API delta queries for efficient ongoing sync
- **Reply detection**: Automatically detects when leads reply, marks them as replied, and fires webhooks
- Syncs run on the same schedule as Gmail inboxes (default: every 5 minutes)

### Token Refresh
Access tokens are automatically refreshed when they expire (or within 5 minutes of expiry). Refresh tokens last much longer and are used to obtain new access tokens without user interaction.

---

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/office365/status` | Check if O365 OAuth is configured |
| `GET` | `/api/office365/accounts` | List connected O365 accounts |
| `GET` | `/oauth/office365/authorize` | Start OAuth flow (redirects to Microsoft) |
| `GET` | `/oauth/office365/callback` | OAuth callback (handled automatically) |
| `DELETE` | `/api/office365/accounts/{id}` | Disconnect an O365 account |
| `GET` | `/api/office365/graph-webhook/subscriptions` | List active Graph subscriptions |
| `POST` | `/api/office365/graph-webhook/subscriptions/{inbox_id}` | Create or renew a Graph subscription |
| `DELETE` | `/api/office365/graph-webhook/subscriptions/{inbox_id}` | Remove a Graph subscription |
| `POST` | `/api/office365/graph-webhook/notifications` | Microsoft Graph notification callback (public) |

### Query Parameters for `/oauth/office365/authorize`

| Parameter | Default | Description |
|---|---|---|
| `display_name` | `""` | Display name for the new inbox |
| `max_per_day` | `50` | Maximum emails per day |
| `ramp_up_enabled` | `false` | Enable warm-up ramp-up |

---

## Troubleshooting

### "No refresh token received"
- Ensure `offline_access` is included in the requested scopes
- The app registration must have `offline_access` in API permissions
- Try revoking access at https://myapps.microsoft.com and reconnecting

### "Failed to exchange authorization code"
- Verify your `OFFICE365_CLIENT_ID` and `OFFICE365_CLIENT_SECRET` are correct
- Check that the redirect URI in Azure matches exactly: `{BASE_URL}/oauth/office365/callback`
- Ensure the client secret hasn't expired

### "Microsoft auth/permission error (403)"
- The connected account may not have a valid Microsoft 365 license
- Admin consent may be required — ask your org admin to grant consent
- Check that `Mail.Send` and `Mail.ReadWrite` permissions are granted

### Token refresh failures
- Client secrets expire — check the expiration in Azure portal and rotate if needed
- The user may have revoked access — reconnect the account
- Check the `token_expired` webhook for automated alerts

### Emails not sending
- Verify the inbox provider is set to `office365` (check via `GET /api/inboxes`)
- Ensure the inbox is not paused
- Check the campaign sending window and daily limits
- Review logs at `logs/office365_api.log`

---

## Step 7: Enable Microsoft Graph Webhooks (Real-Time New Mail Notifications)

Microsoft Graph change notification subscriptions replace the 5-minute polling interval with a real-time push notification whenever new mail arrives in an inbox. This means reply detection and unibox updates happen within seconds instead of minutes.

### Prerequisites

- Your Quickly instance must be reachable from the public internet over **HTTPS** (Microsoft will not call `http://` or `localhost` endpoints).
- For local development, use a tunnel tool such as [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) or [ngrok](https://ngrok.com/) and set `BASE_URL` to the tunnel URL.

### How It Works

1. You call `POST /api/office365/graph-webhook/subscriptions/{inbox_id}` to subscribe an inbox.
2. Microsoft Graph sends a **validation POST** to `/api/office365/graph-webhook/notifications?validationToken=...` — Quickly echoes the token back and the subscription is confirmed.
3. When a new email arrives in the subscribed inbox, Microsoft immediately POSTs a change notification to the same URL.
4. Quickly validates the notification's `clientState` secret (constant-time comparison), then:
   - Queues an incremental sync for that inbox.
   - Broadcasts a `unibox.sync.triggered` event to all connected SSE clients so the UI refreshes instantly.
5. Subscriptions are valid for up to ~70 hours and are **automatically renewed every 6 hours** by a background job.

### Subscribe an Inbox

Because subscriptions are created automatically during account connection,
manual subscription is only necessary if you ever delete or renew a subscription
from the UI or via the API.  After connecting an Office 365 inbox (Step 5),
subscribe it to Graph notifications if you skipped the automatic step or if a
subscription has lapsed:

```bash
curl -X POST https://your-domain.com/api/office365/graph-webhook/subscriptions/{inbox_id} \
  -H "Authorization: Bearer <your-api-key>"
```

Successful response:
```json
{
  "ok": true,
  "action": "created",
  "subscription_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "expiry": "2026-03-12T14:00:00.000Z"
}
```

### List Active Subscriptions

```bash
curl https://your-domain.com/api/office365/graph-webhook/subscriptions \
  -H "Authorization: Bearer <your-api-key>"
```

Each entry includes `minutes_until_expiry` so you can monitor subscription health.

### Renew a Subscription Manually

Re-POST to the same endpoint to renew:

```bash
curl -X POST https://your-domain.com/api/office365/graph-webhook/subscriptions/{inbox_id} \
  -H "Authorization: Bearer <your-api-key>"
```

If the existing subscription is still valid it will be renewed via a PATCH. If it has expired, a new one is created automatically.

### Unsubscribe

```bash
curl -X DELETE https://your-domain.com/api/office365/graph-webhook/subscriptions/{inbox_id} \
  -H "Authorization: Bearer <your-api-key>"
```

The subscription is removed from both Microsoft Graph and the local database. Quickly will fall back to polling-only mode for that inbox.

### Notification Security

Each subscription is created with a unique random `clientState` secret (64-character hex string) that is stored in the database. Every incoming notification is validated by comparing the `clientState` field in the notification body to the stored secret using a constant-time HMAC comparison. Notifications with a missing or mismatched `clientState` are silently dropped, preventing spoofed notifications.

### Automatic Renewal

A background job runs every 6 hours and renews any subscription that will expire within 24 hours. No manual action is required after the initial subscription. If renewal repeatedly fails (e.g. due to an expired access token), the subscription will lapse and Quickly will fall back to the 5-minute polling schedule until the inbox is re-subscribed.

### Firewall / Reverse Proxy Requirements

Microsoft Graph sends notifications from a range of IP addresses. Ensure your firewall allows incoming `POST` requests from any source to `/api/office365/graph-webhook/notifications`. The `clientState` validation protects the endpoint; no IP-level allow-listing is required.

---


| Feature | Gmail | Office 365 |
|---|---|---|
| Auth mechanism | Google OAuth 2.0 | Microsoft OAuth 2.0 (MSAL) |
| Send API | Gmail API (`/messages/send`) | Microsoft Graph (`/me/sendMail`) |
| Threading | Gmail `threadId` | Graph `conversationId` |
| Sync mechanism | Gmail History API (delta) | Graph Messages API (delta queries) |
| Push notifications | Gmail Push (Pub/Sub) | Microsoft Graph Webhooks (real-time) |
| Token source | `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | `OFFICE365_CLIENT_ID` / `OFFICE365_CLIENT_SECRET` |

Both providers support the same campaign features: sequences, A/B testing, tracking, unsubscribe headers, ramp-up, and reply detection.

---

## Security Notes

- OAuth tokens are stored in the database (same security model as Gmail tokens)
- Client secrets should be kept confidential and rotated periodically
- Use HTTPS in production for all OAuth redirect URIs
- Consider using single-tenant mode if only your organization will connect accounts
