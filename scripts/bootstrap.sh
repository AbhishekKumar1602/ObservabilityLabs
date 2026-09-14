#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
case "${1:-}" in
  ""|--start) ;;
  *) echo "Usage: $0 [--start]" >&2; exit 2 ;;
esac
for tool in docker python3 curl; do
  command -v "$tool" >/dev/null || { echo "Missing required command: $tool" >&2; exit 1; }
done
docker info >/dev/null 2>&1 || { echo "Docker daemon is unavailable or access is denied." >&2; exit 1; }
docker compose version >/dev/null || { echo "Install Docker Compose v2 (2.24 or newer)." >&2; exit 1; }
for path in app/Dockerfile app/requirements.txt app/requirements-dev.txt app/alembic.ini \
  app/migrations/versions/0001_create_items.py postgres/init.sql \
  config/health/Dockerfile config/otel/otel-collector-config.yaml config/loki/loki-config.yaml \
  config/tempo/tempo.yaml config/pyroscope/pyroscope.yml config/prometheus/prometheus.yml \
  config/prometheus/rules/application.yml config/prometheus/rules/platform.yml \
  config/alertmanager/alertmanager.yml config/alertmanager/templates/default.tmpl \
  config/grafana/provisioning/datasources/datasources.yml \
  config/grafana/provisioning/dashboards/dashboards.yml config/grafana/dashboards/fastapi-overview.json \
  docker-compose.yml .env.example; do
  [[ -s "$path" ]] || { echo "Required file is absent or empty: $path" >&2; exit 1; }
done
[[ -d labs && -d docs && -d app/tests ]] || { echo "Repository directories are incomplete." >&2; exit 1; }
python3 - <<'PYTHON'
import os
import secrets
from pathlib import Path
path = Path('.env')
if path.exists():
    print('Using existing .env; credentials were not changed.')
else:
    content = Path('.env.example').read_text()
    keys = {'POSTGRES_PASSWORD', 'POSTGRES_ADMIN_PASSWORD', 'REDIS_PASSWORD', 'GRAFANA_ADMIN_PASSWORD'}
    content = '\n'.join(
        f'{line.split("=", 1)[0]}={secrets.token_hex(24)}'
        if line.split('=', 1)[0] in keys else line for line in content.splitlines()
    ) + '\n'
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        stream.write(content)
    print('Created private .env with random local credentials.')
PYTHON
docker compose config --quiet
if [[ "${1:-}" == --start ]]; then
  docker compose up --build -d
  echo "Started. Allow initialization, then run make health and make load."
else
  echo "Configuration valid. Start with: docker compose up --build"
fi
