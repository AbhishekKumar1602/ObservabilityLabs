# Source from Bash; this is a local lab helper, not the application's environment file.
set -o pipefail
LAB_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export APP_URL="${APP_URL:-http://127.0.0.1:8000}"

dc() {
  docker compose --project-directory "$LAB_ROOT" --env-file "$LAB_ROOT/.env" \
    -f "$LAB_ROOT/docker-compose.yml" \
    -f "$LAB_ROOT/lab-notes/compose.baseline.yaml" "$@"
}

api() {
  curl --connect-timeout 2 --max-time 15 "$@"
}

wait_ready() {
  local attempt
  for attempt in {1..45}; do
    if curl --connect-timeout 1 --max-time 5 -fsS "$APP_URL/health/ready" 2>/dev/null \
      | jq -e '.status == "ready"' >/dev/null 2>&1; then
      echo "Application and both dependencies are ready"
      return 0
    fi
    sleep 1
  done
  echo "Readiness deadline exceeded; inspect dc ps -a and dependency logs" >&2
  return 1
}

wait_live() {
  local attempt
  for attempt in {1..45}; do
    if curl --connect-timeout 1 --max-time 3 -fsS "$APP_URL/health/live" \
      >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "Liveness deadline exceeded" >&2
  return 1
}

load_app_settings() {
  local settings
  settings="$(dc exec -T app python -c \
    'from app.config import Settings; s=Settings(); print(s.service_name, s.environment, s.redis_db, s.cache_ttl_seconds)')" || return 1
  read -r LAB_SERVICE LAB_ENVIRONMENT LAB_REDIS_DB LAB_CACHE_TTL <<<"$settings"
  export LAB_SERVICE LAB_ENVIRONMENT LAB_REDIS_DB LAB_CACHE_TTL
}

assert_baseline() {
  local actual
  actual="$(dc ps --services --status running | sort)" || return 1
  if [[ "$actual" != $'app\npostgres\nredis' ]]; then
    printf 'Unexpected running services:\n%s\n' "$actual" >&2
    return 1
  fi
}

baseline_check() {
  wait_ready && load_app_settings && assert_baseline
}

dbsql() {
  dc exec -T postgres sh -c \
    'exec psql -X -v ON_ERROR_STOP=1 -U postgres -d "$APP_DB_NAME" "$@"' sh "$@"
}

rcli() {
  : "${LAB_REDIS_DB:?Run load_app_settings first}"
  dc exec -T redis sh -c \
    'export REDISCLI_AUTH="$REDIS_PASSWORD"; selected_db="$1"; shift; exec redis-cli -n "$selected_db" --raw "$@"' \
    sh "$LAB_REDIS_DB" "$@"
}

cache_key() {
  : "${LAB_SERVICE:?Run load_app_settings first}"
  : "${LAB_ENVIRONMENT:?Run load_app_settings first}"
  printf '%s:%s:items:v1:%s' "$LAB_SERVICE" "$LAB_ENVIRONMENT" "$1"
}

new_uuid() {
  python3 -c 'from uuid import uuid4; print(uuid4())'
}
