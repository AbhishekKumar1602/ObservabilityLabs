#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
for tool in curl python3; do
  command -v "$tool" >/dev/null || { echo "Missing command: $tool" >&2; exit 1; }
done
BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
DURATION_SECONDS="${DURATION_SECONDS:-60}"
[[ "$DURATION_SECONDS" =~ ^[0-9]{1,3}$ ]] && ((10#$DURATION_SECONDS >= 1 && 10#$DURATION_SECONDS <= 300)) || {
  echo "DURATION_SECONDS must be an integer from 1 to 300." >&2; exit 2;
}
DURATION_SECONDS=$((10#$DURATION_SECONDS))
BASE_URL="${BASE_URL%/}"
item_id=''
cleanup() {
  if [[ -n "$item_id" ]]; then
    curl --silent --max-time 5 -X DELETE "$BASE_URL/api/v1/items/$item_id" >/dev/null || true
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
request() { curl --fail --silent --show-error --connect-timeout 2 --max-time 10 "$@"; }
request "$BASE_URL/health/ready" >/dev/null
start=$SECONDS
cycles=0
while (( SECONDS - start < DURATION_SECONDS )); do
  created=$(request -H 'Content-Type: application/json' -H 'X-Request-ID: learning-load' \
    -d '{"name":"Load sample","description":"Temporary learning traffic","price":"12.50","is_active":true}' \
    "$BASE_URL/api/v1/items")
  item_id=$(python3 -c 'import json,sys; from uuid import UUID; print(UUID(json.load(sys.stdin)["id"]))' <<<"$created")
  for hit in 1 2 3; do request "$BASE_URL/api/v1/items/$item_id" >/dev/null; done
  request "$BASE_URL/api/v1/items?limit=10" >/dev/null
  request -X PUT -H 'Content-Type: application/json' \
    -d '{"name":"Updated load sample","description":"Cache invalidation exercise","price":"13.75","is_active":true}' \
    "$BASE_URL/api/v1/items/$item_id" >/dev/null
  request "$BASE_URL/api/v1/items/$item_id" >/dev/null
  demo_status=$(curl --silent --show-error --connect-timeout 2 --max-time 10 -o /dev/null -w '%{http_code}' \
    "$BASE_URL/api/v1/demo/work?iterations=1000000&delay_ms=10")
  case "$demo_status" in 200|404|429) ;; *) echo "Demo returned HTTP $demo_status" >&2; exit 1 ;; esac
  request -X DELETE "$BASE_URL/api/v1/items/$item_id" >/dev/null
  item_id=''
  cycles=$((cycles + 1))
  sleep 0.2
done
printf 'Completed %s sequential CRUD cycles in %s seconds. Allow 30 seconds for scrape and profile upload.\n' "$cycles" "$((SECONDS-start))"
