# Operations Runbook

Scope: one Linux Docker host, one application worker and local persistent volumes. PostgreSQL is required for readiness; Redis is optional. Observability services are essential to the learning exercise but are not dependencies of the business readiness decision. Run commands from the repository root. Use synthetic data for all failure exercises.

## Start, stop and inspect

```bash
./scripts/bootstrap.sh --start
make ps
make health
docker compose logs --tail=100 init-volumes migrate app
```

Expected: both one-shot jobs exit 0; all long-running containers become healthy. An app blocked behind the migration job needs investigation of that job, not repeated application restarts. First-time PostgreSQL initialization and backend readiness can take a few minutes.

```bash
make down             # containers/network removed, volumes retained
make up               # build and start in background
make restart          # process restart, not an image rebuild
```

After editing app code, requirements or migrations, use `make build` then `docker compose up -d app`. After editing environment variables, recreate affected services with `docker compose up -d`; a plain restart does not adopt changed container environment. Configuration bind-mount edits generally require restarting the consuming service. Grafana rescans provisioned dashboard files periodically.

The restart policy handles process exits. Docker health status alone does not restart an unhealthy container. Never build a watchdog that restarts the API because PostgreSQL or telemetry is down.

## Migrations and initialization

```bash
docker compose logs --tail=100 postgres migrate
make migrate
docker compose run --rm migrate alembic current
```

`init.sql` runs only against a new PostgreSQL volume. It creates the application role/database and grants ownership; Alembic alone creates application tables/indexes. Re-running `make migrate` is idempotent at head. A missing table makes readiness fail. A failed migration blocks first application startup.

For a new revision, change models, `make build`, run `make migration m="describe change"`, review both upgrade and downgrade, rebuild again, then `make migrate`. Autogeneration is a draft requiring review; it cannot infer safe renames or data migration intent. Back up before destructive changes. In production, use reviewed expand/contract migrations with a dedicated migration identity.

## Health and app logs

```bash
curl -fsS http://localhost:8000/health/live
curl -sS -w '\nHTTP %{http_code}\n' http://localhost:8000/health/ready
make health
docker compose logs --tail=100 app
```

Liveness must stay 200 during dependency outages. Readiness: 200 `ready` if PostgreSQL and Redis respond, 200 `degraded` if only Redis is down, 503 `not_ready` if PostgreSQL or the schema is unavailable. Readiness executes bounded probes; it is not a guarantee that the next transaction succeeds.

Request logs contain normalized route, status, duration, request ID and, when active, trace/span IDs. Error frames deliberately omit exception text and SQL. Use the exception class, operation and sanitized frame locations, then inspect dependency state. Do not enable SQL echo or print connection URLs into a ticket.

`docker compose logs app` reads Docker's local dual-logging cache. It does not prove that Loki received the entry. Only the app uses the Fluent Forward driver; component logs remain rotated Docker JSON files and are not duplicated into Loki in Phase 1.

## PostgreSQL connectivity, sessions and bottlenecks

```bash
docker compose exec postgres pg_isready -U postgres -d postgres
docker compose exec -T postgres sh -c 'exec psql -U postgres -d "$APP_DB_NAME"' <<'SQL'
SELECT current_database();
SELECT version_num FROM alembic_version;
SELECT count(*) FROM items;
SELECT datname, state, wait_event_type, wait_event, count(*)
FROM pg_stat_activity
GROUP BY 1, 2, 3, 4
ORDER BY 1;
SELECT pid, state, wait_event_type, wait_event, now() - query_start AS age
FROM pg_stat_activity
WHERE datname = current_database()
ORDER BY query_start;
SQL
```

The administrative commands use the container's local Unix socket. Application connectivity is separately verified through readiness using the application role and asyncpg. A healthy `pg_isready` does not prove correct app credentials or schema.

Use `application_postgres_operation_duration_seconds` to identify slow operations. Then inspect trace structure, pool limits, active waits and query plans with synthetic data. A pool has five connections plus five overflow connections per worker; increasing workers multiplies it. Statement, connect and pool timeouts bound failure waiting. Diagnose blocked transactions and missing indexes before increasing every timeout or connection count. Do not terminate database sessions without understanding their transactions.

## Redis health and cache behavior

```bash
docker compose exec redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli ping'
docker compose exec redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli INFO memory'
docker compose exec redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli --scan --pattern "*:items:v1:*"'
```

Use SCAN, not KEYS, for nontrivial datasets. Do not print cache values containing real data. Redis is capped at 64 MB with allkeys-LRU; eviction is expected for a cache. Its append-only volume supports restart experiments but is not the authoritative backup.

Observe hits/misses and errors together with DB operation rate. A high hit ratio can conceal stale data; this implementation has TTL-bounded eventual consistency. Failed invalidation triggers a worker-local cache bypass for one TTL. It does not implement distributed locking or strict cross-worker consistency.

## Prometheus targets and alerts

```bash
curl -fsS http://localhost:9090/-/ready
curl -fsS 'http://localhost:9090/api/v1/targets?state=active'
curl -fsS --get --data-urlencode 'query=up' http://localhost:9090/api/v1/query
curl -fsS --get --data-urlencode 'query=application_dependency_up' http://localhost:9090/api/v1/query
curl -fsS http://localhost:9090/api/v1/alerts
```

All seven configured targets should be up: app, Prometheus, Collector, Loki, Tempo, Pyroscope and Alertmanager. Grafana is checked by HTTP health rather than a Prometheus target. A missing target and a target with `up=0` are different failures; verify target configuration before interpreting absence as health.

```bash
make validate
docker compose restart prometheus alertmanager
```

The validator uses the exact pinned images to load Prometheus rules and Alertmanager templates, not just a generic YAML parser. Do not reload broken rules. Prometheus evaluates every 15 seconds; `for` periods delay firing. Low-traffic error/latency rules deliberately suppress noise until 50 requests occur in five minutes.

## Alertmanager workflow

```bash
curl -fsS http://localhost:9093/-/ready
curl -fsS http://localhost:9093/api/v2/alerts
curl -fsS http://localhost:9093/api/v2/status
docker compose logs --tail=100 alertmanager
```

Inspect groups and silences at http://localhost:9093. Alertmanager retains alert state, and its local route sends no email or chat message. Delivery to an external person is not configured. Create narrowly scoped, expiring silences for known maintenance and record their reason. Inhibition suppresses selected app-derived notifications when the app itself is down; it does not erase the underlying Prometheus alert.

Verify real Prometheus-to-Alertmanager delivery by stopping only the app for slightly over two minutes, checking both APIs for `FastAPITargetDown`, then starting the app and confirming resolution. This is an intentional service interruption; use only the learning stack.

## Loki and log transport

```bash
docker compose logs --tail=100 otel-collector loki
docker compose exec otel-collector /usr/local/bin/busybox wget -qO- http://loki:3100/ready
docker compose exec -T app python - <<'PYTHON'
import json
import urllib.parse
import urllib.request
query = '{service_name="fastapi-items",deployment_environment_name="local"}'
url = 'http://loki:3100/loki/api/v1/query_range?' + urllib.parse.urlencode({'query': query, 'limit': 10})
with urllib.request.urlopen(url, timeout=10) as response:
    data = json.load(response)
print(json.dumps(data, indent=2))
PYTHON
```

If local app logs exist but Loki is empty, check the Docker daemon's reachability to loopback port 8006, Collector Fluent Forward receiver startup, JSON transform warnings, exporter failures and Loki readiness/storage permissions. The Collector logs pipeline receives only `fluent_forward`; OTLP's enabled HTTP/gRPC receivers belong to the trace pipeline in Phase 1. The Loki exporter uses `/otlp` as its base and adds `/v1/logs` itself.

For a non-local `SERVICE_NAME`/`ENVIRONMENT`, substitute those values in the query and dashboard variables. Loki normalizes resource dots to underscores in indexed label names. Raw request and trace IDs are not indexed labels. Use `| json | request_id="learning-001"` or `| json | trace_id="the-hex-id"` after a bounded service/time selector.

Never configure a second driver/agent to collect the same stdout accidentally. The non-blocking driver and finite Collector queues prefer application availability over guaranteed delivery. Inspect loss/error counters and recover backend health; there is no exactly-once guarantee.

## Tempo and Collector

```bash
docker compose logs --tail=100 tempo otel-collector
docker compose exec otel-collector /usr/local/bin/busybox wget -qO- http://tempo:3200/ready
docker compose exec otel-collector /usr/local/bin/busybox wget -qO- http://127.0.0.1:13133/
docker compose exec otel-collector /usr/local/bin/busybox wget -qO- http://127.0.0.1:8888/metrics
```

Collector accepts app OTLP at `otel-collector:4317` and exports to `tempo:4317`; both receivers explicitly bind 0.0.0.0. The app must never point directly to Tempo. Collector self-metrics are separate from the app's direct Prometheus metrics. Inspect the metrics actually exposed by this version for receiver/exporter failures, queue occupancy and refusal; do not copy old counter names without verifying them.

Tempo is a version-3 monolith with local blocks/WAL and local scheduler work state. Do not replace its config with Tempo 2 compactor examples or a distributed Kafka deployment. Search the last 15 minutes with TraceQL `{ resource.service.name = "fastapi-items" }`. For a known ID, use Explore → Tempo → Trace ID; widen the time window if needed.

No trace can mean no business traffic, upstream unsampled context, a lower sampling ratio, exporter queue loss, a bad endpoint, clock skew or backend failure. A trace ID in a log is not proof that its sampled trace was retained. Check `trace_sampled` and queues before changing sampling to 100% under load.

## Pyroscope

```bash
docker compose logs --tail=100 pyroscope
docker compose exec otel-collector /usr/local/bin/busybox wget -qO- http://pyroscope:4040/ready
DURATION_SECONDS=120 make load
```

Open Pyroscope directly or select the provisioned Grafana datasource. Match service `fastapi-items`, CPU profile type, environment and time range. Wait for upload/ingestion; an idle service may have few samples. Look for `cpu_work` and compare flamegraphs before and after a controlled workload.

The SDK records Python on-CPU/GIL samples. Off-CPU waiting, SQL server work and cache server CPU are not represented in this process profile. A slow DB trace with an empty Python CPU stack is expected during waiting. Initialization failures are logged without failing readiness. Native SDK logging is disabled; backend health and profile visibility are the practical delivery checks. This version uses Pyroscope v2 architecture and local storage; preserve its data/raft directories together for experiments.

## High Latency Investigation

1. Confirm the time window, request count, p50/p95/p99 and affected normalized route.
2. Compare error rate, in-progress requests, CPU/disk/memory from `docker stats`, dependency state and cache hit/miss rate.
3. Open a slow trace from a request log. Distinguish time in application spans from DB/Redis spans and waiting.
4. Check pool saturation/DB waits and cache degradation. Use real exporters/host monitoring only after adding them in later labs.
5. Compare a Pyroscope CPU profile for the same service/time range. CPU frames explain CPU work, not I/O waiting.
6. Make one reversible change, rerun bounded load, and compare latency and errors at similar traffic volume.

## High error rate investigation

Check 5xx volume/ratio and exception kind before changing retries. Correlate the request ID and sampled trace to a sanitized error frame. A PostgreSQL outage should yield 503, while malformed input is 422 and missing IDs are 404. Redis failures should primarily produce cache errors/degraded readiness, not CRUD 5xx. Check migrations, pool/statement timeouts and release changes. Unexpected errors have a safe 500 body; inspect code at the recorded frame rather than exposing a traceback to callers.

## Controlled dependency outages

```bash
docker compose stop redis
curl -fsS http://localhost:8000/health/live
curl -fsS http://localhost:8000/health/ready
DURATION_SECONDS=10 make load
docker compose start redis
```

Expected: liveness 200, readiness 200 degraded, persistent CRUD still works, Redis errors increase, cache bypass after failed invalidation, readiness recovers after Redis returns. The Redis alert needs five minutes, so this short drill may never fire.

```bash
docker compose stop postgres
curl -fsS http://localhost:8000/health/live
curl -sS -w '\nHTTP %{http_code}\n' http://localhost:8000/health/ready
docker compose start postgres
make health
```

Expected: liveness 200; readiness 503; writes/list/cache misses fail safely. An existing cache hit can still return a short-lived item, but the app remains not ready because required persistence is unavailable. Check transaction outcome after ambiguous network failures rather than blindly retrying non-idempotent writes. Database startup recovery may take time. Avoid deleting volumes as an outage remedy.

## Logs-to-traces workflow

In Loki select the bounded service/environment/time window, filter a request ID, expand the log and follow TraceID to Tempo. From a Tempo span, use the logs link to find entries with the same trace ID. SQL and Redis spans let you compare cache hits/misses. Compare service/time with Pyroscope independently; Phase 1 does not attach a precise profile to every span. Correlation requires synchronized host clocks and retained data in both systems.

## Capacity, disk and volumes

```bash
docker stats --no-stream
docker system df -v
docker compose ps -a
docker volume ls --filter label=com.docker.compose.project=fastapi-observability
```

Adjust the last filter if `COMPOSE_PROJECT_NAME` changes. Watch available disk on the Docker data root and the host filesystem. Prometheus's 1 GB TSDB retention size excludes some overhead; backend 72-hour retention does not enforce disk quotas. WALs, compaction, queues, local Docker log caches and backups also consume space. Approaching a container memory limit can cause OOM exits even if application health was recently good.

`make down` retains volumes. `make clean CONFIRM=delete-local-data` deletes all project volumes and every stored item, dashboard edit, silence and telemetry record. Never use broad `docker system prune --volumes` as a routine incident response. Do not copy live database files as a valid logical backup.

## Backup and restore drill

PostgreSQL is the priority source-of-truth backup. Keep encrypted copies off the single Docker host, restrict access, define RPO/RTO and test restore. The following local drill uses the container's administrative Unix socket and restores into a separate database:

```bash
mkdir -p backups
chmod 700 backups
umask 077
docker compose exec -T postgres sh -c 'exec pg_dump -U postgres -Fc "$APP_DB_NAME"' > backups/items.dump
docker compose exec postgres createdb -U postgres items_restore
docker compose exec -T postgres pg_restore -U postgres --no-owner -d items_restore < backups/items.dump
docker compose exec postgres psql -U postgres -d items_restore -c 'SELECT count(*) FROM items;'
```

Choose a fresh restore database name on later runs; `createdb` deliberately fails if it exists. This restores objects as the administrative role for inspection; a real recovery must restore appropriate roles/ownership/grants before pointing the app at it. Role definitions and secure configuration need separate backup. Validate Alembic revision, row counts and representative reads/writes after recovery. Store backup files outside source control.

Redis is rebuildable cache data. Provisioned dashboard/configuration source is already in the repository; Grafana's volume contains user/account state that may also need protection. Alertmanager silences, backend telemetry and Collector queues have separate durability needs. For file-based telemetry snapshots, quiesce the service or use its supported backup process; copying changing files may be inconsistent. This stack has no backup automation or HA replication.

## Restart and incident workflow

Declare the user-visible symptom, start time, scope and required/degraded dependencies. Preserve the relevant request/trace IDs, alert state and container logs without secrets. Establish whether the failure is application, dependency, host saturation or telemetry-only. Stabilize with the smallest reversible action; do not repeatedly restart a slow database without understanding recovery work. Record changes and timestamps.

After mitigation, verify liveness, readiness, representative CRUD, targets, log/trace/profile visibility and alert resolution. Check for missing telemetry during the incident, stale caches, ambiguous writes and disk pressure. Document cause, impact, detection gaps and a specific follow-up lab or engineering fix. A green dashboard is evidence to investigate, not proof that no users were affected.
