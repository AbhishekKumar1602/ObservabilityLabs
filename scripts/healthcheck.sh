#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
command -v docker >/dev/null || { echo "Docker is required." >&2; exit 1; }
# Use the app's Python and Docker DNS; internal services need no host ports.
docker compose exec -T app python - <<'PYTHON'
import json
import sys
import urllib.error
import urllib.request
checks = {
    'app live': 'http://app:8000/health/live',
    'app ready': 'http://app:8000/health/ready',
    'Prometheus': 'http://prometheus:9090/-/ready',
    'Grafana': 'http://grafana:3000/api/health',
    'Loki': 'http://loki:3100/ready',
    'Tempo': 'http://tempo:3200/ready',
    'Pyroscope': 'http://pyroscope:4040/ready',
    'Alertmanager': 'http://alertmanager:9093/-/ready',
    'Collector': 'http://otel-collector:13133/',
}
failed = False
for name, url in checks.items():
    try:
        with urllib.request.urlopen(url, timeout=8) as response:
            body = response.read()
        if name == 'app ready':
            state = json.loads(body)
            print(f'{name}: {state["status"]}; dependencies={state["dependencies"]}')
            if state['status'] == 'degraded':
                print('WARNING: Redis unavailable; required PostgreSQL functionality is ready.')
        else:
            print(f'{name}: healthy')
    except (OSError, ValueError, urllib.error.URLError) as exception:
        failed = True
        print(f'{name}: FAILED ({type(exception).__name__})', file=sys.stderr)
sys.exit(1 if failed else 0)
PYTHON
