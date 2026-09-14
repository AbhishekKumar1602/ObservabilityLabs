# Lab 50: Capstone Reliability Game Day and Evidence-Based Postmortem

## Purpose and Scope

> **Primary Objective:** Diagnose overlapping failures using all four signals, event and change evidence; restore safely, prove durable recovery, and write a defensible incident postmortem.

This final lab combines optional-cache degradation, loss of trace visibility and a required-database outage. You will keep a separate external request ledger, investigate symptoms through surviving signals, recover the platform, and reconstruct the incident from timestamps and known identifiers.

The game day is local and bounded. It does not delete volumes, corrupt data, add Kubernetes, or claim high availability. The expected fault plan is visible for reproducibility; for a two-person exercise one operator may run the controller while another investigates without watching its terminal. Solo execution is equally valid.

## 1. Prerequisites, Roles and Clean Start

```bash
source lab-notes/session.sh
source lab-notes/evidence.sh
source lab-notes/metrics-session.sh
source lab-notes/platform-session.sh
source lab-notes/traces-session.sh
source lab-notes/profiling/session.sh
load_app_settings
start_lab 50
umask 077
chmod 700 "$LAB_DIR"
bash lab-notes/operations/validate-platform.sh > "$LAB_DIR/config-validation.log" 2>&1
wait_ready
wait_grafana
wait_backend loki:3100 /ready
wait_backend tempo:3200 /ready
wait_backend pyroscope:4040 /ready
wait_backend otel-collector:13133 /
pq 'up' > "$LAB_DIR/targets-before.json"
dp ps > "$LAB_DIR/containers-before.txt"
```

Complete [Lab 49](Lab-49.md), including rollback to the original image/version, cleanup of restore targets and preservation of its protected backup. Keep Lab 46's safe database failure classification and Lab 44's scoped profiling correlation. Stop other load generators and leave all fourteen learning-stage services running.

The controller owns fault injection and recovery. The investigator owns hypotheses and evidence. The scribe records UTC changes, decisions and uncertainty; one person may perform all three roles. Success means explaining user impact and recovery from recorded evidence, not guessing the component names fastest.

**Abort conditions:** unexpected app restarts, host memory/disk exhaustion, a damaged backup, failure to restore a stopped service, or errors outside the three intended fault targets. Restore Redis, PostgreSQL and Tempo using `dp start redis postgres tempo`, stop the observer, preserve evidence, and investigate before repeating.

## 2. Objectives, Architecture and Predictions

| Phase | Intended change | Business prediction | Observability prediction |
|---|---|---|---|
| Baseline | No failure | Reads and readiness succeed | All signals visible |
| Cache and trace outage | Stop Redis and Tempo | Reads use PostgreSQL; readiness is degraded with HTTP 200 | Native metrics, Loki and profiles survive; trace export queues/retries |
| Database outage | Restore Redis, then stop PostgreSQL; Tempo still down | Liveness succeeds, readiness and uncached/list reads fail | Logs and native metrics describe impact while trace queries remain impaired |
| Recovery | Restore PostgreSQL, then Tempo | Durable rows and writes work again | Fresh and buffered trace delivery must be checked separately |

Metrics answer scope and timing. Logs record specific events, traces explain retained request paths, span events record milestones inside those paths, and profiles show sampled CPU work. A profile is not a request log. A failed trace query is not evidence that no request occurred. Operator changes supply context without becoming authenticated audit records.

**Prediction checkpoint:** predict the first visible symptom, the most reliable independent signal during the Tempo outage, whether cached item GETs can succeed during database failure, and which alerts may remain pending because the fault is shorter than their `for` duration. Save the predictions before running the controller.

The existing signal paths remain unchanged: direct Prometheus scraping; JSON stdout through Docker/Collector to Loki; app OTLP through Collector to Tempo; and direct SDK uploads to Pyroscope. Do not introduce duplicate telemetry to compensate for an outage.

## 3. Create a Known Durable Row and External Observer

```bash
PAYLOAD=$(jq -n --arg name "lab50-$(new_uuid)" '{name:$name,price:"50.00"}')
python3 lab-notes/operations/request_probe.py "$APP_URL" /api/v1/items "$LAB_DIR/created.json" \
  --method POST --body "$PAYLOAD" --expect 201
ITEM_ID=$(jq -er '.response.id' "$LAB_DIR/created.json")
printf '%s\n' "$ITEM_ID" > "$LAB_DIR/item-id.txt"
api -fsS "$APP_URL/api/v1/items/$ITEM_ID" > "$LAB_DIR/item-before.json"
docker inspect --format '{{.Id}} {{.State.StartedAt}} {{.RestartCount}}' "$(dp ps -q app)" > "$LAB_DIR/app-before.txt"
```

```bash
cat > lab-notes/operations/game_monitor.py <<'PYTHON'
"""Bounded, sequential external observer; expected outages remain evidence."""
import argparse,json,secrets,time,uuid
from pathlib import Path
from urllib.error import HTTPError,URLError
from urllib.request import ProxyHandler,Request,build_opener
p=argparse.ArgumentParser();p.add_argument('base_url');p.add_argument('item_id');p.add_argument('output')
p.add_argument('phase_file');p.add_argument('stop_file');p.add_argument('--seconds',type=int,default=240)
a=p.parse_args();assert 30<=a.seconds<=300
uuid.UUID(a.item_id)
paths=['/health/live','/health/ready','/api/v1/items','/api/v1/items/'+a.item_id]
opener=build_opener(ProxyHandler({}));deadline=time.monotonic()+a.seconds
with open(a.output,'x') as output:
    while time.monotonic()<deadline and not Path(a.stop_file).exists():
        for path in paths:
            if Path(a.stop_file).exists() or time.monotonic()>=deadline:break
            rid='game-'+str(uuid.uuid4());trace_id=secrets.token_hex(16)
            phase=Path(a.phase_file).read_text().strip()
            request=Request(a.base_url.rstrip('/')+path,headers={'X-Request-ID':rid,
                'traceparent':f'00-{trace_id}-{secrets.token_hex(8)}-01'})
            start=time.time_ns();status=0;body=None;failure=None;returned_id=None
            try:
                try:response=opener.open(request,timeout=5)
                except HTTPError as error:response=error
                with response:
                    status=response.status;raw=response.read();body=json.loads(raw) if raw else None
                    returned_id=response.headers.get('X-Request-ID')
            except (URLError,TimeoutError,OSError,ValueError) as error:
                failure=type(error).__name__
            output.write(json.dumps({'start_ns':start,'end_ns':time.time_ns(),'phase':phase,
                'path':path,'status':status,'request_id':rid,'returned_request_id':returned_id,
                'trace_id':trace_id,'response':body,'transport_error':failure})+'\n')
            output.flush()
        time.sleep(1)
PYTHON
```

The observer records every attempted liveness, readiness, list and fixture GET with timestamps, status, request ID, a synthetic sampled trace parent and response body. Expected HTTP 503 responses are evidence, not shell failures. Transport errors have status zero and a safe error type. Requests are sequential, pause between cycles and stop after a deadline or stop-file signal.

Health paths remain untraced. Ordinary successful business traces remain subject to tail sampling. CPU canaries use the deliberately retained diagnostic policy; their response IDs provide stronger delivery checks. The observer traffic is a small, controlled sample, not the whole service population.

## 4. Run the Bounded Multi-Fault Controller

```bash
cat > lab-notes/operations/game-day.sh <<'BASH'
game_day() (
  set -euo pipefail
  local phase_file="$LAB_DIR/phase.txt" stop_file="$LAB_DIR/observer.stop"
  local observer_pid=''
  phase() { printf '%s\n' "$1" > "$phase_file.next"; mv "$phase_file.next" "$phase_file"; }
  change() {
    python3 lab-notes/operations/change_event.py "$LAB_DIR/changes.jsonl" "$1" "$2" "$3" --reason "$4"
  }
  recover() {
    dp start redis postgres tempo >/dev/null || printf '%s\n' 'Recovery command failed; investigate immediately.' >&2
    touch "$stop_file"
    if [[ -n $observer_pid ]]; then wait "$observer_pid" || true; fi
  }
  trap recover EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  phase baseline
  python3 lab-notes/operations/game_monitor.py "$APP_URL" "$ITEM_ID" "$LAB_DIR/observer.jsonl" \
    "$phase_file" "$stop_file" --seconds 240 &
  observer_pid=$!
  change begin game-day started 'bounded local multi-fault exercise'
  python3 lab-notes/profiling/profile_load.py "$APP_URL" "$LAB_DIR/baseline-cpu.jsonl" \
    --variant cpu --count 8 --iterations 3000000
  sleep 15
  api -fsS "$APP_URL/metrics" > "$LAB_DIR/metrics-baseline.prom"
  change stop redis-and-tempo intended 'cache fallback and trace visibility loss'
  phase cache-and-traces
  dp stop -t 10 redis tempo
  python3 lab-notes/operations/request_probe.py "$APP_URL" /health/ready "$LAB_DIR/cache-ready.json" --expect 200
  jq -e '.response.status=="degraded" and .response.dependencies.redis=="down"' "$LAB_DIR/cache-ready.json"
  python3 lab-notes/profiling/profile_load.py "$APP_URL" "$LAB_DIR/cache-outage-cpu.jsonl" \
    --variant cpu --count 8 --iterations 3000000
  sleep 15
  api -fsS "$APP_URL/metrics" > "$LAB_DIR/metrics-cache-outage.prom"
  backend otel-collector:8888 /metrics > "$LAB_DIR/collector-cache-outage.prom"
  pq 'application_dependency_up' > "$LAB_DIR/dependencies-cache-outage.json"
  change start redis intended 'restore optional cache before database fault'
  dp start redis
  wait_ready
  change start redis verified 'readiness returned to ready'
  change stop postgres intended 'required persistence unavailable; Tempo remains stopped'
  phase database-and-traces
  dp stop -t 10 postgres
  python3 lab-notes/operations/request_probe.py "$APP_URL" /health/live "$LAB_DIR/db-live.json" --expect 200
  python3 lab-notes/operations/request_probe.py "$APP_URL" /health/ready "$LAB_DIR/db-ready.json" --expect 503
  python3 lab-notes/operations/request_probe.py "$APP_URL" /api/v1/items "$LAB_DIR/db-list.json" --expect 503
  python3 lab-notes/profiling/profile_load.py "$APP_URL" "$LAB_DIR/db-outage-cpu.jsonl" \
    --variant cpu --count 8 --iterations 3000000
  sleep 15
  api -fsS "$APP_URL/metrics" > "$LAB_DIR/metrics-db-outage.prom"
  backend otel-collector:8888 /metrics > "$LAB_DIR/collector-db-outage.prom"
  api -fsS "$PROM_URL/api/v1/alerts" > "$LAB_DIR/alerts-during.json"
  change start postgres intended 'restore required business dependency first'
  phase recovering
  dp start postgres
  wait_ready
  change start postgres verified 'business readiness restored'
  dp start tempo
  wait_backend tempo:3200 /ready
  change start tempo verified 'trace backend ready; delivery checks follow'
  phase recovered
  python3 lab-notes/profiling/profile_load.py "$APP_URL" "$LAB_DIR/recovered-cpu.jsonl" \
    --variant cpu --count 12 --iterations 3000000
  sleep 20
  api -fsS "$APP_URL/metrics" > "$LAB_DIR/metrics-recovered.prom"
  backend otel-collector:8888 /metrics > "$LAB_DIR/collector-recovered.prom"
  touch "$stop_file"
  wait "$observer_pid"
  observer_pid=''
  change end game-day recovered 'faults removed; independent recovery verification follows'
)
BASH
source lab-notes/operations/game-day.sh
game_day
```

The controller performs a finite amount of work and installs restoration traps before stopping anything. Its normal outage is short enough to study the configured retry behavior without deliberately exhausting disk or queues. The observer has its own four-minute bound; a stuck external Docker daemon is still an operator incident, not something a shell trap can guarantee to repair.

From another terminal, source the same session helpers and use `dp ps`, `pq` and Grafana while the controller runs. Do not call `start_lab` there if you intend to write into this run's directory; explicitly use the printed `LAB_DIR`. Record what you inferred before consulting `changes.jsonl`.

## 5. Establish Impact Before Investigating Causes

```bash
cat > lab-notes/operations/game_summary.py <<'PYTHON'
"""Summarize only the bounded observer population; never infer a monthly SLO."""
import collections,datetime,json,math,sys
from pathlib import Path
root=Path(sys.argv[1]);rows=[json.loads(line) for line in (root/'observer.jsonl').read_text().splitlines()]
assert rows,'No observer evidence'
assert {'baseline','cache-and-traces','database-and-traces','recovered'} <= {r['phase'] for r in rows},'Missing an intended observation phase'
def utc(ns):return datetime.datetime.fromtimestamp(ns/1e9,datetime.timezone.utc).isoformat()
phases={}
for phase in dict.fromkeys(row['phase'] for row in rows):
    selected=[r for r in rows if r['phase']==phase]
    eligible=[r for r in selected if r['path'].startswith('/api/v1/items')]
    failed=[r for r in eligible if r['status']==0 or r['status']>=500]
    durations=sorted((r['end_ns']-r['start_ns'])/1e6 for r in eligible)
    phases[phase]={'eligible_requests':len(eligible),'failed_requests':len(failed),
      'error_fraction':len(failed)/len(eligible) if eligible else None,
      'observed_p95_ms':durations[max(0,math.ceil(len(durations)*.95)-1)] if durations else None,
      'liveness_codes':dict(collections.Counter(str(r['status']) for r in selected if r['path']=='/health/live')),
      'readiness_states':dict(collections.Counter((r['response'] or {}).get('status','transport_error') for r in selected if r['path']=='/health/ready'))}
eligible=[r for r in rows if r['path'].startswith('/api/v1/items')]
failed=[r for r in eligible if r['status']==0 or r['status']>=500]
report={'observation_start':utc(rows[0]['start_ns']),'observation_end':utc(rows[-1]['end_ns']),
        'first_observed_business_failure':utc(failed[0]['start_ns']) if failed else None,
        'last_observed_business_failure':utc(failed[-1]['end_ns']) if failed else None,
        'eligible_requests':len(eligible),'failed_requests':len(failed),'phases':phases,
        'interpretation':'Controlled observer sample only; not production traffic or a monthly SLO.'}
(root/'impact-summary.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
PYTHON
```

```bash
python3 lab-notes/operations/game_summary.py "$LAB_DIR" > "$LAB_DIR/impact-summary-display.json"
cat "$LAB_DIR/impact-summary-display.json"
pq 'sum(rate(application_http_requests_total{route=~"/api/v1/items.*",status_code=~"5.."}[5m]))' \
  > "$LAB_DIR/business-error-rate.json"
pq 'application_dependency_up' > "$LAB_DIR/dependencies-after.json"
pq 'pg_up{job="postgres"}' > "$LAB_DIR/postgres-exporter-after.json"
pq 'redis_up{job="redis"}' > "$LAB_DIR/redis-exporter-after.json"
```

Compare the observer's liveness codes, readiness states and business failures with the native metrics. A successful `up` scrape means Prometheus reached that target; an exporter can be reachable while `pg_up` or `redis_up` reports its dependency unavailable. The app's dependency gauge is another observer with its own probe cadence.

The summary denominator includes only this observer's list and item reads. Health probes, CPU exercises and setup/cleanup writes are excluded. Status zero counts as failure for this bounded availability measurement; 5xx is a server failure. Report actual counts and the observation window. Do not extrapolate the exercise into a monthly error budget or invent customer counts.

The first failed sample bounds detection in this observation stream; it is not necessarily the exact start of user impact. Requests can straddle phase transitions. Five-minute PromQL rates mix phases and include other eligible traffic, whereas the JSONL summary groups individual requests by the phase when they began.

## 6. Use Logs, Span Events and Change Context to Test Hypotheses

```bash
RID=$(jq -er '.request_id' "$LAB_DIR/db-list.json")
START_NS=$(jq -er '.start_ns' "$LAB_DIR/created.json")
END_NS=$(python3 -c 'import time; print(time.time_ns())')
QUERY="{service_name=\"$LAB_SERVICE\",deployment_environment_name=\"$LAB_ENVIRONMENT\"} | json | request_id=\"$RID\""
backend loki:3100 /loki/api/v1/query_range query "$QUERY" start "$START_NS" end "$END_NS" limit 100 \
  > "$LAB_DIR/failed-request-logs.json"
jq -e '[.data.result[].values[]] | length>0' "$LAB_DIR/failed-request-logs.json"
dp logs --since "$(cat "$LAB_DIR/started-at.txt")" --no-color app > "$LAB_DIR/app-incident.log"
dp logs --since "$(cat "$LAB_DIR/started-at.txt")" --no-color otel-collector > "$LAB_DIR/collector-incident.log"
for phase in baseline cache-outage db-outage recovered; do
  TRACE_ID=$(jq -rs '.[0].response.trace_id' "$LAB_DIR/$phase-cpu.jsonl")
  if fetch_trace "$TRACE_ID" "$LAB_DIR/$phase-trace.json"; then
    python3 lab-notes/tracing/inspect_trace.py "$LAB_DIR/$phase-trace.json" > "$LAB_DIR/$phase-spans.json"
  else
    printf '%s\n' "$TRACE_ID not observed by query deadline" > "$LAB_DIR/$phase-trace-missing.txt"
  fi
done
```

Find the sanitized database failure log for the known request and the dependency transition records. The transition log may lag the first failed request because the background probe runs periodically. Repeated request failures are not the same event as one dependency state transition.

Inspect CPU-worker span events and their ordering in the normalized traces. Fetch the failed business request's `trace_id` from `db-list.json` separately: its error policy should retain it if transport delivery succeeds. A connection refusal may occur before a SQL query span exists; do not invent a query that was never executed.

Compare outage-period trace arrivals with fresh recovered arrivals. Delayed traces after Tempo returns support a buffering hypothesis; missing traces require checking rejected data, queue exhaustion, retry expiry or process loss. The known CPU policy reduces sampling ambiguity but does not eliminate transport uncertainty. Review the Collector snapshots from the outage as well as recovery; a drained final queue alone cannot prove zero loss.

Only after recording your evidence-supported hypothesis, align it with the operator journal. Distinguish an injected stop, the dependency becoming unavailable, the request failure, its log record, and the later alert-state change. These are related occurrences, not interchangeable timestamps.

## 7. Use Profiles to Explain Work and Validate Correlation

```bash
PROFILE_TYPE=$(cat lab-notes/profiling/profile-type.txt)
START_MS=$(jq -rs '(.[0].start_ns / 1000000 | floor)-10000' "$LAB_DIR/recovered-cpu.jsonl")
END_MS=$(python3 -c 'import time; print(time.time_ns()//1000000)')
SPAN_ID=$(jq -rs '.[0].response.span_id' "$LAB_DIR/recovered-cpu.jsonl")
SELECTOR="{service_name=\"$LAB_SERVICE\",environment=\"$LAB_ENVIRONMENT\",workload=\"cpu\"}"
BODY=$(jq -n --arg selector "$SELECTOR" --arg type "$PROFILE_TYPE" \
  --argjson start "$START_MS" --argjson end "$END_MS" \
  '{profileTypeID:$type,labelSelector:$selector,start:$start,end:$end}')
pquery SelectMergeStacktraces "$BODY" > "$LAB_DIR/recovered-profile.json"
jq -e '(.flamegraph.total // 0 | tonumber)>0' "$LAB_DIR/recovered-profile.json"
SPAN_BODY=$(jq --arg span "$SPAN_ID" '. + {spanSelector:[$span]}' <<< "$BODY")
pquery SelectMergeStacktraces "$SPAN_BODY" > "$LAB_DIR/recovered-span-profile.json"
jq '(.flamegraph.total // 0 | tonumber)' "$LAB_DIR/recovered-span-profile.json"
```

Use a ten-second margin around each ledger window to include upload buckets. Repeat the aggregate query with each outage CPU ledger's timestamps, and keep those aggregate results separate from exact-span recovery evidence. Profiles travel directly to Pyroscope, so they can remain available while Tempo is down. Compare CPU loop frames and reported `thread_cpu_ms`/`wall_ms`; do not blame CPU saturation for a database connection failure merely because an unrelated diagnostic loop appears in a profile.

An individual span can receive zero statistical samples. If the first span profile is empty, select another recorded worker span from the same bounded batch and report that sampling limitation. An aggregate nonzero profile is required to prove collection; a nonzero exact-span result proves the stronger correlation introduced in Lab 44. Verify that `pyroscope.profile.id` in the retained worker span matches the selected span ID before using Grafana's trace-to-profile navigation.

## 8. Prove Business, Storage and Telemetry Recovery

```bash
wait_ready
wait_grafana
wait_backend loki:3100 /ready
wait_backend tempo:3200 /ready
wait_backend pyroscope:4040 /ready
wait_backend otel-collector:13133 /
docker inspect --format '{{.Id}} {{.State.StartedAt}} {{.RestartCount}}' "$(dp ps -q app)" > "$LAB_DIR/app-after.txt"
diff -u "$LAB_DIR/app-before.txt" "$LAB_DIR/app-after.txt"
rcli DEL "$(cache_key "$ITEM_ID")" > /dev/null
api -fsS "$APP_URL/api/v1/items/$ITEM_ID" > "$LAB_DIR/item-recovered.json"
jq -e '.price=="50.00"' "$LAB_DIR/item-recovered.json"
UPDATE=$(jq '{name,description,price:"51.00"}' "$LAB_DIR/item-recovered.json")
python3 lab-notes/operations/request_probe.py "$APP_URL" "/api/v1/items/$ITEM_ID" "$LAB_DIR/update-recovered.json" \
  --method PUT --body "$UPDATE" --expect 200
rcli DEL "$(cache_key "$ITEM_ID")" > /dev/null
api -fsS "$APP_URL/api/v1/items/$ITEM_ID" > "$LAB_DIR/item-updated-from-db.json"
jq -e '.price=="51.00"' "$LAB_DIR/item-updated-from-db.json"
wait_metric 'pg_up{job="postgres"}' 1
wait_metric 'redis_up{job="redis"}' 1
wait_metric 'application_dependency_up{dependency="postgres"}' 1
wait_metric 'application_dependency_up{dependency="redis"}' 1
pq 'up' > "$LAB_DIR/targets-final.json"
api -fsS "$PROM_URL/api/v1/alerts" > "$LAB_DIR/alerts-final.json"
api -fsS "${ALERTMANAGER_URL:-http://127.0.0.1:9093}/api/v2/alerts" > "$LAB_DIR/alertmanager-final.json"
dp ps > "$LAB_DIR/containers-final.txt"
python3 lab-notes/operations/request_probe.py "$APP_URL" "/api/v1/items/$ITEM_ID" "$LAB_DIR/delete-fixture.json" \
  --method DELETE --expect 204
python3 lab-notes/operations/request_probe.py "$APP_URL" "/api/v1/items/$ITEM_ID" "$LAB_DIR/fixture-absent.json" --expect 404
python3 lab-notes/operations/change_event.py "$LAB_DIR/changes.jsonl" verify platform completed \
  --reason 'durable read/write, unchanged app process, dependency and telemetry checks recorded'
```

Check every configured scrape target, not merely the availability of the Prometheus HTTP endpoint. Compare pending/firing/resolved alert states with actual thresholds and evaluation intervals. A short fault may never satisfy `for`; this is not automatically an alerting defect. A recovered service can also leave a rate-based alert active until its window ages out.

Use the recovered CPU request ID to query a fresh Loki record and its returned trace ID to verify a fresh retained trace, alongside the fresh profile and native metrics. Separate those checks from the fate of older buffered data. Confirm the observer exited, all intended faults were removed, and no app restart was used to conceal a pool/recovery problem.

## 9. Write the Evidence-Based Postmortem

Create `postmortem.md` inside this run's `LAB_DIR`. Use the structure below and replace each instruction with your measured finding and an evidence-file reference. Do not manufacture timings, lost-record counts or customer impact to make the report appear complete.

```markdown
# Reliability Game Day Postmortem

## Executive Summary
Describe the observed business impact, visibility impairment, restoration and current state.

## Scope and Impact
State the observer window, eligible request count, failures and denominator exclusions.
Separate cached-read availability from required database functionality.
Explain what real customer impact cannot be inferred from this controlled sample.

## Timeline in UTC
| Time | Observation or action | Evidence | Interpretation and confidence |
|---|---|---|---|

Include the last healthy observation, each change, first detected symptom,
mitigation decisions, business recovery and verified telemetry recovery.

## Detection and Investigation
Explain the first useful signal, misleading indicators and competing hypotheses.
Reference metrics, specific log records, trace/span events and profile evidence.

## Causal Analysis
Separate the injected triggering actions from application behavior and contributing conditions.
Explain cache fallback, required persistence, connection handling and trace buffering.
Distinguish the actual failure from delayed or missing records about that failure.

## Recovery and Data Integrity
Cite uncached reads, successful writes, exporter/dependency health and process identity.
State what the backup/restore checkpoint would protect if state recovery were necessary.

## Telemetry Gaps and Uncertainty
Identify unavailable queries, delayed delivery, unknown loss and sampling limitations.
Do not label all missing traces as lost or all recovered queues as lossless.

## What Helped and What Slowed Diagnosis
Tie each finding to an observed decision or evidence gap.

## Corrective Actions
| Priority | Concrete action | Owner | Due date | Verification | Expected benefit |
|---|---|---|---|---|---|

Choose a small number of actions addressing demonstrated weaknesses.

## Evidence Index
Map each claim to an artifact, request ID, trace/span ID, query window or change event.

## Follow-Up Review
State who will verify completed actions and when the exercise will be repeated.
```

A strong causal statement connects mechanism and evidence: the required persistence dependency was stopped, uncached reads returned the safe database error, liveness stayed healthy, and uncached reads recovered after PostgreSQL returned. A weak statement says only “the database was down.”

For corrective actions, prefer measurable changes such as a tested external check for monitoring availability, a retry-budget alert validated against real queue metrics, or a documented restore rehearsal cadence. Assign a real owner and date yourself. “Improve monitoring” and “be more careful” are not verifiable actions.

Do not blame an operator for following the agreed fault plan. The exercise evaluates system behavior and response practices. Review whether the prewritten runbooks explained the observed cache-hit exception and the distinction between user recovery and telemetry recovery.

## 10. Troubleshooting and Optional Extensions

| Unexpected result | Check before changing the system |
|---|---|
| Redis outage makes readiness HTTP 503 | PostgreSQL health, accidental extra fault, inherited readiness implementation |
| DB outage causes liveness failure | App process identity, event-loop starvation, host pressure; abort if outside scope |
| Cached GET succeeds while readiness fails | TTL and cache key; force an uncached read before concluding DB recovery |
| No trace for successful item GET | Tail policy versus delivery; use the retained CPU canary |
| No failure alert fired | Rule expression, actual evaluation timestamps and `for` duration |
| Profile does not explain failed request latency | CPU sampling measures executing work, not all blocked wall time |
| Restore commands do not recover a target | Configuration, disk and service logs; preserve the failure and use Lab 49 checkpoints deliberately |
| Timeline appears inconsistent | UTC conversion, clock synchronization, request start/end and ingestion delay |

After completing the required run, repeat once with an investigator who has not seen the controller timing. Keep the same bounded fault set and compare diagnosis time and evidence quality. Do not escalate to disk deletion, arbitrary network disruption or unbounded CPU load as an “advanced” extension.

A second useful exercise is to review the postmortem against the evidence index. Ask another reader to challenge one causal claim, one impact calculation and one loss claim. Correct unsupported statements and record what extra measurement would settle the uncertainty.

## 11. Knowledge Check

1. Why separate business recovery from telemetry recovery?
2. Why is an event not the same thing as its log record?
3. What can the observer error fraction legitimately describe?
4. What makes a corrective action testable?

### Answer Guide

1. Requests may recover while queues drain, old records remain missing or a backend still cannot answer queries.
2. The occurrence can happen before its record is emitted, transported, ingested or lost; each has a different timestamp and guarantee.
3. Only the explicitly measured request population and interval, with its exclusions and sampling limitations.
4. A specific change, accountable owner, due date, observable verification and a stated expected benefit.

## 12. Professional Scenario Exercise

Present a ten-minute incident review to an engineering team. Begin with measured user-facing behavior, then show the smallest set of evidence that explains the mechanism. State one conclusion you cannot support and the measurement required to support it. Finish by asking reviewers to assess the corrective actions against the observed failure modes.

## 13. Lab Notebook Template

Create a notebook in the evidence directory for this run:

```bash
test -e "$LAB_DIR/notebook.md" || printf '# Lab 50 Evidence\n' > "$LAB_DIR/notebook.md"
```

Use these headings and fill them with your predictions, commands, observed results and conclusions:

```markdown
# Lab 50 Evidence

## Preflight and predictions
## Role assignments and observation window
## Impact calculation
## UTC incident timeline
## Hypotheses and correlated evidence
## Buffering and loss uncertainty
## Recovery and data integrity
## Postmortem review and follow-up actions
```

## 14. Observable Completion Criteria

- [ ] Predictions and clean-start gates were saved before injecting faults.
- [ ] Redis/Tempo overlap and the later PostgreSQL outage were measured with an independent observer.
- [ ] Business impact uses an explicit denominator and honest time bounds.
- [ ] Metrics, specific event records, traces/span events, profiles and change records support the investigation.
- [ ] Recovery includes unchanged app process identity, uncached durable reads and a successful write.
- [ ] All targets, known-ID telemetry and fixture cleanup are checked.
- [ ] A complete postmortem links claims to evidence and assigns verifiable corrective actions.

## 15. Production Implications

Professional reliability work combines technical instrumentation with controlled changes, safe recovery, explicit uncertainty and learning from incidents. A single-node game day cannot establish availability guarantees, but it can expose weak assumptions before a larger deployment. Protect the evidence, periodically rehearse recovery, and test corrective actions under realistic constraints.

## 16. End State and Transition

This completes the fifty-lab curriculum. The platform is healthy, the baseline release is active, lab-owned fixtures and temporary restore targets are removed, and backup/evidence artifacts are retained securely. Your final deliverable is the completed postmortem and a verified action list, supported by a repository you can inspect, operate and extend.
