# Lab 41: Tempo Span Metrics, Service Graphs, and Exemplars

## Purpose and Scope

> **Primary Objective:** Generate metrics from retained spans, inspect service relationships, and follow a metric exemplar to its trace without confusing sampled telemetry with the service SLI.

Labs 38–40 established that a trace can be dropped at several boundaries. This lab puts that lesson to work: Tempo generates a second, explicitly named family of diagnostic metrics from the spans it actually receives. FastAPI's native metrics remain the source for request totals, latency SLIs and error rates.

You will extend the existing single-node Tempo deployment, enable Prometheus remote-write reception, compare full-trace and tail-sampled workloads, and configure Grafana service graphs and exemplars. No second application metric exporter, Kafka cluster or additional Collector is introduced.

## 1. Inherited State and Starting Checks

Complete [Lab 40](Lab-40.md), including its recovery checks. Run from the repository root in one Bash session. Preserve credentials, named volumes and earlier evidence.

```bash
source lab-notes/session.sh
source lab-notes/evidence.sh
source lab-notes/metrics-session.sh
source lab-notes/platform-session.sh
source lab-notes/traces-session.sh
load_app_settings
start_lab 41
dp config --quiet
dp ps -a
wait_ready
wait_backend tempo:3200 /ready
wait_backend otel-collector:13133 /
wait_backend lab-downstream:8001 /health/live
dp exec -T app python - <<'CHECK'
from app.config import Settings
assert Settings().trace_sample_ratio == 1.0
CHECK
```

`dp` is the stage-aware Compose helper. Continue using it: plain `docker compose up` would omit the learning overlays. `start_lab` creates a fresh evidence directory in `LAB_DIR`. The host tools are Bash, Python 3, curl, jq and the existing PyYAML environment at `lab-notes/.tools/bin/python`.

The inherited platform has thirteen running services and nine scrape jobs. Pyroscope is still disabled. Tempo 3.0.3 runs with `target: all`; Prometheus is 3.14.0, Grafana 13.2.2, and the Collector contrib distribution is 0.160.0. Keep the repository's pins. Tempo's monolithic mode supports its metrics generator directly; the Kafka requirements of other Tempo 3 deployment modes do not apply here.

Check that Lab 40's tiny queue and memory-fault settings were restored. Stop other learning load generators so the experiment's numerator and denominator refer to the same workload.

## 2. Learning Objectives and Signal Ownership

| Signal | Producer and transport | Question it can answer |
|---|---|---|
| `application_http_requests_total` | FastAPI → `/metrics` → Prometheus | How many completed requests did this process observe? |
| `traces_spanmetrics_calls_total` | Retained spans → Tempo generator → remote write | What operations occurred in the retained trace population? |
| `traces_service_graph_request_total` | Paired client/server spans → Tempo generator | Which instrumented services communicated? |
| Histogram exemplar | Tempo generator → Prometheus → Grafana | Which retained trace illustrates one observation? |

An exemplar is a trace reference attached to an observation; it is not an extra time-series label. A service graph is inferred from telemetry, not an authoritative inventory of every network dependency. Missing spans, uninstrumented components and time limits can leave missing or virtual edges.

**Prediction checkpoint:** with full head recording and tail sampling bypassed briefly, the selected server-span count should track the known workload. After restoring error/latency/10% tail policies, the trace-derived error proportion should rise because ordinary successful traces are preferentially discarded. The native request error proportion should stay close to one third for this lab's equal normal/slow/error mix.

## 3. Back Up and Enable the Metrics Generator

```bash
cp lab-notes/tracing/tempo.yml "$LAB_DIR/tempo.before.yml"
cp lab-notes/tracing/collector.yml "$LAB_DIR/collector.before.yml"
cp lab-notes/compose.metrics.yaml "$LAB_DIR/compose.metrics.before.yaml"
cp -a config/grafana/learning/provisioning/datasources "$LAB_DIR/datasources.before"
```

The following editor preserves existing Tempo receivers, storage, retention flags and datasource UIDs. The generator writes its own WAL under the existing Tempo volume. Classic histogram buckets match the PromQL below; the series cap keeps a small VM from silently accepting unbounded cardinality. No request, item, trace or span ID becomes a metric dimension.

```bash
cat > lab-notes/tracing/configure_span_metrics.py <<'PYTHON'
"""Enable Tempo's own metrics generator; leave FastAPI's native metric pipeline intact."""
from pathlib import Path
import yaml

path=Path('lab-notes/tracing/tempo.yml')
config=yaml.safe_load(path.read_text())
assert config['target']=='all'
config['metrics_generator']={
    'registry': {'collection_interval':'5s','external_labels':{'source':'tempo'}},
    'processor': {
        'service_graphs': {'wait':'15s','max_items':1000,'workers':2},
        'span_metrics': {'histogram_buckets':[0.005,0.01,0.025,0.05,0.1,0.2,0.3,0.5,1,2.5,5],
                         'enable_target_info':False}},
    'storage': {'path':'/var/tempo/generator/wal','remote_write':[
        {'url':'http://prometheus:9090/api/v1/write','send_exemplars':True}]},
    'metrics_ingestion_time_range_slack':'2m'}
config.setdefault('overrides',{}).setdefault('defaults',{}).setdefault('metrics_generator',{}).update({
    'processors':['service-graphs','span-metrics'],
    'max_active_series':10000,'generate_native_histograms':'classic'})
path.write_text(yaml.safe_dump(config,sort_keys=False))
path=Path('lab-notes/compose.metrics.yaml');config=yaml.safe_load(path.read_text())
command=config['services']['prometheus']['command']
for flag in ['--web.enable-remote-write-receiver','--enable-feature=exemplar-storage']:
    if flag not in command: command.append(flag)
path.write_text(yaml.safe_dump(config,sort_keys=False))
# Find the existing datasources rather than assuming a provisioned filename.
entries=[]
for path in Path('config/grafana/learning/provisioning/datasources').glob('*.y*ml'):
    document=yaml.safe_load(path.read_text())
    for ds in document.get('datasources',[]): entries.append((path,document,ds))
prom=[e for e in entries if e[2]['type']=='prometheus']
tempo=[e for e in entries if e[2].get('uid')=='tempo']
assert len(prom)==len(tempo)==1,'Expected one existing Prometheus datasource and the Tempo UID'
prom_uid=prom[0][2]['uid']
for path,document,ds in entries:
    changed=False
    if ds['type']=='prometheus':
        ds.setdefault('jsonData',{})['exemplarTraceIdDestinations']=[{'name':'traceID','datasourceUid':'tempo'}]
        changed=True
    if ds.get('uid')=='tempo':
        ds.setdefault('jsonData',{}).update({'serviceMap':{'datasourceUid':prom_uid},'nodeGraph':{'enabled':True}})
        changed=True
    if changed: path.write_text(yaml.safe_dump(document,sort_keys=False))
print('Tempo metrics -> Prometheus remote write; existing datasource UIDs preserved')
PYTHON
```

```bash
lab-notes/.tools/bin/python lab-notes/tracing/configure_span_metrics.py
dp config --quiet
dp up -d --no-deps --force-recreate prometheus
wait_prometheus
dp up -d --no-deps --force-recreate tempo grafana
wait_backend tempo:3200 /ready
wait_grafana
dp logs --since 2m --tail 100 tempo prometheus
```

Prometheus now accepts internal remote writes at `/api/v1/write`, and exemplar storage is enabled explicitly. This endpoint shares Prometheus's HTTP listener: retain the existing loopback-only host binding and do not expose it to untrusted clients.

Expect ready services without unknown-field or remote-write errors. Nine scrape jobs remain nine: Tempo-generated samples arrive by remote write, not by a new scrape job. Do not also configure a Collector span-metrics connector. `source="tempo"` distinguishes the generated samples; `service` is the generated metric label, whereas `service.name` is an OTel resource attribute.

## 4. Measure One Fully Retained Workload

Use a short, reversible tail-sampler bypass. The SDK still samples at 1.0. This establishes a denominator for interpreting the later biased view. The trap restores the complete saved Collector configuration on normal exit or interruption; it never deletes the logs pipeline or persistent queue settings.

```bash
(
  set -euo pipefail
  restore_tail() {
    cp "$LAB_DIR/collector.before.yml" lab-notes/tracing/collector.yml
    dp up -d --no-deps --force-recreate otel-collector
  }
  trap restore_tail EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  lab-notes/.tools/bin/python - <<'PYTHON'
from pathlib import Path
import yaml
p=Path('lab-notes/tracing/collector.yml'); c=yaml.safe_load(p.read_text())
a=c['service']['pipelines']['traces']['processors']
assert 'tail_sampling' in a
c['service']['pipelines']['traces']['processors']=[v for v in a if v!='tail_sampling']
p.write_text(yaml.safe_dump(c,sort_keys=False))
PYTHON
  dp run --rm -T --no-deps otel-collector validate --config=/etc/otelcol-contrib/config.yaml
  dp up -d --no-deps --force-recreate otel-collector
  wait_backend otel-collector:13133 /
  api -fsS "$APP_URL/metrics" > "$LAB_DIR/full.before.prom"
  python3 lab-notes/tracing/scenario_load.py "$APP_URL" "$LAB_DIR/full.jsonl" --count 60
  api -fsS "$APP_URL/metrics" > "$LAB_DIR/full.after.prom"
  LEDGER_JSON=$(cat "$LAB_DIR/full.jsonl")
  dp exec -T app python - "$LEDGER_JSON" --wait 35 --require-all \
    < lab-notes/tracing/retention_audit.py > "$LAB_DIR/full-audit.json"
  sleep 10
  pq 'traces_spanmetrics_calls_total{source="tempo"}' > "$LAB_DIR/full-span-counters.json"
  pq 'traces_service_graph_request_total{source="tempo"}' > "$LAB_DIR/full-edges.json"
)
wait_backend otel-collector:13133 /
jq '{requested,head_sampled,complete}' "$LAB_DIR/full-audit.json"
jq '.data.result[] | {metric,value}' "$LAB_DIR/full-span-counters.json"
jq '.data.result[] | {metric,value}' "$LAB_DIR/full-edges.json"
```

Expect sixty complete traces: twenty normal, twenty slow and twenty error scenarios. Each trace contains multiple spans, so summing **all** span-metric series does not equal sixty requests. Find the `SPAN_KIND_SERVER` series for your app and the `/api/v1/demo/downstream` operation, and compare that series population with the sixty-row ledger. Generated counters can contain earlier traffic; use before/after deltas or an isolated time interval when claiming exact counts.

The graph should contain the application → `lab-downstream` relationship. Inspect the actual `client` and `server` label values before applying filters. SQL and Redis instrumentation can produce additional inferred dependency nodes. Their presence does not mean those servers are exporting spans.

If the audit succeeds but generated series are initially empty, poll the same queries for up to sixty seconds. Trace availability, generator collection and remote-write visibility have separate delays. A HTTP 200 query with an empty result is not successful proof of metric delivery.

## 5. Query RED Metrics and Reveal Sampling Bias

```promql
sum by (service) (rate(traces_spanmetrics_calls_total{source="tempo",span_kind="SPAN_KIND_SERVER"}[5m]))
```

```promql
sum by (service) (rate(traces_spanmetrics_calls_total{source="tempo",span_kind="SPAN_KIND_SERVER",status_code="STATUS_CODE_ERROR"}[5m]))
/
sum by (service) (rate(traces_spanmetrics_calls_total{source="tempo",span_kind="SPAN_KIND_SERVER"}[5m]))
```

```promql
histogram_quantile(0.95,
  sum by (le,service) (rate(traces_spanmetrics_latency_bucket{source="tempo",span_kind="SPAN_KIND_SERVER"}[5m]))
)
```

The latency histogram is named `traces_spanmetrics_latency_bucket`, without an added `_seconds` suffix. The configured bucket boundaries are seconds. Restrict `span_name` as well when investigating one operation; discover its exact value in the saved result rather than guessing it from a raw URL. Five-minute rates blend traffic from both experiments, so use separate absolute time windows for a visual comparison.

```bash
api -fsS "$APP_URL/metrics" > "$LAB_DIR/tail.before.prom"
python3 lab-notes/tracing/scenario_load.py "$APP_URL" "$LAB_DIR/tail.jsonl" --count 60
api -fsS "$APP_URL/metrics" > "$LAB_DIR/tail.after.prom"
LEDGER_JSON=$(cat "$LAB_DIR/tail.jsonl")
dp exec -T app python - "$LEDGER_JSON" --wait 35 \
  < lab-notes/tracing/retention_audit.py > "$LAB_DIR/tail-audit.json"
sleep 10
pq 'traces_spanmetrics_calls_total{source="tempo"}' > "$LAB_DIR/tail-span-counters.json"
jq '{requested,head_sampled,complete}' "$LAB_DIR/tail-audit.json"
```

Expect all error and slow traces plus a probabilistic subset of normal traces, subject to delivery success. Do not demand an exact 10% count from twenty normal requests. Compare counter deltas between the two saved generator snapshots after allowing both runs to settle. The retained population overrepresents errors and slower requests: its latency distribution and error proportion are diagnostic, not unbiased service measurements.

For a future SLI, retain the application metrics and their complete request denominator. Multiplying tail-sampled counts by ten cannot repair outcome-dependent selection. Moving generation before sampling is an alternative architecture with different costs; it is not implemented in this lab.

## 6. Use a Service Graph and an Exemplar

In Grafana Explore, select **Tempo** and open its service graph view. The configured `serviceMap.datasourceUid` points to the existing Prometheus datasource. Inspect the app/downstream edge, then inspect a trace from the known error ledger. The graph displays generated relationships; it does not replace the waterfall's causal ordering.

Switch Explore to **Prometheus**. Query the latency buckets for the discovered service/operation, use an absolute interval covering the experiment, enable exemplars in the query options and click an exemplar marker. The derived destination uses the case-sensitive exemplar label `traceID` and the existing `tempo` UID.

Verify the same observation through Prometheus's API:

```bash
EXEMPLAR_START=$(python3 - "$LAB_DIR/full.jsonl" <<'PYTHON'
import json,sys
with open(sys.argv[1]) as f: print(int(json.loads(next(f))['start_ns']/1e9)-5)
PYTHON
)
EXEMPLAR_END=$(date +%s)
curl -fsS --connect-timeout 2 --max-time 15 -G \
  "$PROM_URL/api/v1/query_exemplars" \
  --data-urlencode 'query=traces_spanmetrics_latency_bucket{source="tempo"}' \
  --data-urlencode "start=$EXEMPLAR_START" --data-urlencode "end=$EXEMPLAR_END" \
  > "$LAB_DIR/exemplars.json"
jq -e '.status=="success" and (.data|length)>0' "$LAB_DIR/exemplars.json"
EXEMPLAR_TRACE=$(jq -r '[.data[].exemplars[].labels.traceID // empty][0] // empty' "$LAB_DIR/exemplars.json")
test -n "$EXEMPLAR_TRACE"
fetch_trace "$EXEMPLAR_TRACE" "$LAB_DIR/exemplar-trace.json"
```

The pass condition is a retrievable trace, not merely a clickable marker. Exemplars are sparse representatives; they do not promise one link per request or that the selected trace is exactly the calculated p95 request. A quantile is a histogram estimate over a population.

## 7. Troubleshooting and Safe Recovery

| Observation | Check and correction |
|---|---|
| Remote write returns 404 | Recreate Prometheus with the modified Compose command; a config reload alone cannot change startup flags. |
| No generated metrics | Check enabled processors in `overrides.defaults.metrics_generator`, Tempo logs, WAL permissions and receiver URL. |
| Metrics exist, graph absent | Check Tempo datasource UID mapping, edge metric labels and client/server span pairing. |
| Exemplar query empty | Check feature flag, `send_exemplars`, selected interval and actual generated histogram samples. |
| Exemplar exists but trace absent | Separate trace retention/ingestion loss from exemplar retention; query the exact ID and inspect Collector/Tempo logs. |
| Error ratio disagrees with native metrics | First test sampling, span-kind/operation filters, and interval overlap. Do not assume FastAPI counters are wrong. |
| Tempo fails after editing | Restore the saved Tempo file and recreate only Tempo; do not remove its volume. |

To completely undo this lab's infrastructure changes, restore the three saved configuration files, replace the datasource directory with its saved copy, and recreate Prometheus, Tempo, Collector and Grafana using `dp`. Normal completion keeps the generator and datasource links enabled; only the temporary tail bypass is removed.

```bash
lab-notes/.tools/bin/python - <<'CHECK'
import yaml
c=yaml.safe_load(open('lab-notes/tracing/collector.yml'))
assert 'tail_sampling' in c['service']['pipelines']['traces']['processors']
CHECK
wait_ready
wait_backend tempo:3200 /ready
wait_backend otel-collector:13133 /
dp ps -a
```

Technical references: [Tempo metrics generator](https://grafana.com/docs/tempo/latest/metrics-from-traces/metrics-generator/), [span metrics](https://grafana.com/docs/tempo/latest/metrics-from-traces/span-metrics/), [Tempo configuration](https://grafana.com/docs/tempo/latest/configuration/), and [Prometheus feature flags](https://prometheus.io/docs/prometheus/latest/feature_flags/). Check the repository's pinned-version behavior when comparing newer documentation.

## 8. Knowledge Check

1. Why are native request metrics still needed?
2. Why is summing all span calls an invalid request count?
3. Can an exemplar be treated as the p95 request?
4. Does a missing service-graph node prove there is no dependency?

### Answer Guide

1. They retain a request denominator independent of trace sampling and transport loss.
2. A request creates server, client and internal spans; select the relevant service, operation and span kind.
3. No. It is a representative observation, while p95 is estimated from a distribution.
4. No. Instrumentation, sampling, matching windows and telemetry loss determine graph visibility.

## 9. Professional Scenario Exercise

A teammate proposes paging on a 45% error ratio from Tempo while the native route metric reports 33%. Use the ledgers, retained trace classes, counter deltas and matching intervals to decide whether the service changed or the observation population changed. State which signal should own the alert.

## 10. Lab Notebook Template

Create a notebook in the evidence directory for this run:

```bash
test -e "$LAB_DIR/notebook.md" || printf '# Lab 41 Evidence\n' > "$LAB_DIR/notebook.md"
```

Use these headings and fill them with your predictions, commands, observed results and conclusions:

```markdown
# Lab 41 Evidence

## Prediction and inherited sampling
## Generator and remote-write configuration
## Full versus selected workload
## Service graph and exemplar trace ID
## Recovery and remaining limits
```

## 11. Observable Completion Criteria

- [ ] The generator writes real span/edge metrics to Prometheus without duplicating FastAPI metrics.
- [ ] Full and sampled request ledgers, retention audits and counter observations are saved.
- [ ] The service graph contains a verified app/downstream edge.
- [ ] An actual exemplar ID resolves to a Tempo trace.
- [ ] Tail sampling is restored and all required services are ready.

## 12. Production Implications

Generated metrics add series, WAL writes and remote-write load. Bound dimensions and capacity, monitor delivery, and protect the receiver. Sampling policy becomes part of the metric definition. Service graphs and exemplars accelerate investigation; they do not establish topology completeness or HA. Production decisions require measured volume, retention, authentication and failure budgets.

## 13. End State and Transition

Tempo generation, service graph configuration and exemplar navigation remain enabled. The original tail policies are active, native metrics remain authoritative, and profiling is still off. Continue to [Lab 42](Lab-42.md) to enable the existing Python profiler and inspect real sampled stacks.
