#!/usr/bin/env bash
# E2E boot smoke — the gate that would have caught issue #11 (greenlet
# ImportError crash-loop) and issue #14 (silent MemoryJobStore fallback) before
# they shipped.
#
# Usage: scripts/e2e_boot_smoke.sh [IMAGE]
#   IMAGE defaults to quickly-under-test:local. For release gating pass the
#   freshly pushed tag, e.g. `scripts/e2e_boot_smoke.sh azowail/quickly:v2.6.1`.
#
# What it asserts (against a real container + real PostgreSQL):
#   1. the container survives import and serves the SPA on /   (greenlet class)
#   2. the API layer answers /api/status with 401 unauthenticated
#   3. the scheduler really uses the persistent SQLAlchemyJobStore on
#      PostgreSQL, and its startup log never leaks the DB password
#      (psycopg class)
#
# Exit codes: 0 = all assertions held, 1 = any failed. Always tears the stack
# down. Ports come from docker-compose.e2e.yml (app :8301, db :5451).

set -uo pipefail

IMAGE="${1:-quickly-under-test:local}"
APP_PORT=8301
BASE_URL="http://127.0.0.1:${APP_PORT}"
PROJECT="quickly-e2e"
COMPOSE=(docker compose -f docker-compose.e2e.yml -p "$PROJECT")

export E2E_IMAGE="$IMAGE"

cleanup() {
	"${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

fail() {
	echo "E2E FAIL: $1"
	exit 1
}

echo "E2E: booting $IMAGE against postgres:16 ..."
if ! "${COMPOSE[@]}" up -d --quiet-pull 2>&1 | tail -2; then
	fail "docker compose up failed"
fi

# --- 1. boot: the app must serve the SPA within 120s -------------------------
up=0
for _ in $(seq 1 60); do
	code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 "$BASE_URL/" 2>/dev/null || true)
	if [ "$code" = "200" ] || [ "$code" = "307" ] || [ "$code" = "302" ]; then
		up=1
		break
	fi
	sleep 2
done
if [ "$up" -ne 1 ]; then
	echo "--- app logs (crash likely) ---"
	"${COMPOSE[@]}" logs --no-log-prefix app 2>/dev/null | tail -25
	fail "app never answered GET / (last code: ${code:-none}) — import-time crash?"
fi
echo "E2E: GET / -> $code"

# --- 2. API layer alive and auth-enforced ------------------------------------
code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$BASE_URL/api/status" 2>/dev/null || true)
if [ "$code" != "401" ]; then
	fail "/api/status returned $code, expected 401 unauthenticated"
fi
echo "E2E: /api/status -> 401 (auth enforced)"

# --- 3. scheduler job store is real, and logs are clean ----------------------
logs=$("${COMPOSE[@]}" logs --no-log-prefix app 2>/dev/null)

if echo "$logs" | grep -qi "falling back to MemoryJobStore"; then
	echo "$logs" | grep -i "MemoryJobStore" | tail -3
	fail "scheduler fell back to MemoryJobStore — persistent job store is broken (issue #14 class)"
fi
if ! echo "$logs" | grep -q "using SQLAlchemyJobStore"; then
	echo "$logs" | tail -10
	fail "no 'using SQLAlchemyJobStore' line — scheduler job store not attached?"
fi
if echo "$logs" | grep -qiE "ImportError|ModuleNotFoundError"; then
	echo "$logs" | grep -iE "ImportError|ModuleNotFoundError" | tail -3
	fail "import error in container logs"
fi
echo "E2E: SQLAlchemyJobStore attached"
jobstore_line=$(echo "$logs" | grep "using SQLAlchemyJobStore" | tail -1)
# The URL in the log line must have its password fully masked ("***") or empty.
# A real password is any run of characters that is not '*' between ':' and '@'.
if echo "$jobstore_line" | grep -E "://[^/]+:[^*@]+@" >/dev/null; then
	echo "E2E: $jobstore_line"
	fail "job store log line leaked credentials"
fi
echo "E2E: log line does not leak credentials"

echo "E2E PASS: $IMAGE boots, serves, and uses the persistent job store"
