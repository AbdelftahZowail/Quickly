# Release helper — Quickly

**Read this before every release; update it after every release.** Cross-check
against the actual repo state (`git log`, `git tag --sort=-v:refname | head`,
`.github/workflows/release.yml`) — evidence from the repo beats this file.

> **Run releases through the super-vibe skill (ship mode).** This file is the
> repo's release contract; super-vibe supplies the method (recon, scoping,
> independent verification, workstream commits, publish + verify). The mapping
> is: helper 1 = ship recon · helper 2 gates = ship gates **plus** the boot
> smoke and the agentic verification (helper step 4 / `.opencode/release-verify.md`)
> = ship's adversarial verification · helper 3 = ship commits · helper 4–7 =
> ship release mechanics · helper 8 = ship helper-doc step. **Super-vibe does
> not replace any step below** — if a ship run reaches tagging without one of
> these having happened, stop and do it.

> Lives at `.opencode/release-helper.md` on purpose: the **super-vibe** ship
> skill looks for exactly this path.

- **Last released version:** `v2.6.1` (tag `v2.6.1`)
- **Version source of truth:** the **git tag** — there is no version file. The
  tag is passed to the image build as `QUICKLY_VERSION` (build arg) and the app
  strips the leading `v` for the wire format; a source/dev build reports
  `unknown` and therefore never sends telemetry.
- **Registry:** Docker Hub, image `azowail/quickly` (also tagged `latest`).
- **Repo:** `origin` = `https://github.com/AbdelftahZowail/Quickly.git` (branch `main`).
- **Committed:** yes — this file is tracked in the repo as of `v2.5.0`. It was
  an untracked working-tree file before that (the release that added it did not
  commit it), so verify with `git ls-files .opencode/` before trusting this line.

## Credentials

- Docker Hub push access for `azowail/quickly` (CI secret `DOCKERHUB_USERNAME` + token).
- `git push origin main --tags` access.
- The hub write key is a **CI secret** (`QUICKLY_TELEMETRY_KEY`), injected as a
  build arg — never committed.

## Release steps (exact)

From the repo root, on `main`, with a clean release scope:

1. **Recon.** `git status`, `git log --oneline -10`, `git tag --sort=-v:refname | head`.
   Stage **explicit paths** — never `git add -A`.
2. **Gates** (all green):
   - `.venv/bin/python -m pytest tests/test_telemetry.py tests/test_email_html.py tests/test_notices.py tests/test_smtp.py tests/test_schedule_body_resolution.py -q`
   - `cd frontend && npm run build`
   - **Boot smoke (release-blocking, runs inside `release.yml`):** the `smoke`
     job boots the freshly pushed `azowail/quickly:<tag>` against a real
     PostgreSQL via `scripts/e2e_boot_smoke.sh` and fails the release if the
     container doesn't serve, or the scheduler silently falls back to
     MemoryJobStore, or credentials leak into the logs. This gate exists
     because #11 (greenlet, crash-loop) and #14 (psycopg, silent memory
     store) both shipped despite green unit tests. The same script runs on
     every PR/push via `.github/workflows/e2e.yml`.
   - (the full suite has known **pre-existing** failures unrelated to a release —
     auth/env isolation — note them, don't be blocked by them; confirm they also
     fail on a pristine `git archive HEAD` export before blaming the release)
3. **Commit** by workstream (conventional subject + a body enumerating the change
   and the gates). Keep unrelated work in separate commits.
4. **Tag:** `git tag -a v{version}` (semver; annotated, with a summary message
   like the earlier releases).
   ⚠️ **Before tagging: run the agentic verification protocol**
   (`.opencode/release-verify.md`) — spawn an independent brother agent with
   the Verifier prompt and only proceed on `VERDICT: SHIP`. v2.5.1 and v2.6.1
   shipped on the releaser's inline judgment alone; this gate is what catches
   the third bug class ("boots, serves, and is *wrong*").
5. **Push:** `git push origin main` then `git push origin v{version}` — push the
   **specific** tag, **not** `--tags` (see Gotchas: stale local tags get swept in).
   ⚠️ **The tag push DOES trigger the Release workflow now** (observed on v2.5.1
   and v2.6.0; the "pushes are suppressed here" note only held up to v2.5.0). That
   run builds + pushes the image and publishes the release with the **static**
   body. Two ways to publish:
   - *Tag push only:* watch the run, then prepend "What's New" with `gh release
     edit` (step 7) — the release is already published, so omit `--draft=false`.
   - *Explicit dispatch (preferred — gives you a draft to finish before it goes
     live):*
     ```bash
     gh workflow run release.yml --ref main \
       -f tag=v{version} -f push_image=true -f create_release=true
     gh run list --workflow=release.yml --limit 3    # find the tag-push duplicate
     gh run cancel <duplicate-id>                    # cancel it NOW, before `merge`
     gh run watch <dispatch-id> --exit-status
     ```
     Dispatch creates the GitHub Release as a **draft** (`release` `needs: merge`,
     which needs `push_image=true`).
   ⚠️ **Never leave both runs alive** — they build the same image and race on the
   manifest push in `merge`.
6. **Verify it landed:** Docker Hub shows the new tags
   (`docker buildx imagetools inspect azowail/quickly:v{version}` and `:latest`
   must share the same digest); the GitHub Release + tag page resolve
   (`git ls-remote --tags origin v{version}`). The `.env.example` asset is
   uploaded as **`default.env.example`** by `ncipollo/release-action`.
7. **Write the "What's New" — REQUIRED, not optional.** The GitHub Release body is
   what feeds the hub's version feed, which is what users see in the in-app
   **notice card** and **Settings**. The workflow ships a **static** body (Docker
   image + setup options) — it is identical every release, so it is **not** a
   changelog. `gh release edit <tag> --notes-file FILE` **replaces** the whole
   body, so concatenate instead of clobbering: keep the static Setup Options and
   put the short user-facing "What's New" on top, e.g.
   ```bash
   gh release view v{version} --json body -q .body > /tmp/body.md
   { cat /tmp/whats-new.md; echo; echo '---'; echo; cat /tmp/body.md; } > /tmp/combined.md
   # workflow_dispatch leaves the release a DRAFT: publish it in the same edit.
   gh release edit v{version} --notes-file /tmp/combined.md --draft=false --latest
   ```
   (or edit the release on github.com). See `## "What's New" is a release artifact`.
8. **Update this file** (last version in the header + a new entry under `## Releases`).

## "What's New" is a release artifact (the hub feed)

The in-app notice card and settings read the hub version feed
(`GET /v1/versions?product=quickly`). The hub sources it from **GitHub Releases**
(the hub's `HUB_GITHUB_REPO` / `HUB_GITHUB_REPOS`). A release body containing
`<!-- security -->` is flagged security-critical and surfaced **more loudly** by
the client — use it for security releases.

Consequence: **the release's notes ARE the product announcement.** So every
release gets a short, user-facing "What's New" (3–6 bullets: what's new, what
changed, anything breaking). There is no `CHANGELOG.md` to maintain — the GitHub
Release is the source of truth.

## Gotchas

- **No version file.** The tag is the version; `QUICKLY_VERSION` is injected at
  build time. Bumping a file is neither needed nor correct.
- **Dev DB exposure.** The dev compose publishes Postgres on `0.0.0.0` with
  default creds — development only; do not treat it as production. Ports:
  `docker-compose.dev.yml` → `5433`; `docker-compose-not-host.dev.yml` and
  `docker-compose.no-caddy.dev.yml` → `5434`.
- **`brag-output/`-style media must never be committed.** Stage explicit paths.
- **`app/database.py` owns migrations** (`Base.metadata.create_all`; new columns
  are added with idempotent `ALTER TABLE … ADD COLUMN IF NOT EXISTS`). Any schema
  change ships in the release commit.
- ⚠️ **A tag push DOES trigger the Release workflow (as of v2.5.1).** The older
  "pushes are suppressed in this environment" behavior only held up to v2.5.0;
  both v2.5.1 and v2.6.0 produced a `push`-triggered run alongside the dispatch.
  If you dispatch as well, cancel one of the two **immediately** — they race on
  the manifest push in `merge`. (v2.6.0 kept the dispatch run, so the release
  started as a draft, and cancelled the push run.)
- ⚠️ **Use `git push origin v{version}`, not `--tags`.** This checkout carried
  local-only older tags; `--tags` swept them onto the remote (v2.5.0: v1.0.7,
  v1.1.0–v1.1.3, v1.1.5, v1.1.6). Harmless here (their GitHub Releases already
  existed and nothing triggered), but avoid it.
- **Hidden assets get renamed.** `ncipollo/release-action` uploads `.env.example`
  as `default.env.example`.
- **The telemetry key is baked into the public image by design** (`Dockerfile`
  `ENV QUICKLY_TELEMETRY_KEY`), so `docker`'s `SecretsUsedInArgOrEnv` build
  warnings (`Dockerfile#59`, `#60`) are expected — it is a low-privilege
  ingestion filter, not a credential.

## Releases

- **v2.6.1** (2026-10-28) — fix(scheduler): name the sync job-store driver
  explicitly (#14): since SQLAlchemy 2.1 a bare `postgresql://` URL defaults to
  the psycopg (v3) driver (not installed), so SQLAlchemyJobStore failed to
  build and APScheduler silently fell back to MemoryJobStore on every
  PostgreSQL deployment; also stopped logging `user:password` from the sync
  URL, and added `tests/test_scheduler_jobstores.py` (engine construction
  needs no database). Reproduced in a venv before fixing, like v2.5.1's
  greenlet fix. Dispatch duplication hit AGAIN (one `gh workflow run` call →
  two runs 2s apart; duplicate `40689680453` cancelled, keeper `40689676399`).
  Published via explicit dispatch. Image `azowail/quickly:v2.6.1` == `:latest`,
  digest `sha256:938c284a…`;
  release https://github.com/AbdelftahZowail/Quickly/releases/tag/v2.6.1.
- **v2.6.0** (2026-10-28) — paginated lead management (#13, carried from #8): the
  Leads page and a campaign's Leads tab paginate server-side (100/page) with search
  over email/name/company/domain, "select all N matching" with per-lead exclusions,
  and bulk remove-from-campaign / bulk delete. Ships @tuancrabit's contribution
  unchanged plus fixes for the three blocking review issues (broken CSV export,
  select-all bulk actions that no-opped, campaign select-all removing leads outside
  the active filter — now fails closed with a 400). Gates: 101 targeted tests
  passed, `vite build` clean. Tag push triggered a run AND the dispatch triggered
  one; the push run was cancelled to avoid the manifest race, the dispatch run
  created a draft, then `gh release edit --draft=false --latest` added "What's New"
  and published. Image `azowail/quickly:v2.6.0` == `:latest`, digest
  `sha256:6582103e…`; release
  https://github.com/AbdelftahZowail/Quickly/releases/tag/v2.6.0.
- **v2.5.1** (2026-10-28) — fix(deps): `sqlalchemy[asyncio]` so `greenlet` ships
  in fresh builds — `v2.5.0`/`latest` images crash-looped at startup with
  `ImportError: The SQLAlchemy asyncio module requires that the Python 'greenlet'
  library is installed` (SQLAlchemy 2.1.x no longer installs greenlet
  implicitly). #11 by fsioni, merged after local repro of the failure and the
  fix; the CLA workflow/agreement/store + its bot comments were removed the
  same day, so the PR merged despite an unrecorded signature. Published via
  `gh workflow run` (tag push did not trigger Actions); image
  `azowail/quickly:v2.5.1` == `:latest`, digest `sha256:ff227c84…`;
  release https://github.com/AbdelftahZowail/Quickly/releases/tag/v2.5.1.
  ⚠️ One `gh workflow run` call dispatched THREE concurrent duplicate runs —
  cancel extras fast (`gh run cancel`), duplicate builds race on the manifest
  push in `merge`.
- **v2.5.0** (2026-10-18) — anonymous opt-out usage telemetry (heartbeat + hub),
  in-app notices/version check proxying the public hub feeds, HTML email-spacing
  normalisation shared by previews/sends, `{{random}}` template variable, and a
  test-email provider-credential fix. Also committed this helper doc.
  Published via `gh workflow run` (tag push did not trigger Actions); image
  `azowail/quickly:v2.5.0` == `:latest`, digest `sha256:544e80c0…`;
  release https://github.com/AbdelftahZowail/Quickly/releases/tag/v2.5.0.
