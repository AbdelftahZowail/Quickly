# Release helper — Quickly

**Read this before every release; update it after every release.** Cross-check
against the actual repo state (`git log`, `git tag --sort=-v:refname | head`,
`.github/workflows/release.yml`) — evidence from the repo beats this file.

> Lives at `.opencode/release-helper.md` on purpose: the **super-vibe** ship
> skill looks for exactly this path.

- **Last released version:** `v2.5.0` (tag `v2.5.0`)
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
   - (the full suite has known **pre-existing** failures unrelated to a release —
     auth/env isolation — note them, don't be blocked by them; confirm they also
     fail on a pristine `git archive HEAD` export before blaming the release)
3. **Commit** by workstream (conventional subject + a body enumerating the change
   and the gates). Keep unrelated work in separate commits.
4. **Tag:** `git tag v{version}` (semver).
5. **Push:** `git push origin main --tags` (never force). Pushing the tag runs
   `.github/workflows/release.yml`: it builds + pushes the multi-arch Docker
   image(s) and creates a **GitHub Release** with the compose files / `.env.example`
   / Caddyfile attached. A `workflow_dispatch` run only creates a **draft** when
   **both** `push_image` and `create_release` are checked — `release` `needs: merge`
   and `merge` is skipped unless `push_image` is true, so defaults skip it.
6. **Verify it landed:** Docker Hub shows the new tags
   (`docker buildx imagetools inspect azowail/quickly:v{version}`); the GitHub
   Release + tag page resolve.
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
   gh release edit v{version} --notes-file /tmp/combined.md
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

## Releases

- **v2.5.0** — anonymous opt-out usage telemetry (heartbeat + hub), in-app
  notices/version check proxying the public hub feeds, HTML email-spacing
  normalisation shared by previews/sends, `{{random}}` template variable, and a
  test-email provider-credential fix. Committed the release-helper doc itself.
