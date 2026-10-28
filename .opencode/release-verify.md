# Release verification protocol — Quickly

**This is a mandatory gate, not a suggestion.** No tag is pushed and no release
is published until an independent agent has run this protocol and returned
`SHIP`. It exists because deterministic tests are not enough: #11 (greenlet)
and #14 (psycopg) both passed every unit test and shipped broken — one
crash-looped at import, one silently degraded the scheduler. A reviewer with
fresh eyes and a running container catches the third category: "it boots, it
serves, and it's *wrong*".

## Roles

- **Releaser** (primary agent or human): prepares the change set, drafts the
  What's New, runs the deterministic gates. May NOT be the verifier.
- **Verifier** (brother agent, fresh context): executes this protocol
  end-to-end and returns a verdict. Must be independent — it does not inherit
  the releaser's assumptions or conversation.

## Stages

1. **Deterministic gates** (releaser runs, verifier re-checks):
   - unit subset: `pytest tests/test_telemetry.py tests/test_email_html.py tests/test_notices.py tests/test_smtp.py tests/test_schedule_body_resolution.py -q`
   - frontend build: `cd frontend && npm run build`
   - boot smoke: `bash scripts/e2e_boot_smoke.sh <image>` (see
     `.opencode/release-helper.md`; known pre-existing `test_schedule_api`
     failures are documented — verify against a pristine HEAD export before
     failing a release over them)
2. **Change review** (verifier): `git log --oneline vX.Y.Z..HEAD` and the
   diffs of anything runtime-touching. Flag: schema changes without
   `Base.metadata.create_all` / `ALTER TABLE` handling, new required env vars
   not documented in `.env.example`, dependency edits, auth/security surface
   changes, redaction/logging of secrets.
3. **Live functional probes** (verifier): boot the image (reuse the smoke
   stack: `docker compose -f docker-compose.e2e.yml`), then exercise beyond
   the smoke script: SPA HTML contains the app mount + asset links and those
   assets return 200, unauthenticated API returns 401 (not 500), logs contain
   no tracebacks, no unexpected ERROR/WARNING lines beyond documented ones,
   scheduler uses the persistent job store, and the container's
   `QUICKLY_VERSION` matches the intended tag.
4. **Notes audit** (verifier): the drafted What's New must match reality —
   every bullet traceable to a commit; no user-visible change unmentioned;
   correct version number and issue links.
5. **Verdict**: `SHIP` or `BLOCK` (with the blocking reasons, each with
   evidence). `BLOCK` is final for this attempt; the releaser fixes and
   re-runs the protocol.

## Brother-agent invocation (for the releaser)

```
brother_agent(
  task = <contents of "Verifier prompt" below, with placeholders filled>,
  waitForFinish = true, returnLastOnly = true
)
```

## Verifier prompt (fill ⟨…⟩ before launching)

You are the independent release verifier for the Quickly repo at
/home/ubuntu/Quickly. The releaser proposes to release tag `⟨vX.Y.Z⟩` from
HEAD. Your job is to SHIP it or BLOCK it. Do not trust the releaser's
claims — verify everything yourself.

Change set: commits between `⟨vX.Y.Z⟩` and HEAD
(`git log --oneline ⟨vX.Y.Z⟩..HEAD` plus diffs; ignore the docs/release-helper
commit if present).

1. Read `.opencode/release-verify.md` and follow stages 2–4.
2. Deterministic: run the unit subset with `.venv/bin/python -m pytest …`
   and the frontend build. Boot smoke: build
   `docker build -t verify-under-test:local .` then
   `bash scripts/e2e_boot_smoke.sh verify-under-test:local`.
   (Known pre-existing `test_schedule_api` failures are documented in
   `.opencode/release-helper.md` — confirm they fail identically on a
   pristine `git archive HEAD` export before treating them as blockers.)
3. Live probes beyond the smoke script, per stage 3.
4. Audit this What's New draft against the actual commits:
   ```
   ⟨what's new draft⟩
   ```
5. Reply with EXACTLY this format and nothing else:
   `VERDICT: SHIP` or `VERDICT: BLOCK`
   Then one bullet per finding: `[BLOCKER|WARN|OK] <finding> — <evidence>`.
