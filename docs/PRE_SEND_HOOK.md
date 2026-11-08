# Pre-send decision hook

Right before Quickly sends the first email of a sequence to a lead, it can ask one HTTPS endpoint of your choosing whether to go ahead. The endpoint answers **allow**, **hold** or **deny**. Quickly does not know or care who runs it: a compliance checker, your CRM, a do-not-contact list, a 20-line script of your own.

The hook is **off by default** and **fails open by default**: until you set a URL nothing leaves Quickly, and a hook that is down, slow or broken never stops sending unless you ask it to.

---

## Configuration

Set these in `.env` (they win) or as `app_setting` rows with the same names in lower case (`pre_send_hook_url` and so on).

| Variable | Default | Meaning |
|---|---|---|
| `PRE_SEND_HOOK_URL` | empty (off) | The endpoint. HTTPS only, except plain HTTP to `localhost` for development. |
| `PRE_SEND_HOOK_SECRET` | empty | Shared secret. When set, every request is signed (see below) and a signed response is verified. |
| `PRE_SEND_HOOK_TIMEOUT` | `3` | Seconds to wait for an answer, 1 to 10. |
| `PRE_SEND_HOOK_ON_ERROR` | `allow` | What a missing or broken answer means: `allow` sends and logs a warning (fail open); `hold` keeps the lead's queue slot and asks again at the next scan. |
| `PRE_SEND_HOOK_FIELDS` | `minimal` | What the request carries. `minimal`: the lead's address and the campaign. `contact`: also the lead's name and `custom_data` (strings, numbers and booleans only). |
| `PRE_SEND_HOOK_SCOPE` | `first` | `first`: only the first email of a sequence to a lead. `all`: every send, including follow-ups. |

Test-mode sends never call the hook.

---

## The request

One `POST` per decision, JSON body:

```json
{
  "event": "pre_send",
  "version": 1,
  "timestamp": "2026-11-07T15:04:05.123456Z",
  "lead": { "id": 42, "email": "jane@acme.co.uk" },
  "campaign": { "id": 5, "public_id": "k3j9x2m1", "name": "November outreach" },
  "sequence_index": 0
}
```

With `PRE_SEND_HOOK_FIELDS=contact`, `lead` also has `name` and `custom_data`.

Headers:

```
Content-Type: application/json
User-Agent: Quickly-PreSendHook/1
X-Quickly-Event: pre_send
X-Quickly-Timestamp: 1762527845
X-Quickly-Signature: v1=<hex HMAC-SHA256>      (only when a secret is set)
```

The signature is `HMAC-SHA256(secret, "<X-Quickly-Timestamp>.<raw request body>")`, hex encoded. Verify it before trusting a request, and reject timestamps older than a few minutes if replay matters to you.

---

## The response

Answer `200` with JSON:

```json
{ "decision": "allow" }
```

| Field | Required | Meaning |
|---|---|---|
| `decision` | yes | `allow`, `hold` or `deny`. |
| `reason` | no | One line, shown on the lead and in logs (300 characters at most). |
| `reference` | no | Your id for this decision, for example a receipt or ticket id (120 characters at most). Kept on the lead and sent in the `lead.status_changed` webhook on a deny. |
| `retry_after_seconds` | no | For `hold` only: when to ask again. Default 6 hours, maximum 7 days. |

What Quickly does with each decision:

- **allow**: the email is sent. The decision is reused for the same lead and hook for 30 days, so a lead enrolled in a second campaign is not asked about again.
- **hold**: nothing is sent now. The lead keeps its queue slot and is asked about again after `retry_after_seconds`. Use this for "not yet" answers (a lookup still running, a fact you need first).
- **deny**: nothing is sent. The enrolment is paused, its queue slots are dropped, and a `lead.status_changed` webhook fires with `reason`, `pre_send_decision` and `pre_send_reference`. The lead is otherwise untouched, so you can review and resume it. A deny is also reused for 30 days.

Anything else (a non-200 status, a timeout, invalid JSON, an unknown decision, a bad signature) is an **error** and follows `PRE_SEND_HOOK_ON_ERROR`. Errors are never cached, so the next scan asks again.

Responses may be signed: send `X-Hook-Signature: v1=<hex HMAC-SHA256(secret, raw response body)>`. Quickly verifies it when a secret is set and treats a wrong signature as an error, so a forged deny cannot stop sending and a forged allow is ignored under `PRE_SEND_HOOK_ON_ERROR=hold`.

---

## What is recorded

Each lead gets `pre_send_decision` (`allow`, `hold`, `deny`, `error_allow` or `error_hold`) and `pre_send_check` (`reason`, `reference`, `error`, `checked_at`, `retry_at`, `host`, `fields`, `sequence_index`). Both are returned by the leads API.

---

## A minimal hook of your own

A do-not-contact list in 20 lines (FastAPI, Python 3.11+):

```python
import hashlib, hmac, os
from fastapi import FastAPI, Header, HTTPException, Request

SECRET = os.environ["HOOK_SECRET"].encode()
BLOCKED_DOMAINS = {"competitor.example", "nhs.uk"}
app = FastAPI()

@app.post("/pre-send")
async def pre_send(request: Request, x_quickly_timestamp: str = Header(), x_quickly_signature: str = Header()):
    body = await request.body()
    expected = "v1=" + hmac.new(SECRET, x_quickly_timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, x_quickly_signature):
        raise HTTPException(401)
    email = (await request.json())["lead"]["email"].lower()
    if email.rsplit("@", 1)[-1] in BLOCKED_DOMAINS:
        return {"decision": "deny", "reason": "Domain is on our do-not-contact list"}
    return {"decision": "allow"}
```

---

## Providers that speak this contract

Any HTTPS endpoint can. Known ones:

- **Precleared** (precleared.dev), a per-recipient cold-email legality check (UK, US, Canada, Germany, France, Australia, Ireland, the Netherlands, Spain, Italy, Latvia, the UAE, Egypt and Qatar). One call with your Precleared key, `curl -s https://precleared.dev/api/v1/hooks/pre-send -H "Authorization: Bearer YOUR_KEY"`, returns the URL and a hook-specific secret for `PRE_SEND_HOOK_URL` and `PRE_SEND_HOOK_SECRET`, so the key itself never goes into Quickly's settings. Set `PRE_SEND_HOOK_FIELDS=contact` so it sees the lead's name and company, and `PRE_SEND_HOOK_TIMEOUT=10`, because a UK company lookup can take several seconds. `ok_to_email` comes back as allow, `consent_needed` and `blocked` as deny with the rule in `reason` and a signed receipt id in `reference`, and `unknown` as hold. Without a key, `PRE_SEND_HOOK_URL=https://precleared.dev/api/v1/hooks/pre-send` uses its free allowance. Setup guide: https://precleared.dev/docs/quickly. Disclosure: this hook was contributed by Precleared's maintainer. Quickly has no dependency on it and sends nothing to it unless you point the URL there.

If you run a hook others could use, add it here with a pull request.
