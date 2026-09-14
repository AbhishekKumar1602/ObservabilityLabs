# Lab 38: Head Sampling and Diagnostic Loss

## Purpose and Scope

> **Primary Objective:** Measure how head-sampling ratios change retained trace and span-event evidence while requests, logs and native metrics continue.

Sampling is a deliberate reduction in trace evidence. A head sampler decides before an operation's duration or eventual failure is known. This lab compares 100%, 25% and 0% root sampling using the same small scenario workload and known trace IDs.

    You will distinguish SDK sampling decisions from backend retention, quantify lost spans/events, and demonstrate parent-based behavior. Tail sampling, Collector queue faults and cost claims based on guessed disk compression are excluded. The normal end state restores full head sampling for Lab 39.

## 1. Inherited State and Starting Checks

Complete [Lab 37](Lab-37.md) first. Run from the repository root in one Bash session; keep the existing credentials, named volumes, checkpoint item, dashboards and earlier evidence.

```bash
source lab-notes/session.sh
source lab-notes/evidence.sh
source lab-notes/metrics-session.sh
source lab-notes/platform-session.sh
source lab-notes/traces-session.sh
load_app_settings
start_lab 38
dp config --quiet
dp ps -a
wait_ready
wait_backend loki:3100 /ready
wait_backend tempo:3200 /ready
wait_backend otel-collector:13133 /
wait_backend lab-downstream:8001 /health/live
````dp` is the stage-aware Compose helper from Lab 31. It preserves the learning overlays and the current project. Plain `docker compose up` would use a different set of settings. `start_lab` creates a new `LAB_DIR`; all observations in this guide belong to that directory. Host tools remain Bash, Python 3, curl and jq; YAML edits use the isolated `lab-notes/.tools/bin/python` environment already installed in Lab 31.

The inherited platform has thirteen services, nine Prometheus scrape jobs and four existing dashboards. These labs add no service or scrape job. Pyroscope remains disabled until the profiling phase. The application still exposes native Prometheus metrics; traces travel through the Collector; Docker's fluentd logging driver sends JSON stdout to the same Collector and then Loki.

```bash
cp lab-notes/compose.auto-tracing.yaml "$LAB_DIR/compose.auto-tracing.yaml.before"
lab-notes/.tools/bin/python - <<'CHECK'
import yaml
c=yaml.safe_load(open('lab-notes/tracing/collector.yml'))
assert not any(p.startswith('tail_sampling') for p in c['service']['pipelines']['traces']['processors'])
CHECK
```

Stop other learning load generators during each measurement window. Do not stop dependency health monitoring. The audit considers only this run’s known request IDs, so unrelated background traces are not its denominator. Record each planned application recreation as a change event in your notebook; existing startup/error alerts may observe these changes.

## 2. Learning Objectives and Sampling Predictions

The application uses `ParentBased(TraceIdRatioBased(ratio))`. An incoming valid parent can determine the sampling decision; the root ratio applies when there is no parent. The Lab 36 standard-library caller sends no `traceparent`, so each request is an independent root. The downstream service respects that propagated decision.

| Root ratio | Expected behavior for 60 calls | Diagnostic implication |
|---|---|---|
| 1.0 | All 60 root traces sampled | Full controlled baseline, subject to export success |
| 0.25 | About 15 roots sampled | Some slow/error traces and their events are absent |
| 0.0 | No roots sampled | Requests/logs/metrics continue; trace detail is unavailable |

**Prediction checkpoint:** normal, slow and error requests should all be subject to the same head probability. A later error cannot reverse a root DROP decision. Logs can still contain a valid trace ID with `trace_sampled=false`; that ID is correlation context, not a promise of a retrievable trace.

For 60 independent roots at 25%, the expected count is 15 and the binomial standard deviation is about 3.35. Do not fail the lab because the observed count is not exactly 15. A twenty-request error subgroup has an expected five sampled traces with substantial variation.

## 3. Make the Existing Root Ratio Explicitly Switchable

```bash
lab-notes/.tools/bin/python - <<'PYTHON'
from pathlib import Path
import yaml
path=Path('lab-notes/compose.auto-tracing.yaml')
config=yaml.safe_load(path.read_text())
config['services']['app']['environment']['TRACE_SAMPLE_RATIO']='${LAB_HEAD_SAMPLE_RATIO:-1.0}'
path.write_text(yaml.safe_dump(config,sort_keys=False))
PYTHON
set_head_ratio() {
  case "$1" in 1.0|0.25|0.0) ;; *) echo 'Use 1.0, 0.25 or 0.0' >&2; return 2 ;; esac
  export LAB_HEAD_SAMPLE_RATIO="$1"
  dp config --quiet || return
  dp up -d --no-deps --force-recreate app || return
  wait_ready || return
  dp exec -T app python - "$1" <<'CHECK'
import sys
from app.config import Settings
s=Settings()
assert s.otel_enabled and s.trace_sample_ratio==float(sys.argv[1])
print('Active root ratio:',s.trace_sample_ratio)
CHECK
}
```

This edits the overlay already loaded by `dp`; a new arbitrary overlay filename would not be included automatically. `restart` would retain the old environment, so the function recreates only the app. It prints the ratio, not the resolved environment or credentials.

The downstream root sampler stays at 1.0. During correctly propagated calls it sees a parent and honors the upstream decision. If Lab 35's context-stripping fault is active, it can start independent sampled roots and invalidate the experiment. The workload's cross-service assertions detect this problem.

## 4. Install a Known-ID Retention Audit

```bash
cat > lab-notes/tracing/retention_audit.py <<'PYTHON'
"""Run inside app: inspect known trace IDs without exposing Tempo's port."""
import argparse
import concurrent.futures
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

parser = argparse.ArgumentParser()
parser.add_argument('ledger_jsonl')
parser.add_argument('--wait', type=int, default=20)
parser.add_argument('--require-all', action='store_true')
args = parser.parse_args()
rows = [json.loads(line) for line in args.ledger_jsonl.splitlines() if line.strip()]
assert 1 <= len(rows) <= 90 and 1 <= args.wait <= 60
states = {r['response']['upstream_trace_id']: {
    'trace_id': r['response']['upstream_trace_id'], 'scenario': r['scenario'],
    'request_id': r['request_id'], 'head_sampled': r['response']['trace_sampled'],
    'found': False, 'complete': False, 'spans': 0, 'events': 0, 'query_bytes': 0,
    'last_http': None, 'lookup_error': None} for r in rows}
assert len(states) == len(rows), 'Each call must be a distinct root'


def lookup(trace_id):
    request = Request('http://tempo:3200/api/traces/' + trace_id, headers={'Accept': 'application/json'})
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(request, timeout=2) as response:
            raw = response.read()
        data = json.loads(raw)
        spans = [span for batch in data.get('batches', data.get('resourceSpans', []))
                 for scope in batch.get('scopeSpans', batch.get('instrumentationLibrarySpans', []))
                 for span in scope.get('spans', [])]
        names = {span['name'] for span in spans}
        return trace_id, {'found': bool(spans), 'complete': len(spans) >= 5 and
            {'demo.evaluate', 'demo.compute'} <= names, 'spans': len(spans),
            'events': sum(len(span.get('events', [])) for span in spans),
            'query_bytes': len(raw), 'last_http': 200, 'lookup_error': None}
    except HTTPError as error:
        return trace_id, {'last_http': error.code,
                          'lookup_error': None if error.code == 404 else 'HTTPError'}
    except (URLError, TimeoutError, ValueError) as error:
        return trace_id, {'lookup_error': type(error).__name__}


deadline = time.monotonic() + args.wait
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
    while True:
        pending = [key for key, state in states.items() if not state['complete']]
        if not pending:
            break
        for key, result in pool.map(lookup, pending):
            states[key].update(result)
        if time.monotonic() >= deadline:
            break
        time.sleep(1)
result = {'requested': len(rows), 'head_sampled': sum(s['head_sampled'] for s in states.values()),
          'found': sum(s['found'] for s in states.values()),
          'complete': sum(s['complete'] for s in states.values()),
          'span_count': sum(s['spans'] for s in states.values()),
          'event_count': sum(s['events'] for s in states.values()),
          'traces': list(states.values())}
print(json.dumps(result, indent=2))
if any(s['lookup_error'] for s in states.values()):
    raise SystemExit('Some lookups failed; do not interpret them as sampling loss')
if args.require_all and result['complete'] != result['requested']:
    raise SystemExit('Not all known traces are complete; inspect export/ingestion evidence')
PYTHON
```

This script runs inside the existing app container, where `tempo:3200` resolves. It uses only the standard library, bounded concurrent lookups and a polling deadline. The JSONL ledger is passed as one quoted argument; it contains synthetic IDs and scenario metadata, not secrets.

`found` means some spans arrived; `complete` additionally requires both custom names and at least five spans. HTTP 404 remains distinct from a connection failure or backend 5xx. Repeated 404 after a healthy baseline and a known head DROP is expected diagnostic absence; 404 alone is not proof of why a trace is missing. Large real traces need stronger completeness checks than this deliberately fixed topology.

`query_bytes` measures returned JSON bytes as a payload-size proxy. It is not compressed Tempo block size, WAL size, billing or wire bytes sent by OTLP/gRPC.

## 5. Run Three Small, Comparable Experiments

```bash
# If a command fails, run set_head_ratio 1.0 before leaving this lab.
for ratio in 1.0 0.25 0.0; do
  set_head_ratio "$ratio"
  api -fsS "$APP_URL/metrics" > "$LAB_DIR/metrics-$ratio.before.prom"
  python3 lab-notes/tracing/scenario_load.py "$APP_URL" "$LAB_DIR/head-$ratio.jsonl" --count 60
  api -fsS "$APP_URL/metrics" > "$LAB_DIR/metrics-$ratio.after.prom"
  LEDGER_JSON=$(cat "$LAB_DIR/head-$ratio.jsonl")
  dp exec -T app python - "$LEDGER_JSON" --wait 25 < lab-notes/tracing/retention_audit.py > "$LAB_DIR/audit-$ratio.json"
  jq '{requested,head_sampled,found,complete,span_count,event_count}' "$LAB_DIR/audit-$ratio.json"
done
```

Allow SDK batching and Tempo ingestion time. The 25-second observation window is deliberately longer than normal export intervals, but it is not a delivery guarantee. Investigate a sampled-but-incomplete trace using Collector/SDK evidence before assigning it to sampling loss.

The app counter resets on each recreation. Compare before/after snapshots **within** a ratio run, never subtract raw values across container recreations. With no competing caller, the route should gain 60 requests at every ratio. Each run includes twenty intentional 502 responses; these affect the existing error metrics and can legitimately trip learning alerts.

## 6. Quantify Evidence Reduction and Failure Coverage

```bash
python3 - "$LAB_DIR" <<'CHECK'
import json,sys
from pathlib import Path
root=Path(sys.argv[1])
baseline=json.loads((root/'audit-1.0.json').read_text())
assert baseline['head_sampled']==60 and baseline['complete']==60, 'Resolve baseline delivery first'
print('ratio requests SDK-kept complete spans events span-reduction% JSON-bytes-proxy')
for ratio in ['1.0','0.25','0.0']:
    data=json.loads((root/f'audit-{ratio}.json').read_text())
    assert not any(s['lookup_error'] for s in data['traces'])
    reduction=100*(1-data['span_count']/baseline['span_count'])
    print(ratio,data['requested'],data['head_sampled'],data['complete'],data['span_count'],
          data['event_count'],round(reduction,1),sum(s['query_bytes'] for s in data['traces']))
    for scenario in ['normal','slow','error']:
        rows=[s for s in data['traces'] if s['scenario']==scenario]
        print(' ',scenario,'stored complete',sum(s['complete'] for s in rows),'of',len(rows))
    if ratio=='0.0':
        assert data['head_sampled']==0 and data['found']==0
CHECK
```

Record both the benefit and the missing evidence: retained span/event counts, relative span-volume reduction, JSON-size proxy and error-trace coverage. For example, if only five of twenty error traces survive, fifteen failures have lost their detailed trace path and span events even though all twenty HTTP failures occurred. Report your actual count; do not copy that example as an expected result.

Tempo disk growth includes compression, block overhead, WAL, compaction and unrelated traffic. A measured reduction in stored trace payload is evidence for potential storage savings, not an exact disk-cost percentage. To measure physical savings later, isolate a repeatable workload in equal retention windows and account for compaction; do not delete this shared learning volume for a cleaner graph.

Head sampling does not necessarily reduce independent log volume. Trace events are part of discarded spans, so they disappear with their traces. The safe completion log can remain available even where the `work.rejected` event is unavailable.

## 7. Prove Logs and Native HTTP Metrics Remain

```bash
rg 'application_http_requests_total.*route="/api/v1/demo/downstream"' "$LAB_DIR/metrics-0.0.after.prom"
jq -rs '.[0] | {request_id,trace_id:.response.upstream_trace_id,sampled:.response.trace_sampled}' "$LAB_DIR/head-0.0.jsonl"
```

The metrics snapshot should contain the route-template counter, normally 40 HTTP 200 and 20 HTTP 502 requests for this isolated run. Confirm the before values; another caller could change the exact counts.

In Loki, search the zero-ratio request ID or trace ID using JSON parsing on the established service/environment streams. Confirm the completion record and `trace_sampled=false`. Clicking its Tempo link is expected to find no recorded trace. Do not remove trace IDs from logs merely because sampling exists; they can still correlate records across services.

The Prometheus request counter remains the denominator for service error rates. The number of traces returned by Tempo cannot replace it, especially after outcome-dependent sampling in Lab 39.

## 8. Test the Parent-Based Exception Deliberately

```bash
REMOTE_TRACE=$(python3 -c 'import secrets; print(secrets.token_hex(16))')
REMOTE_PARENT=$(python3 -c 'import secrets; print(secrets.token_hex(8))')
api -fsS "$APP_URL/api/v1/demo/downstream?scenario=normal" \
  -H "traceparent: 00-$REMOTE_TRACE-$REMOTE_PARENT-01" > "$LAB_DIR/remote-parent.json"
jq -e '.trace_sampled==true and .operation_recording==true' "$LAB_DIR/remote-parent.json"
fetch_trace "$REMOTE_TRACE" "$LAB_DIR/remote-parent-trace.json"
```

The app is still at root ratio 0.0. A valid sampled remote parent is nevertheless honored by this ParentBased sampler. This request is deliberately excluded from the sixty-root ledger. The caller does not export its synthetic parent span, so its absence in the waterfall is known context, not a new data-loss incident.

At an untrusted boundary, allowing callers to force trace recording has cost and trust implications. Decide where incoming context is accepted or replaced. Do not use the sampled bit or trace ID as authorization. A non-parent-based configuration has different semantics; document it rather than assuming all samplers behave this way.

## 9. Restore Full Head Sampling and Prove Recovery

```bash
set_head_ratio 1.0
unset LAB_HEAD_SAMPLE_RATIO
python3 lab-notes/tracing/scenario_load.py "$APP_URL" "$LAB_DIR/recovered.jsonl" --count 3
LEDGER_JSON=$(cat "$LAB_DIR/recovered.jsonl")
dp exec -T app python - "$LEDGER_JSON" --wait 25 --require-all < lab-notes/tracing/retention_audit.py > "$LAB_DIR/recovered-audit.json"
jq '{requested,head_sampled,complete,event_count}' "$LAB_DIR/recovered-audit.json"
wait_ready
```

The overlay's default is 1.0, so unsetting the shell override preserves the same behavior on the next recreation. Retain the switchable overlay and audit helper for later labs.

| Symptom | Likely distinction to investigate |
|---|---|
| 25% keeps exactly every request | Instrumented caller or incoming sampled parent; verify no parent header. |
| Downstream remains sampled at root ratio zero | Context lost or deliberately stripped; restore propagation. |
| Sampled flag true but Tempo missing | SDK/export/Collector/backend loss or arrival delay, not evidence that head sampling dropped it. |
| App setting stays unchanged | Recreate the container and inspect typed Settings; restart alone is insufficient. |
| Counter seems to decrease | Process restart reset; use within-run snapshots or reset-aware rates. |
| Zero-ratio trace link fails | Expected when logs carry context without retained trace data. |

## 10. Knowledge Check

1. Why can a head sampler not preferentially keep a failure discovered at the end?
2. Why should a 25% sample not retain exactly 15 of 60 requests every time?
3. Is `trace_sampled=true` a storage acknowledgement?
4. Does reducing trace volume imply the same percentage reduction in logs or disk usage?

### Answer Guide

1. The decision is made when the root begins, before the eventual result is known.
2. Sampling is probabilistic over distinct root IDs; small populations have substantial variance.
3. No. It is the propagated sampling decision; later transport or backend failures can still lose data.
4. No. Logs have independent ownership, and physical storage has compression, overhead and other traffic.

## 11. Professional Scenario Exercise

Your team proposes 1% head sampling after a storage alert. Use this experiment to estimate error-evidence coverage for a rare failure and state what native metrics and logs would still establish. Recommend a trial with measurable retention and diagnostic criteria; do not promise that the next rare incident will contain a trace.

## 12. Lab Notebook Template

Create a notebook in the evidence directory for this run:

```bash
test -e "$LAB_DIR/notebook.md" || printf '# Lab 38 Evidence\n' > "$LAB_DIR/notebook.md"
```

Use these headings and fill them with your predictions, commands, observed results and conclusions:

```markdown
# Lab 38 Evidence

## Prediction and starting state
## Configuration and bounded workload
## Evidence with timestamps and identifiers
## Explanation and competing hypotheses
## Recovery proof
## Production decision and remaining uncertainty
```

## 13. Observable Completion Criteria

- [ ] Sixty independent roots are measured at each of three ratios.
- [ ] The baseline has complete retained traces before reductions are interpreted.
- [ ] Span/event reduction and scenario-specific diagnostic coverage are quantified.
- [ ] Logs and native metrics remain observable for unsampled requests.
- [ ] A sampled remote parent demonstrates ParentBased behavior at root ratio zero.
- [ ] Ratio 1.0 is restored and a complete three-scenario canary succeeds.

## 14. Production Implications

Select head sampling with an explicit diagnostic budget. Sampling reduces SDK/export/backend trace work, but propagation, middleware, logs and request metrics still have cost. Parent trust and consistent propagation matter as much as the configured root ratio. See [OpenTelemetry sampling concepts](https://opentelemetry.io/docs/concepts/sampling/) and [Python sampler API](https://opentelemetry-python.readthedocs.io/en/latest/sdk/trace.sampling.html).

## 15. End State and Transition

Keep head sampling at 1.0, preserve the ledger/audit tooling and leave all existing telemetry paths healthy. [Lab 39](Lab-39.md) moves the retention decision to the Collector so observed errors and high latency can influence it.
