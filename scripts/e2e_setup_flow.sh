#!/usr/bin/env bash
# E2E setup flow — the gate for the printed setup code.
#
# Usage: scripts/e2e_setup_flow.sh [IMAGE]
#   IMAGE defaults to quickly-under-test:local.
#
# What it asserts, against a real container + real PostgreSQL:
#   1. first boot logs a setup code and setup-status never returns it
#   2. no first admin and no pre-setup restore without that code
#   3. the code works for register + OAuth authorize + restore metadata
#   4. the first admin can log in by email and by username
#   5. a restart does not lock the admin out
#   6. the code is dead after setup, including the old restore path
#   7. a real .qbk backup can be restored before setup with a fresh code,
#      and the restored admin can log in
#
# Ports come from docker-compose.e2e.yml (app :8301, db :5451).

set -uo pipefail

IMAGE="${1:-quickly-under-test:local}"
APP_PORT=8301
BASE_URL="http://127.0.0.1:${APP_PORT}"
PROJECT="quickly-e2e-setup"
COMPOSE=(docker compose -f docker-compose.e2e.yml -p "$PROJECT")

export E2E_IMAGE="$IMAGE"

ADMIN_USER="e2eadmin"
ADMIN_EMAIL="e2e@example.com"
ADMIN_PASS="E2ePassword123"

fail() {
	echo "E2E SETUP FAIL: $1"
	echo "--- app logs ---"
	"${COMPOSE[@]}" logs --no-log-prefix app 2>/dev/null | tail -30 || true
	exit 1
}

cleanup() {
	"${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

log() { echo "E2E: $*"; }

wait_app() {
	local code=""
	for _ in $(seq 1 60); do
		code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 "$BASE_URL/" 2>/dev/null || true)
		if [ "$code" = "200" ] || [ "$code" = "307" ] || [ "$code" = "302" ]; then
			return 0
		fi
		sleep 2
	done
	fail "app never answered GET / (last code: ${code:-none})"
}

http_code() {
	# Writes the body to /tmp/e2e-body.json and prints the status code.
	curl -s -o /tmp/e2e-body.json -w "%{http_code}" --max-time 30 "$@"
}

json_py() {
	python3 -c "$1" < /tmp/e2e-body.json
}

setup_code_from_logs() {
	"${COMPOSE[@]}" logs --no-log-prefix app 2>/dev/null \
		| grep -oE 'Setup code: [A-Z0-9]{4}-[A-Z0-9]{4}' \
		| tail -1 | awk '{print $NF}'
}

boot_fresh() {
	"${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
	if ! "${COMPOSE[@]}" up -d --quiet-pull 2>&1 | tail -2; then
		fail "docker compose up failed"
	fi
	wait_app
}

# ---------------------------------------------------------------------------
# Phase 1: fresh install, gates closed until the printed code is used
# ---------------------------------------------------------------------------

log "booting $IMAGE against postgres:16 (fresh volume)"
boot_fresh

CODE=$(setup_code_from_logs)
[ -n "$CODE" ] || fail "no setup code in the startup logs"
log "setup code printed: $CODE"

code=$(http_code "$BASE_URL/api/auth/setup-status") || fail "setup-status request failed"
[ "$code" = "200" ] || fail "setup-status returned $code"
json_py 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get("setup_complete") is False and d.get("setup_code_required") is True else 1)' \
	|| fail "setup-status shape is wrong before setup"
grep -q "$CODE" /tmp/e2e-body.json && fail "setup-status leaked the setup code"
log "setup-status: incomplete, code required, code not returned"

code=$(http_code -X POST -H "Content-Type: application/json" \
	-d '{"code":"WRONG-CODE"}' "$BASE_URL/api/auth/setup-code/verify")
[ "$code" = "403" ] || fail "setup-code verify with a wrong code returned $code, expected 403"
code=$(http_code -X POST -H "Content-Type: application/json" \
	-d "{\"code\":\"$CODE\"}" "$BASE_URL/api/auth/setup-code/verify")
[ "$code" = "200" ] || fail "setup-code verify with the right code returned $code, expected 200"
json_py 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("ok") is True else 1)' \
	|| fail "setup-code verify did not return ok"
log "setup-code verify: wrong code 403, right code 200 (nothing else shown before this)"

code=$(http_code "$BASE_URL/oauth/app/google/authorize")
[ "$code" = "403" ] || fail "Google authorize without code returned $code, expected 403"
code=$(http_code "$BASE_URL/oauth/app/google/authorize?setup_code=WRONG-CODE")
[ "$code" = "403" ] || fail "Google authorize with wrong code returned $code, expected 403"
code=$(http_code "$BASE_URL/oauth/app/google/authorize?setup_code=$CODE")
[ "$code" = "302" ] || [ "$code" = "307" ] || fail "Google authorize with the code returned $code, expected a redirect"
log "OAuth first-user gate: no code 403, wrong code 403, right code redirects"

dd if=/dev/urandom of=/tmp/e2e-not-a-backup.qbk bs=64 count=1 status=none
code=$(http_code -F "file=@/tmp/e2e-not-a-backup.qbk" "$BASE_URL/api/auth/restore-setup/metadata")
[ "$code" = "403" ] || fail "restore metadata without code returned $code, expected 403"
code=$(http_code -F "file=@/tmp/e2e-not-a-backup.qbk" -F "setup_code=WRONG-CODE" "$BASE_URL/api/auth/restore-setup/metadata")
[ "$code" = "403" ] || fail "restore metadata with wrong code returned $code, expected 403"
code=$(http_code -F "file=@/tmp/e2e-not-a-backup.qbk" -F "setup_code=$CODE" "$BASE_URL/api/auth/restore-setup/metadata")
[ "$code" = "400" ] || fail "restore metadata with the code returned $code, expected 400 (invalid backup, gate passed)"
log "pre-setup restore gate: no/wrong code 403, right code reaches file validation"

code=$(http_code -X POST -H "Content-Type: application/json" \
	-d "{\"username\":\"$ADMIN_USER\",\"email\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PASS\"}" \
	"$BASE_URL/api/auth/register")
[ "$code" = "403" ] || fail "register without code returned $code, expected 403"
code=$(http_code -X POST -H "Content-Type: application/json" \
	-d "{\"username\":\"$ADMIN_USER\",\"email\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PASS\",\"setup_code\":\"WRONG-CODE\"}" \
	"$BASE_URL/api/auth/register")
[ "$code" = "403" ] || fail "register with wrong code returned $code, expected 403"
code=$(http_code -X POST -H "Content-Type: application/json" \
	-d "{\"username\":\"$ADMIN_USER\",\"email\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PASS\",\"setup_code\":\"$CODE\"}" \
	"$BASE_URL/api/auth/register")
[ "$code" = "201" ] || fail "register with the code returned $code, expected 201"
log "first admin created with the printed code"

code=$(http_code -X POST -H "Content-Type: application/json" \
	-d "{\"username\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PASS\"}" \
	"$BASE_URL/api/auth/login")
[ "$code" = "200" ] || fail "login by email returned $code, expected 200"
TOKEN=$(json_py 'import json,sys; print(json.load(sys.stdin)["access_token"])')
[ -n "$TOKEN" ] || fail "login did not return an access token"

code=$(http_code -H "Authorization: Bearer $TOKEN" "$BASE_URL/api/auth/me")
[ "$code" = "200" ] || fail "GET /api/auth/me returned $code, expected 200"
log "login by email works"

# ---------------------------------------------------------------------------
# Phase 2: restart must not lock the admin out
# ---------------------------------------------------------------------------

log "restarting app container"
"${COMPOSE[@]}" restart app >/dev/null 2>&1 || fail "docker compose restart failed"
wait_app

code=$(http_code -X POST -H "Content-Type: application/json" \
	-d "{\"username\":\"$ADMIN_USER\",\"password\":\"$ADMIN_PASS\"}" \
	"$BASE_URL/api/auth/login")
[ "$code" = "200" ] || fail "login by username after restart returned $code, expected 200"
TOKEN=$(json_py 'import json,sys; print(json.load(sys.stdin)["access_token"])')
log "login by username still works after restart (no lockout)"

code=$(http_code "$BASE_URL/api/auth/setup-status")
[ "$code" = "200" ] || fail "setup-status after setup returned $code"
json_py 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get("setup_complete") is True and d.get("setup_code_required") is False else 1)' \
	|| fail "setup-status shape is wrong after setup"

code=$(http_code -X POST -H "Content-Type: application/json" \
	-d "{\"username\":\"$ADMIN_USER\",\"email\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PASS\",\"setup_code\":\"$CODE\"}" \
	"$BASE_URL/api/auth/register")
[ "$code" = "403" ] || fail "register after setup returned $code, expected 403"

code=$(http_code -F "file=@/tmp/e2e-not-a-backup.qbk" -F "setup_code=$CODE" "$BASE_URL/api/auth/restore-setup/metadata")
[ "$code" = "403" ] || fail "pre-setup restore after setup returned $code, expected 403"

code=$(http_code -X POST -H "Content-Type: application/json" \
	-d "{\"code\":\"$CODE\"}" "$BASE_URL/api/auth/setup-code/verify")
[ "$code" = "403" ] || fail "setup-code verify after setup returned $code, expected 403"

code=$(http_code "$BASE_URL/oauth/app/google/authorize")
[ "$code" = "302" ] || [ "$code" = "307" ] || fail "OAuth authorize after setup returned $code, expected a redirect (code ignored)"
log "setup code is dead after setup; normal OAuth/login still work"

# ---------------------------------------------------------------------------
# Phase 3: a real backup can still be restored before setup, with the new code
# ---------------------------------------------------------------------------

log "downloading a real .qbk backup"
curl -sS -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
	-d '{"encrypt":false}' --max-time 120 \
	"$BASE_URL/api/settings/backup/download" -o /tmp/e2e-backup.qbk \
	|| fail "backup download failed"
[ "$(wc -c < /tmp/e2e-backup.qbk)" -gt 1000 ] || fail "downloaded backup is unexpectedly small"

log "rebooting with a clean database and a new setup code"
boot_fresh
CODE2=$(setup_code_from_logs)
[ -n "$CODE2" ] || fail "no setup code after the second boot"
[ "$CODE2" != "$CODE" ] || fail "setup code did not rotate after a fresh boot"

code=$(http_code -F "file=@/tmp/e2e-backup.qbk" -F "setup_code=$CODE2" "$BASE_URL/api/auth/restore-setup/metadata")
[ "$code" = "200" ] || fail "restore metadata with the new code returned $code, expected 200"

code=$(http_code -F "file=@/tmp/e2e-backup.qbk" -F "setup_code=$CODE2" "$BASE_URL/api/auth/restore-setup/preview")
[ "$code" = "200" ] || fail "restore preview returned $code, expected 200"
RESTORE_TOKEN=$(json_py 'import json,sys; print(json.load(sys.stdin)["restore_token"])')
[ -n "$RESTORE_TOKEN" ] || fail "restore preview did not return a token"

code=$(http_code -X POST -H "Content-Type: application/json" \
	-d "{\"restore_token\":\"$RESTORE_TOKEN\",\"setup_code\":\"$CODE2\"}" \
	"$BASE_URL/api/auth/restore-setup/execute")
[ "$code" = "200" ] || fail "restore execute returned $code, expected 200"
wait_app

code=$(http_code -X POST -H "Content-Type: application/json" \
	-d "{\"username\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PASS\"}" \
	"$BASE_URL/api/auth/login")
[ "$code" = "200" ] || fail "login after restore returned $code, expected 200"
log "restore completed and the restored admin can log in"

echo "E2E SETUP PASS: printed code gates setup/restore; no lockout; restore works"
