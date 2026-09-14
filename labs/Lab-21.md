# Lab 21: Build USE and Dependency Dashboards

## Purpose and Scope

> **Primary Objective:** Correlate application behavior with host utilization, saturation clues, error evidence and independent database/cache observations.

RED describes completed service requests. USE asks about utilization, saturation and errors of supporting resources. Exporters observe those resources from different connections and scopes than the application.

This lab builds a twenty-panel dashboard and briefly stops Redis to compare business success, cache degradation and PostgreSQL fallback. It does not add new services or repeat the full Redis incident exercise scheduled for Lab 45.

## 1. Inherited State and Starting Checks

```bash
source lab-notes/session.sh
source lab-notes/evidence.sh
source lab-notes/raw-metrics.sh
source lab-notes/metrics-session.sh
metrics_check
start_lab 21
```

Complete [Lab 20](Lab-20.md) first. Run commands from the repository root in the same Bash session. Keep the existing credentials, named volumes and checkpoint item. Keep eight services and five jobs healthy. Node Exporter measures the Linux VM, not a Docker Desktop workstation. Preserve the checkpoint item.

## 2. Objectives and Scope of Evidence

You will separate utilization from pressure, match vector labels correctly, distinguish exporter reachability from downstream health and correlate two dashboards over one time range.

| Resource | Utilization | Saturation/pressure clue | Error evidence or gap |
|---|---|---|---|
| CPU | Non-idle fraction | Load per CPU | No universal CPU-error counter |
| Memory | Used fraction from MemAvailable | Requires pressure context | Used bytes alone do not prove OOM risk |
| Disk | Busy time and filesystem use | Weighted I/O time | Not a direct application storage-error count |
| Network | Per-interface byte rate | Needs link-capacity context | Interface errors differ from HTTP failures |
| PostgreSQL | Connections and transactions | Pool/query investigation | Deadlocks, rollbacks and connectivity differ |
| Redis | Memory, clients, commands | Evictions/rejections | Cache errors and downstream health |

This table maps questions to available evidence rather than pretending every USE cell is completely instrumented. The relevant events are cache failure, fallback reads, exporter collection and recovery.

## 3. Inspect Resource Identity Before Graphing

```bash
ITEM_ID=$(cat lab-notes/checkpoint-item-id.txt)
LAB_DATABASE=$(dm exec -T app python -c 'from app.config import Settings; print(Settings().postgres_db)')
api -fsS "$APP_URL/api/v1/items/$ITEM_ID" > "$LAB_DIR/checkpoint-before.json"
pq 'node_filesystem_size_bytes{job="node"}' > "$LAB_DIR/filesystems.json"
pq 'node_network_receive_bytes_total{job="node"}' > "$LAB_DIR/interfaces.json"
pq 'pg_stat_database_numbackends{job="postgres"}' > "$LAB_DIR/databases.json"
docker info --format '{{.DockerRootDir}}' > "$LAB_DIR/docker-data-directory.txt"
jq -r '.data.result[].metric | [.mountpoint,.device,.fstype] | @tsv' "$LAB_DIR/filesystems.json"
```

Identify the filesystem holding Docker's data directory. Do not assume `/dev/sda`, root `/` or `eth0` on every VM. Physical, bridge and veth interfaces can see the same packets; summing them blindly double-counts work.

Host-wide use includes other services. A time correlation motivates a hypothesis but does not attribute all CPU or disk activity to FastAPI.

## 4. Generate the Complete Dependency Dashboard

```bash
cat > lab-notes/build_use_dashboard.py <<'PYTHON'
"""Usage: build_use_dashboard.py ENVIRONMENT SERVICE DATABASE OUTPUT_JSON"""

import json, sys
from dashboard_factory import panel, save, scope

if len(sys.argv) != 5:
    raise SystemExit(__doc__)
environment, service, database, output = sys.argv[1:]
base = scope(environment, service)
pg = 'job="postgres",datname=' + json.dumps(database)
fs = 'job="node",fstype!~"tmpfs|devtmpfs|overlay|squashfs|proc|sysfs|cgroup2?"'
hits = "rate(pg_stat_database_blks_hit{" + pg + "}[2m])"
reads = "rate(pg_stat_database_blks_read{" + pg + "}[2m])"
cache_hits = 'sum(rate(application_cache_hits_total{job="fastapi",' + base + "}[2m]))"
cache_misses = (
    'sum(rate(application_cache_misses_total{job="fastapi",' + base + "}[2m]))"
)
p = []


def add(title, queries, unit, description, kind="timeseries"):
    p.append(panel(len(p) + 1, title, queries, unit, description, kind))


add(
    "Exporter scrape health",
    [('up{job=~"node|postgres|redis"}', "{{job}} {{instance}}")],
    "short",
    "Prometheus-to-exporter path.",
    "stat",
)
add(
    "Dependency observation paths",
    [
        ('pg_up{job="postgres"}', "PostgreSQL exporter → server"),
        ('redis_up{job="redis"}', "Redis exporter → server"),
        (
            'application_dependency_up{job="fastapi",' + base + "}",
            "application → {{dependency}}",
        ),
    ],
    "short",
    "Independent observers; do not average their binary states.",
    "stat",
)
add(
    "Host CPU utilization",
    [
        (
            '1 - avg by (instance) (rate(node_cpu_seconds_total{job="node",mode="idle"}[2m]))',
            "{{instance}}",
        )
    ],
    "percentunit",
    "Host-wide non-idle fraction across CPUs, not application-only use.",
)
add(
    "Load per CPU",
    [
        (
            'max by (instance) (node_load1{job="node"}) / count by (instance) (node_cpu_seconds_total{job="node",mode="idle"})',
            "{{instance}}",
        )
    ],
    "short",
    "Runnable and uninterruptible tasks per CPU; a pressure clue, not a direct queue measure.",
)
add(
    "Host memory used fraction",
    [
        (
            '1 - node_memory_MemAvailable_bytes{job="node"} / node_memory_MemTotal_bytes{job="node"}',
            "{{instance}}",
        )
    ],
    "percentunit",
    "Available memory accounts for reclaimable memory better than free memory alone.",
)
add(
    "Filesystem used fraction",
    [
        (
            "(1 - node_filesystem_avail_bytes{"
            + fs
            + "} / node_filesystem_size_bytes{"
            + fs
            + "}) and (node_filesystem_size_bytes{"
            + fs
            + "} > 0)",
            "{{mountpoint}} {{device}}",
        )
    ],
    "percentunit",
    "Match the Docker data directory to its actual mount; do not sum duplicate mounts.",
)
add(
    "Disk busy fraction",
    [
        (
            'rate(node_disk_io_time_seconds_total{job="node",device!~"loop.*|ram.*"}[2m])',
            "{{device}}",
        )
    ],
    "percentunit",
    "Busy time is not a universal performance ceiling for parallel storage.",
)
add(
    "Disk weighted I/O time rate",
    [
        (
            'rate(node_disk_io_time_weighted_seconds_total{job="node",device!~"loop.*|ram.*"}[2m])',
            "{{device}}",
        )
    ],
    "short",
    "Approximate average in-flight I/O; interpret per device with latency.",
)
add(
    "Network throughput",
    [
        (
            'rate(node_network_receive_bytes_total{job="node",device!="lo"}[2m])',
            "RX {{device}}",
        ),
        (
            'rate(node_network_transmit_bytes_total{job="node",device!="lo"}[2m])',
            "TX {{device}}",
        ),
    ],
    "Bps",
    "Keep physical, bridge and veth paths distinct to avoid double-counting.",
)
add(
    "Network errors",
    [
        (
            'rate(node_network_receive_errs_total{job="node",device!="lo"}[2m])',
            "RX {{device}}",
        ),
        (
            'rate(node_network_transmit_errs_total{job="node",device!="lo"}[2m])',
            "TX {{device}}",
        ),
    ],
    "ops",
    "Interface errors, not HTTP errors.",
)
add(
    "PostgreSQL connections",
    [("pg_stat_database_numbackends{" + pg + "}", "{{datname}}")],
    "short",
    "Includes app pool, exporter and operator connections; no invented maximum denominator.",
)
add(
    "PostgreSQL transactions",
    [
        ("rate(pg_stat_database_xact_commit{" + pg + "}[2m])", "commits/s"),
        ("rate(pg_stat_database_xact_rollback{" + pg + "}[2m])", "rollbacks/s"),
    ],
    "ops",
    "Server activity includes health checks and monitoring, not just item writes.",
)
add(
    "PostgreSQL buffer hit fraction",
    [(f"({hits} / ({hits} + {reads})) and ({hits} + {reads} > 0)", "{{datname}}")],
    "percentunit",
    "Shared-buffer hits; does not include operating-system cache effects.",
)
add(
    "PostgreSQL deadlocks",
    [("rate(pg_stat_database_deadlocks{" + pg + "}[2m])", "{{datname}}")],
    "ops",
    "Database deadlocks, not all query failures.",
)
add(
    "Redis memory",
    [('redis_memory_used_bytes{job="redis"}', "used bytes")],
    "bytes",
    "Server memory; used bytes alone do not establish saturation.",
)
add(
    "Redis clients",
    [('redis_connected_clients{job="redis"}', "clients")],
    "short",
    "Connections from application, exporter and administration.",
)
add(
    "Redis commands",
    [('rate(redis_commands_processed_total{job="redis"}[2m])', "commands/s")],
    "ops",
    "All server commands, including monitoring.",
)
add(
    "Application cache outcomes",
    [
        (cache_hits, "hits/s"),
        (cache_misses, "misses/s"),
        (
            'sum(rate(application_redis_errors_total{job="fastapi",' + base + "}[2m]))",
            "errors/s",
        ),
    ],
    "ops",
    "Application cache observations, not global Redis keyspace statistics.",
)
add(
    "Application cache hit fraction",
    [
        (
            f"({cache_hits} / ({cache_hits} + {cache_misses})) and ({cache_hits} + {cache_misses} > 0)",
            "hit fraction",
        )
    ],
    "percentunit",
    "Successful lookup population; read alongside cache-error rate.",
)
add(
    "Redis evictions and rejections",
    [
        ('rate(redis_evicted_keys_total{job="redis"}[2m])', "evictions/s"),
        ('rate(redis_rejected_connections_total{job="redis"}[2m])', "rejections/s"),
    ],
    "ops",
    "Distinct pressure symptoms tied to server configuration.",
)
save(output, "lab21-use", "Lab 21 — Host and Dependencies", p)
PYTHON
```

```bash
python3 lab-notes/build_use_dashboard.py "$LAB_ENVIRONMENT" "$LAB_SERVICE" "$LAB_DATABASE" lab-notes/grafana/lab21-use.json
python3 -m json.tool lab-notes/grafana/lab21-use.json >/dev/null
cp lab-notes/grafana/lab21-use.json "$LAB_DIR/use-dashboard-generated.json"
```

Import as **Lab 21 — Host and Dependencies**, UID `lab21-use`. Keep the RED dashboard in a second tab with the same UTC interval. The existing factory provides consistent panel structure without adding a plugin.

Bytes, clients, fractions and operations/second remain separate. There is no invented maximum-connection denominator or assumption that Redis used bytes equals memory saturation.

## 5. Explain the Query Choices

The load/CPU-count division aggregates both sides by `instance` so their label sets match. An unmatched division can silently return no data.

The pinned PostgreSQL exporter exposes `pg_stat_database_xact_commit` and related counters without `_total`; use their actual type/name rather than inventing a suffix. Shared-buffer hit fraction does not include operating-system cache behavior. Consult the [versioned exporter documentation](https://github.com/prometheus-community/postgres_exporter/tree/v0.17.1).

Redis server statistics include all clients and monitoring commands. Application cache outcomes cover the wrapper's lookup/error behavior. A failed cache operation is not automatically a normal miss, so read cache errors alongside a hit fraction.

## 6. Compare Independent Dependency Observers

```bash
pq 'up{job=~"postgres|redis"}' > "$LAB_DIR/exporter-health-before.json"
pq 'pg_up{job="postgres"}' > "$LAB_DIR/postgres-health-before.json"
pq 'redis_up{job="redis"}' > "$LAB_DIR/redis-health-before.json"
pq 'application_dependency_up{job="fastapi"}' > "$LAB_DIR/app-dependencies-before.json"
api -fsS "$APP_URL/health/ready" > "$LAB_DIR/readiness-before.json"
```

Explain why exporter `up=1` can coexist with `redis_up=0`: Prometheus reached the exporter, but the exporter could not collect from Redis. The application has its own connections and health contract.

Do not average these binary checks into one reassuring number. Their different observation times and failure domains are diagnostically useful.

## 7. Establish a Warm-Cache Baseline

```bash
snapshot "$LAB_DIR/warm-before.json"
for index in $(seq 1 20); do
  api -fsS -o /dev/null -w '%{http_code} %{time_total}\n' "$APP_URL/api/v1/items/$ITEM_ID" >> "$LAB_DIR/warm-client.txt"
  sleep 1
done
snapshot "$LAB_DIR/warm-after.json"
printf '%s\n' 'SELECT datname,numbackends,xact_commit,xact_rollback,blks_hit,blks_read FROM pg_stat_database WHERE datname=current_database();' \
  | dbsql > "$LAB_DIR/database-before.txt"
```

The first read or a TTL boundary can miss; subsequent reads should usually hit. Compare raw counter deltas using the earlier `metric_sum` helper. PostgreSQL still performs health/exporter/operator work even when application reads hit cache.

## 8. Predict and Run the Bounded Redis Experiment

```bash
(
  set -euo pipefail
  trap 'dm start redis >/dev/null' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  record_change "use_redis_stop" planned
  dm stop redis
  wait_metric 'redis_up{job="redis"}' 0
  wait_target redis up
  api -fsS "$APP_URL/health/ready" > "$LAB_DIR/degraded-readiness.json"
  snapshot "$LAB_DIR/degraded-before.json"
  for index in $(seq 1 30); do
    api -fsS -o /dev/null -w '%{http_code} %{time_total}\n' "$APP_URL/api/v1/items/$ITEM_ID" >> "$LAB_DIR/degraded-client.txt"
    sleep 1
  done
  snapshot "$LAB_DIR/degraded-after.json"
  pq 'application_dependency_up{job="fastapi"}' > "$LAB_DIR/degraded-app-state.json"
  printf '%s\n' 'SELECT datname,numbackends,xact_commit,xact_rollback,blks_hit,blks_read FROM pg_stat_database WHERE datname=current_database();' \
    | dbsql > "$LAB_DIR/database-during.txt"
)
wait_ready
wait_metric 'redis_up{job="redis"}' 1
metrics_check
record_change "use_redis_restored" completed
```

Before stopping Redis, predict both exporter hops, readiness HTTP status, business status and cache errors. Expected: exporter endpoint healthy, Redis downstream unavailable, readiness HTTP 200 with degraded cache, successful business reads and additional cache errors.

PostgreSQL connection count need not rise: the existing pool can execute more operations on the same connections. Compare operation/transaction activity and client evidence rather than asserting a connection spike. Thirty reads may be subtle against monitoring traffic and a two-minute rate window.

## 9. Correlate Both Dashboards and Prove Recovery

```bash
api -fsS "$APP_URL/api/v1/items/$ITEM_ID" > "$LAB_DIR/checkpoint-recovered.json"
for index in $(seq 1 10); do api -fsS "$APP_URL/api/v1/items/$ITEM_ID" >/dev/null; sleep 1; done
snapshot "$LAB_DIR/recovered-metrics.json"
api -fsS "$APP_URL/health/ready" > "$LAB_DIR/recovered-readiness.json"
capture_app_logs
jq -e --arg id "$ITEM_ID" '.id==$id' "$LAB_DIR/checkpoint-recovered.json"
metrics_check
```

Use the same absolute time window on both dashboards. Relate successful requests and latency to cache errors, downstream health, database activity and host observations. Save one supported conclusion and one uncertainty.

After recovery, the first read can repopulate the cache and later reads should hit. Lifetime error counters retain historical failures; their rates return toward zero as the window moves past the outage. A missing Redis server series during failure is not a measured zero.

## 10. Troubleshooting

| Symptom | Check | Action |
|---|---|---|
| Host panels empty | Node target and bridge gateway | Revisit Lab 14; do not expose exporter on every interface |
| Database panels empty | Actual `datname` | Regenerate with the configured database |
| Buffer ratio absent at idle | Hit/read denominator | Preserve undefined state rather than declaring 100% hits |
| Redis exporter up but server metrics missing | Downstream collection | Inspect `redis_up`, credentials and server health |
| High memory use with no pressure | Available/reclaimable memory | Do not infer OOM risk from use alone |
| Database effect is small | Rate window and competing work | Compare raw deltas and application operations at matched times |

Use the [versioned Node Exporter collector reference](https://github.com/prometheus/node_exporter/tree/v1.9.1) when interpreting host-specific collector behavior.

## 11. Knowledge Check

1. Why is utilization not saturation?
2. Why can pooled connection count stay flat?
3. Why not sum all network interfaces?

### Answer Guide

1. Use alone does not show waiting or resource exhaustion.
2. Existing connections can execute more work.
3. The same traffic may traverse multiple observed interfaces.

## 12. Professional Scenario Exercise

Latency and database connections rise while host CPU remains modest. Propose two competing hypotheses and a discriminating observation for each before recommending a larger VM.

## 13. Lab Notebook Template

Create a notebook in the evidence directory for this run:

```bash
test -e "$LAB_DIR/notebook.md" || printf '# Lab 21 Evidence\n' > "$LAB_DIR/notebook.md"
```

Use these headings and fill them with your predictions, commands, observed results and conclusions:

```markdown
# Lab 21 Evidence

## Starting state and operational question
## Prediction before the change
## Commands and timestamps
## Observed results and source evidence
## Unexpected findings and troubleshooting
## Recovery proof and remaining limitations
```

## 14. Observable Completion Criteria

- [ ] Twenty panels use actual dependency/host metrics.
- [ ] Measurement scopes and vector matching are explained.
- [ ] Redis outage preserves business reads and changes cache observations.
- [ ] Both dashboards share a correlated interval and recovery evidence.

## 15. Production Implications

Host-wide metrics are not automatic application attribution. Maintain exporter contracts and scoped credentials; combine utilization with pressure and workload evidence before assigning capacity or alert thresholds.

## 16. End State and Transition

Keep eight services and both dashboard source files. [Lab 22](Lab-22.md) adds selectors, navigation and version-controlled provisioning.
