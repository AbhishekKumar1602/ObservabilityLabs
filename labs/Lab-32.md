# Lab 32: Log Shipping Failure, Retention, and Cost

## Purpose and Scope

> **Primary Objective:** Measure what the application, Docker driver, Collector and Loki retain during two bounded outages, then reconcile event identities after recovery.

An available application can produce logs that never reach the search backend. Conversely, a healthy Collector process does not prove that its exporter is delivering records.

This lab interrupts Loki first, then the Collector, while sending only twenty read-only requests per experiment. You will inspect finite buffers, compare the same completion events at three observation points, and calculate a transparent storage estimate. You will not fill a disk, delete production logs, shorten retention to destroy existing history, or claim exactly-once delivery.

## 1. Inherited State and Starting Checks

Complete [Lab 31](Lab-31.md) first. Run every command from the repository root in the same Bash session. Retain the credentials, named volumes and existing application checkpoint item. Keep the Lab 31 restart alert inactive. Avoid app restarts and other load during both experiments.

```bash
source lab-notes/session.sh
source lab-notes/evidence.sh
source lab-notes/metrics-session.sh
source lab-notes/platform-session.sh
load_app_settings
start_lab 32
dp config --quiet
dp ps -a
wait_ready
wait_backend loki:3100 /ready
wait_backend otel-collector:13133 /
```

`dp` is the stage-aware Compose helper introduced in Lab 31. It reads the same project and named volumes, adding only the overlays that exist. Use it throughout these labs: running plain `docker compose up` would enable the original full-stack settings instead of the learning stage. `start_lab` creates a fresh evidence directory and sets `LAB_DIR`; keep that directory for this run.

```bash
backend loki:3100 /prometheus/api/v1/alerts > "$LAB_DIR/alerts-before.json"
backend otel-collector:8888 /metrics > "$LAB_DIR/collector-before.prom"
ACTIVE_COLLECTOR_CONFIG=$(mounted_config otel-collector /etc/otelcol-contrib/config.yaml)
ACTIVE_LOKI_CONFIG=$(mounted_config loki /etc/loki/config.yaml)
cp "$ACTIVE_COLLECTOR_CONFIG" "$LAB_DIR/collector-before.yml"
cp "$ACTIVE_LOKI_CONFIG" "$LAB_DIR/loki-before.yml"
```

## 2. Learning Objectives and Failure Boundaries

You will distinguish process readiness from delivery, explain what each buffer can lose, compare unique event identities after recovery, and estimate retention cost without treating raw JSON size as compressed disk size.

| Boundary | What can survive | What can still be lost |
|---|---|---|
| App stdout → Docker | Docker can continue accepting output in non-blocking mode | Finite non-blocking buffer overflow; abrupt process/host failure |
| Docker Fluentd driver → Collector | Driver retries/asynchronous buffering during a short disconnect | Finite driver buffer; daemon restart; connection errors |
| Collector → Loki | Persistent exporter queue can retain accepted batches | Queue full, disk full, retry expiry, records not yet admitted, permanent rejection |
| Loki → local storage | Accepted records become queryable and persistent under the storage configuration | Single disk/host failure, retention deletion, corruption |
| Docker local log cache | Recent records remain readable through `docker logs` | Rotation removes old records; cache is not a replay source for the driver |

A Collector queue is not the Docker driver buffer. Stopping the Collector prevents it from accepting new records into its queue. This distinction is the core of the second experiment.

## 3. Inspect the Actual Buffer and Retention Policy

```bash
dp config --format json | python3 -c '
import json, sys
c=json.load(sys.stdin)
print(json.dumps(c["services"]["app"]["logging"],indent=2))
' > "$LAB_DIR/app-logging-policy.json"
cat "$LAB_DIR/app-logging-policy.json"
lab-notes/.tools/bin/python - "$ACTIVE_COLLECTOR_CONFIG" "$ACTIVE_LOKI_CONFIG" <<'PYTHON'
import sys, yaml, json
c=yaml.safe_load(open(sys.argv[1])); l=yaml.safe_load(open(sys.argv[2]))
print(json.dumps({'log_exporter':c['exporters']['otlp_http/loki'],
                 'file_storage':c['extensions']['file_storage'],
                 'retention':l['limits_config']['retention_period'],
                 'compactor':l['compactor'],
                 'index_policy':l['limits_config']['otlp_config']},indent=2))
PYTHON
```

Record the actual values: asynchronous driver delivery, non-blocking buffer size, driver buffer limit, queue capacity, retry expiry, file-storage directory and Loki retention. Queue capacity is measured in the exporter's configured units—normally queued requests/batches here—not individual JSON lines or megabytes. Record batch sizes as well before estimating capacity.

The inherited Loki policy retains 72 hours with a compactor and structured metadata. Retention requires the compactor to run and deletion is asynchronous. A five-minute lab cannot prove a 72-hour retention cycle; reading configuration is policy evidence, not deletion proof.

**Prediction checkpoint:** stopping Loki should increase delivery failures or queue occupancy while the Collector stays reachable. Stopping the Collector instead moves pressure toward Docker. Twenty small records may fit comfortably in both cases; absence of loss here does not prove durability under a longer outage.

## 4. Install a Bounded Workload and Identity Comparison

The workload performs GET requests only, validates each 200 response and echoed request ID, and writes a client ledger before the next request. It generates no arbitrary item names and deletes nothing.

```bash
cat > lab-notes/bounded_reads.py <<'PYTHON'
"""Twenty read-only requests, fixed timeout and bounded arrival rate; preserve client evidence."""
import json, os, time, urllib.error, urllib.request, uuid
from pathlib import Path
import sys

output = Path(sys.argv[1])
assert not output.exists(), 'Choose a fresh evidence filename'
prefix = 'shipping-' + uuid.uuid4().hex[:12]
url = os.environ.get('APP_URL', 'http://127.0.0.1:8000') + '/api/v1/items?limit=1'
with output.open('x') as stream:
    for number in range(20):
        request_id = f'{prefix}-{number:02}'
        request = urllib.request.Request(url, headers={'X-Request-ID':request_id})
        started = time.time_ns()
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                status = response.status
                returned = response.headers.get('X-Request-ID')
                response.read()
        except urllib.error.HTTPError as exc:
            status, returned = exc.code, exc.headers.get('X-Request-ID')
        record = {'request_id':request_id, 'status':status, 'returned_id':returned, 'time_ns':started}
        stream.write(json.dumps(record)+'\n'); stream.flush()
        assert status == 200 and returned == request_id, record
        time.sleep(0.2)
print(prefix)
PYTHON
```

```bash
cat > lab-notes/compare_shipping.py <<'PYTHON'
"""Compare completion-event identities at client, Docker and Loki; report evidence gaps."""
import collections, json, sys
from pathlib import Path

client = [json.loads(line) for line in Path(sys.argv[1]).read_text().splitlines()]
expected = {r['request_id'] for r in client}
def records(lines):
    output = []
    for line in lines:
        try: row = json.loads(line)
        except json.JSONDecodeError: continue
        if row.get('request_id') in expected and row.get('event_name') == 'request_completed': output.append(row)
    return output
local = records(Path(sys.argv[2]).read_text().splitlines())
response = json.loads(Path(sys.argv[3]).read_text())
assert response['status'] == 'success'
remote = records(v[1] for stream in response['data']['result'] for v in stream['values'])
def summarize(rows):
    counts = collections.Counter(r['request_id'] for r in rows)
    ids = [r['event_id'] for r in rows]
    return {'records':len(rows), 'missing_request_ids':sorted(expected-set(counts)),
            'repeated_request_ids':{k:v for k,v in counts.items() if v>1},
            'unique_event_ids':len(set(ids))}
print(json.dumps({'client_requests':len(client), 'docker':summarize(local), 'loki':summarize(remote),
                  'docker_events_missing_from_loki':sorted({r['event_id'] for r in local}-{r['event_id'] for r in remote}),
                  'same_unique_events':{r['event_id'] for r in local} == {r['event_id'] for r in remote}}, indent=2))
PYTHON
```

The comparison reads `event_id` and `request_id` from the original JSON body. It reports missing and repeated records separately. A set comparison alone would hide duplicates; the report therefore includes raw record counts and unique identities.

## 5. Experiment A: Stop Loki While the Collector Runs

```bash
SHIP_START=$(date -u +%Y-%m-%dT%H:%M:%SZ)
# Restores both components if you leave this shell before completing recovery.
trap 'dp start loki otel-collector >/dev/null' EXIT
dp stop loki
api -fsS "$APP_URL/health/live"
api -fsS "$APP_URL/health/ready"
wait_backend otel-collector:13133 /
PREFIX_A=$(python3 lab-notes/bounded_reads.py "$LAB_DIR/client-a.jsonl")
backend otel-collector:8888 /metrics > "$LAB_DIR/collector-loki-down.prom"
dp logs --tail=100 --no-color otel-collector > "$LAB_DIR/collector-loki-down.log"
dp start loki
wait_backend loki:3100 /ready
```

Expected: the client ledger contains twenty successful responses while Loki is unavailable. The Collector health endpoint can still return success. Inspect the exporter logs and its actual exposed metric families:

```bash
rg 'otelcol_exporter_(queue|send_failed|sent|enqueue_failed).*'   "$LAB_DIR/collector-loki-down.prom" | head -n 35
```

A queue gauge may already have drained by the time you capture it; a small workload and short outage can make the peak brief. A failure counter rising does not equal the number of permanently lost records: retried batches can fail multiple attempts. Do not add guessed metric names to alert rules.

## 6. Reconcile the First Experiment After Recovery

```bash
SCOPE=$(log_select)
QUERY_A="$SCOPE | json rid="request_id",event="event_name" | __error__="" | rid=~"$PREFIX_A-.*" | event="request_completed""
for attempt in {1..60}; do
  backend loki:3100 /loki/api/v1/query_range query "$QUERY_A" since 30m limit 1000 direction forward     > "$LAB_DIR/loki-a.json"
  if jq -e '[.data.result[].values[]] | length>=20' "$LAB_DIR/loki-a.json" >/dev/null; then break; fi
  sleep 2
done
dp logs --since "$SHIP_START" --no-color --no-log-prefix app > "$LAB_DIR/docker-a.jsonl"
python3 lab-notes/compare_shipping.py "$LAB_DIR/client-a.jsonl" "$LAB_DIR/docker-a.jsonl" "$LAB_DIR/loki-a.json"   | tee "$LAB_DIR/comparison-a.json"
backend otel-collector:8888 /metrics > "$LAB_DIR/collector-after-a.prom"
```

If all layers match, expect twenty client requests, twenty completion records in each source, twenty unique event IDs, and no missing IDs. The script reports evidence instead of silently treating a mismatch as success.

If fewer records arrive, inspect retry time limits, collector errors and clock/time bounds before concluding permanent loss. Save the initial mismatch and repeat the query later; delayed arrival is different from permanent absence. The 30-minute search window must still include the experiment.

## 7. Experiment B: Stop the Collector

```bash
SHIP_START_B=$(date -u +%Y-%m-%dT%H:%M:%SZ)
dp stop otel-collector
PREFIX_B=$(python3 lab-notes/bounded_reads.py "$LAB_DIR/client-b.jsonl")
api -fsS "$APP_URL/health/ready"
dp logs --since "$SHIP_START_B" --no-color --no-log-prefix app > "$LAB_DIR/docker-b.jsonl"
dp start otel-collector
wait_backend otel-collector:13133 /
QUERY_B="$SCOPE | json rid="request_id",event="event_name" | __error__="" | rid=~"$PREFIX_B-.*" | event="request_completed""
for attempt in {1..60}; do
  backend loki:3100 /loki/api/v1/query_range query "$QUERY_B" since 30m limit 1000 direction forward     > "$LAB_DIR/loki-b.json"
  if jq -e '[.data.result[].values[]] | length>=20' "$LAB_DIR/loki-b.json" >/dev/null; then break; fi
  sleep 2
done
python3 lab-notes/compare_shipping.py "$LAB_DIR/client-b.jsonl" "$LAB_DIR/docker-b.jsonl" "$LAB_DIR/loki-b.json"   | tee "$LAB_DIR/comparison-b.json"
```

There is deliberately no assertion that all twenty records *must* survive this boundary. Record what this Docker version and driver do. The non-blocking configuration favors application availability under telemetry failure; finite buffers create a loss risk.

If recovery yields all twenty records, you demonstrated successful buffering for this small outage. You did not prove that a daemon restart, full buffer, long outage or host power loss preserves them. Do not prolong the outage until the host is unstable just to force a dramatic graph.

## 8. Evaluate Retention, Cardinality, and Cost

```bash
SERIES_START=$(python3 -c 'import time; print(time.time_ns()-1800*10**9)')
backend loki:3100 /loki/api/v1/series 'match[]' "$SCOPE" start "$SERIES_START" > "$LAB_DIR/indexed-streams.json"
jq '.data' "$LAB_DIR/indexed-streams.json"
dp exec -T loki /usr/local/bin/busybox du -sk /loki > "$LAB_DIR/loki-disk-kib.txt"
dp exec -T otel-collector /usr/local/bin/busybox du -sk /var/lib/otelcol > "$LAB_DIR/queue-disk-kib.txt"
python3 - "$LAB_DIR/loki-a.json" <<'PYTHON'
import json, sys
rows=[v[1] for stream in json.load(open(sys.argv[1]))['data']['result'] for v in stream['values']]
assert rows, 'Need observed log bodies for this estimate'
average=sum(len(s.encode()) for s in rows)/len(rows)
rate=10
print({'sample_records':len(rows),'mean_body_bytes':round(average,1),
       'hypothetical_records_per_second':rate,
       'raw_body_GiB_per_day':round(average*rate*86400/1024**3,3),
       'raw_body_GiB_for_72h':round(average*rate*86400*3/1024**3,3)})
PYTHON
```

The projection assumes ten records/second and the observed mean body size. It excludes compression, indexes, structured metadata, WAL, compactor work, replication and filesystem overhead. Label it as a raw-body projection, never measured capacity.

`/series` reports indexed streams. Request IDs and event IDs remain metadata/body fields; inspect this policy after changes. More unique indexed combinations mean more streams, generally more index/chunk overhead and poorer compression opportunities. Narrow selectors and shorter investigation windows reduce query work; a broad regex over every stream does the opposite.

No file deletion is part of this exercise. Back up and test a retention-policy change in a separate environment before it can remove irreplaceable history.

## 9. Recovery and Final Canary

```bash
dp start loki otel-collector
wait_backend loki:3100 /ready
wait_backend otel-collector:13133 /
wait_ready
FINAL_ID="shipping-recovery-$(new_uuid)"
api -fsS -H "X-Request-ID: $FINAL_ID" "$APP_URL/api/v1/items?limit=1" >/dev/null
FINAL_QUERY="$SCOPE | json rid="request_id" | __error__="" | rid="$FINAL_ID""
for attempt in {1..45}; do
  backend loki:3100 /loki/api/v1/query_range query "$FINAL_QUERY" since 5m limit 100     > "$LAB_DIR/recovery-canary.json"
  if jq -e '[.data.result[].values[]]|length>0' "$LAB_DIR/recovery-canary.json" >/dev/null; then break; fi
  sleep 1
done
jq -e '[.data.result[].values[]]|length>0' "$LAB_DIR/recovery-canary.json"
backend loki:3100 /prometheus/api/v1/rules > "$LAB_DIR/rules-recovered.json"
trap - EXIT
```

The current canary proves restored delivery. Reconciliation of the earlier IDs answers the separate question of historical completeness. Preserve both answers, including any unresolved gaps.

## 10. Troubleshooting Paths

| Evidence | Investigate next |
|---|---|
| App unavailable during Loki outage | Check app logs and dependencies; a telemetry failure should not gate business readiness. |
| Collector healthy, queue rising | Exporter reachability/rejection and retry logs, not only the health extension. |
| Local Docker log exists, Loki record missing | Upstream buffer loss, collector acceptance, queue/retry expiry, Loki rejection and query bounds. |
| Client ledger exists, Docker record missing | Logging middleware, Docker cache rotation and process termination; the client alone cannot locate loss. |
| Loki record repeats with the same event ID | Delivery duplication or duplicate ingestion path; compare unique identities before counting occurrences. |
| Queue files remain after recovery | Persistent storage can retain allocation; a nonempty directory does not imply pending records. |
| Disk does not shrink after retention interval | Compactor/deletion delay, WAL and active chunks; inspect policy and compactor health. |

See the [Docker Fluentd driver](https://docs.docker.com/engine/logging/drivers/fluentd/), [Collector resiliency](https://opentelemetry.io/docs/collector/resiliency/) and [Loki retention](https://grafana.com/docs/loki/latest/operations/storage/retention/) documentation for the relevant boundaries.

## 11. Knowledge Check

1. Why can Collector health succeed during a Loki outage?
2. Does Docker logs provide automatic replay after recovery?
3. Does send failure count equal permanently lost events?
4. What does a successful twenty-record test prove?

### Answer Guide

1. Health reports the process/pipeline endpoint, not successful downstream delivery.
2. No. The local cache is readable evidence, not a driver replay queue.
3. No; retries can count repeated failures of the same batch.
4. Only that this bounded workload survived this observed outage and recovery.

## 12. Professional Scenario Exercise

During an incident you have 200 successful client responses, 200 local completion records and 160 Loki records. Write a timeline and a next-step investigation that distinguishes late arrival, query truncation, duplicated records and permanent loss without blaming the application from one number.

## 13. Lab Notebook Template

Create a notebook in the evidence directory for this run:

```bash
test -e "$LAB_DIR/notebook.md" || printf '# Lab 32 Evidence\n' > "$LAB_DIR/notebook.md"
```

Use these headings and fill them with your predictions, commands, observed results and conclusions:

```markdown
# Lab 32 Evidence

## Starting state and operational question
## Prediction before the change
## Commands and timestamps
## Observed results and source evidence
## Unexpected findings and troubleshooting
## Recovery proof and remaining limitations
```

## 14. Observable Completion Criteria

- [ ] Both outages used twenty bounded read-only requests.
- [ ] Client, Docker and Loki identities were compared; gaps and duplicates were preserved as evidence.
- [ ] Actual buffers, retry policy and retention were recorded.
- [ ] Storage estimate states assumptions and does not claim compressed disk capacity.
- [ ] Backends recovered and a new completion record arrived in Loki.

## 15. Production Implications

Persistent queues improve resilience but do not supply end-to-end exactly-once delivery. Monitor queue occupancy, refusal/drop counters, export failures, disk and fresh canary arrival together. Keep retention, ingestion volume, stream cardinality and query cost in one capacity model. The single VM remains one failure domain.

## 16. End State and Transition

Eleven services, eight scrape jobs and the existing dashboards remain; no data was intentionally deleted. The same log pipeline stays active in [Lab 33](Lab-33.md), while a separate OTLP trace pipeline and Tempo are introduced.
