# Lab 37: Tempo Search and TraceQL

## Purpose and Scope

> **Primary Objective:** Find known slow and failed requests using TraceQL, then verify the dependency path in the actual trace.

A trace ID lookup answers “show this trace.” Search answers “which stored traces match these properties?” This lab uses the span schema implemented in Lab 36 to build queries whose expected results are known in advance.

    You will search by resource, operation, scenario, duration, status, event and ancestry. You will distinguish span-local predicates from relationships across spans and use a waterfall to reason about the critical path. Trace-derived metrics, service graphs and exemplars belong to Lab 41 and are excluded here.

## 1. Inherited State and Starting Checks

Complete [Lab 36](Lab-36.md) first. Run from the repository root in one Bash session; keep the existing credentials, named volumes, checkpoint item, dashboards and earlier evidence.

```bash
source lab-notes/session.sh
source lab-notes/evidence.sh
source lab-notes/metrics-session.sh
source lab-notes/platform-session.sh
source lab-notes/traces-session.sh
load_app_settings
start_lab 37
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
dp exec -T app python - <<'CHECK'
from app.config import Settings
assert Settings().trace_sample_ratio == 1.0
CHECK
lab-notes/.tools/bin/python - <<'CHECK'
import yaml
c=yaml.safe_load(open('lab-notes/tracing/collector.yml'))
assert not any(p.startswith('tail_sampling') for p in c['service']['pipelines']['traces']['processors'])
print('Search baseline: full head sampling, no tail policy')
CHECK
```

Search completeness cannot be judged without the sampling state. Record those checks alongside your query window; a later lab intentionally changes what Tempo retains.

## 2. Learning Objectives and Search Vocabulary

| Expression | Meaning in this repository |
|---|---|
| `resource.service.name` | Which process produced the span |
| `resource.deployment.environment.name` | Environment identity, not a span attribute |
| `span:name` | Stable operation name, such as `demo.evaluate` |
| `span.lab.scenario` | Custom span attribute set in Lab 36 |
| `span:duration` | Duration of that matching span |
| `span:status` | Span status enum: `error`, `ok` or `unset` |
| `event:name` | Name of an event attached to a matching span |
| `trace:duration` | End-to-end extent of the stored trace |

TraceQL uses a colon for intrinsics and a dot for custom scoped attributes. This is not PromQL or LogQL. A condition inside one pair of braces must match the same span; separate spansets can express relationships across a trace.

**Prediction checkpoint:** `demo.evaluate` and `demo.compute` cannot both be the name of one span. Error requests should match `span:status=error`; a normal success may be `unset`, so `status=ok` is not a reliable definition of success here.

## 3. Create a Known Search Population

```bash
SEARCH_START=$(date +%s)
python3 lab-notes/tracing/scenario_load.py "$APP_URL" "$LAB_DIR/search-ledger.jsonl" --count 12
SEARCH_END=$(( $(date +%s) + 1 ))
printf '%s %s\n' "$SEARCH_START" "$SEARCH_END" > "$LAB_DIR/search-window.txt"
jq -rs '[.[] | {scenario,request_id,trace_id:.response.upstream_trace_id}]' "$LAB_DIR/search-ledger.jsonl" > "$LAB_DIR/known-traces.json"
TRACE_ID=$(jq -r '.[0].trace_id' "$LAB_DIR/known-traces.json")
fetch_trace "$TRACE_ID" "$LAB_DIR/known-trace.json"
```

There are four calls per scenario. The saved timestamps are Unix seconds for the search API; they are not trace nanoseconds. A one-second upper margin includes the final request. Trace arrival and searchable visibility are asynchronous, so a successful known-ID lookup is a useful starting check rather than proof that every query has immediately indexed all results.

Keep the time window narrow and preserve the ledger. Background dependency checks and another learner's traffic can coexist in Tempo; comparing known IDs is more reliable than expecting a global result count of exactly twelve.

## 4. Search the Operation Through Tempo’s Real API

```bash
Q_ALL="{ resource.service.name = \"$LAB_SERVICE\" && resource.deployment.environment.name = \"$LAB_ENVIRONMENT\" && span:name = \"demo.evaluate\" }"
backend tempo:3200 /api/search q "$Q_ALL" start "$SEARCH_START" end "$SEARCH_END" limit 100 > "$LAB_DIR/search-all.json"
jq '{returned:(.traces // [] | length),metrics,traces}' "$LAB_DIR/search-all.json"
```

The existing `backend` helper runs an HTTP GET from the app container and URL-encodes each key/value pair. Do not concatenate an unescaped TraceQL expression into a URL. Tempo remains internal at `tempo:3200`; no new host port is needed.

In Grafana Explore, select the provisioned Tempo datasource, switch to the TraceQL/code query mode, set a recent time range covering the workload and paste the resulting query. If your service is `fastapi-items` in `local`, the query is:

```traceql
{ resource.service.name = "fastapi-items" && resource.deployment.environment.name = "local" && span:name = "demo.evaluate" }
```

`limit=100` is a result cap, not a count of all matching requests. Search ordering and backend work limits also matter. Record response metrics and any error rather than treating a truncated/failed response as complete evidence. Repeat within a bounded minute if the first search is empty while the known trace is retrievable.

## 5. Add Duration, Outcome, Status and Event Predicates

```bash
Q_SLOW="{ resource.service.name = \"$LAB_SERVICE\" && span:name = \"demo.evaluate\" && span.lab.scenario = \"slow\" && span:duration > 200ms }"
Q_ERROR="{ resource.service.name = \"$LAB_SERVICE\" && span:name = \"demo.evaluate\" && span:status = error }"
Q_OUTCOME="{ resource.service.name = \"$LAB_SERVICE\" && span:name = \"demo.evaluate\" && span.app.outcome = \"failed\" }"
Q_EVENT='{ resource.service.name = "lab-downstream" && span:name = "demo.compute" && event:name = "work.rejected" }'
backend tempo:3200 /api/search q "$Q_SLOW" start "$SEARCH_START" end "$SEARCH_END" limit 100 > "$LAB_DIR/search-slow.json"
backend tempo:3200 /api/search q "$Q_ERROR" start "$SEARCH_START" end "$SEARCH_END" limit 100 > "$LAB_DIR/search-error.json"
backend tempo:3200 /api/search q "$Q_OUTCOME" start "$SEARCH_START" end "$SEARCH_END" limit 100 > "$LAB_DIR/search-outcome.json"
backend tempo:3200 /api/search q "$Q_EVENT" start "$SEARCH_START" end "$SEARCH_END" limit 100 > "$LAB_DIR/search-event.json"
jq -r '.traces[]?.traceID' "$LAB_DIR/search-error.json"
```

These are explicit filters on fields the code produces. `span.app.outcome` describes the operation contract; `span:status` is the general trace status used by later tail policies. They should agree for these controlled failures, but arbitrary real applications can have different error conventions.

The event query selects spans with a `work.rejected` event and returns their matching traces. It does not query Loki. The slow query asks for an upstream operation with both the known scenario and sufficient elapsed time; it does not conclude that CPU execution consumed that time.

Verify the known error IDs are included:

```bash
python3 - "$LAB_DIR" <<'CHECK'
import json,sys
from pathlib import Path
root=Path(sys.argv[1]); known=json.loads((root/'known-traces.json').read_text())
for name,scenario in [('slow','slow'),('error','error'),('outcome','error'),('event','error')]:
    expected={r['trace_id'] for r in known if r['scenario']==scenario}
    result=json.loads((root/f'search-{name}.json').read_text())
    found={r['traceID'].lower() for r in result.get('traces',[])}
    print(name, 'expected', len(expected), 'matched', len(expected & found))
    assert expected <= found, 'Retry the search window; inspect query errors/limits and trace arrival'
CHECK
```

An assertion failure is an investigation trigger, not permission to increase the time window indefinitely. First open one missing known ID, inspect its stored fields, then check time units, matching scope, API errors and result limits.

## 6. Prove Relationships, Not Just Co-occurrence

```bash
Q_IMPOSSIBLE='{ span:name = "demo.evaluate" && span:name = "demo.compute" }'
Q_BOTH='{ span:name = "demo.evaluate" } && { span:name = "demo.compute" }'
Q_DESCENDANT='{ span:name = "demo.evaluate" } >> { resource.service.name = "lab-downstream" && span:name = "demo.compute" }'
backend tempo:3200 /api/search q "$Q_IMPOSSIBLE" start "$SEARCH_START" end "$SEARCH_END" limit 100 > "$LAB_DIR/search-impossible.json"
backend tempo:3200 /api/search q "$Q_BOTH" start "$SEARCH_START" end "$SEARCH_END" limit 100 > "$LAB_DIR/search-both.json"
backend tempo:3200 /api/search q "$Q_DESCENDANT" start "$SEARCH_START" end "$SEARCH_END" limit 100 > "$LAB_DIR/search-descendant.json"
jq -e '(.traces // [] | length)==0' "$LAB_DIR/search-impossible.json"
```

The first query should match nothing because a single name cannot equal two different strings. The second finds traces containing both kinds of span, without proving ancestry. The third requires `demo.compute` to be a descendant of `demo.evaluate`, crossing intermediate CLIENT and SERVER spans. Use `>>`, not direct-child `>`, for that chain.

Search narrows the investigation. Open a matching trace and run `verify_boundary.py` on its raw payload to prove the particular CLIENT-to-SERVER edge. Two spans sharing a trace, or sharing a request ID in logs, is weaker evidence than the actual parent relation.

## 7. Explain the Critical Path From a Known Slow Trace

```bash
SLOW_ID=$(jq -r '[.[] | select(.scenario=="slow")][0].trace_id' "$LAB_DIR/known-traces.json")
fetch_trace "$SLOW_ID" "$LAB_DIR/slow-trace.json"
python3 lab-notes/tracing/verify_boundary.py "$LAB_DIR/slow-trace.json"
python3 lab-notes/tracing/inspect_trace.py "$LAB_DIR/slow-trace.json" > "$LAB_DIR/slow-spans.json"
python3 - "$LAB_DIR/slow-spans.json" <<'CHECK'
import json,sys
spans=json.load(open(sys.argv[1])); origin=min(s['start_ns'] for s in spans)
for s in sorted(spans,key=lambda s:s['start_ns']):
    print(f"{(s['start_ns']-origin)/1e6:8.2f} ms start  {s['duration_ms']:8.2f} ms duration  {s['service']}  {s['name']}")
evaluate=next(s for s in spans if s['name']=='demo.evaluate')
compute=next(s for s in spans if s['name']=='demo.compute')
print('compute/evaluate duration ratio:', round(compute['duration_ms']/evaluate['duration_ms'],3))
CHECK
```

Open this trace in Grafana and expand the full waterfall. In this sequential, awaited call, the downstream compute interval blocks completion of the upstream operation. The injected `asyncio.sleep(0.25)` explains much of the path's elapsed time while consuming little CPU. The remainder can include scheduling, network and framework work.

Parent duration includes child duration. Adding upstream SERVER + INTERNAL + CLIENT + downstream SERVER + compute durations double-counts the same waiting time repeatedly. The ratio above is descriptive for this controlled nested call, not a general critical-path algorithm or CPU-utilization measurement.

With parallel children, the slowest dependency that blocks completion matters; total child durations can exceed root duration. Across different hosts, clock skew complicates timestamp ordering. Use parentage and application control flow alongside the waterfall instead of assuming every visual overlap proves causation.

## 8. Correlate Search Findings With Logs and Metrics

Select one matched error trace. In Loki, filter the established service/environment labels, parse JSON and filter its exact `trace_id`. Confirm the upstream completion status 502 and downstream 503. The derived trace link should return you to the same stored trace.

In Prometheus, inspect the application's existing route-template HTTP counter and duration histogram from the earlier metrics labs. Compare their time window with your client ledger. Those native measurements include all business requests; the query result list is a capped selection of stored traces and is not a request-rate or SLO denominator.

Write one evidence statement: “Four client-ledger errors in this run were found by outcome, status and event queries; their traces show the downstream rejection and their completion logs show the mapped HTTP statuses.” Add limits: other traffic may exist, the search is bounded, and tracing is presently fully sampled.

## 9. Recovery and Troubleshooting

This lab changes no application configuration or schema. Keep the evidence and verify normal readiness. Do not delete Tempo blocks to remove synthetic traces; the retention policy will age them out.

| Symptom | Check |
|---|---|
| Trace-ID lookup works, search is empty | Query window/units, search visibility delay, attribute scope and result limits. |
| Event query returns no errors | Inspect raw span events; confirm Lab 36 code is running and the failure scenario executed. |
| `status=ok` omits normal work | Successful custom spans intentionally remain UNSET; use `app.outcome="success"`. |
| Both spans exist but direct-child query fails | Intermediate HTTP CLIENT and downstream SERVER spans require descendant `>>`. |
| Query syntax error | Keep TraceQL distinct from PromQL/LogQL; use scoped intrinsics and quote string values. |
| Search count differs from request count | Sampling, limits, unrelated traffic, arrival delay or partial data; compare known IDs first. |
| Latency appears multiplied | Do not sum inclusive parent/child durations. |

## 10. Knowledge Check

1. How do resource attributes differ from span attributes?
2. Why is `{A} && {B}` weaker than `{A} >> {B}`?
3. Why can a trace search result count not establish a service error rate?
4. Does a 250 ms span prove 250 ms of CPU work?

### Answer Guide

1. Resource attributes identify the emitter; span attributes describe one operation.
2. The first establishes same-trace presence; the second establishes an ancestor/descendant relationship.
3. Sampling, bounded windows, result caps and partial arrival change which traces are returned.
4. No. Elapsed time can include waiting, network delay and scheduling; profiling addresses CPU work later.

## 11. Professional Scenario Exercise

An incident report says “the database is slow” because the root HTTP span is long. Use a known slow scenario to show a dependency wait on the critical path, then write what evidence would be needed before attributing a real incident to SQL. Include one useful TraceQL query, one misleading query and an explanation of each.

## 12. Lab Notebook Template

Create a notebook in the evidence directory for this run:

```bash
test -e "$LAB_DIR/notebook.md" || printf '# Lab 37 Evidence\n' > "$LAB_DIR/notebook.md"
```

Use these headings and fill them with your predictions, commands, observed results and conclusions:

```markdown
# Lab 37 Evidence

## Prediction and starting state
## Configuration and bounded workload
## Evidence with timestamps and identifiers
## Explanation and competing hypotheses
## Recovery proof
## Production decision and remaining uncertainty
```

## 13. Observable Completion Criteria

- [ ] A fixed workload ledger and bounded search window are saved.
- [ ] Known slow and failed IDs are located with fields actually produced in Lab 36.
- [ ] The same-span contradiction returns no matches; ancestry query finds the expected chain.
- [ ] A slow waterfall is explained without adding overlapping durations.
- [ ] Logs and native HTTP metrics are correlated without treating trace results as traffic totals.

## 14. Production Implications

Choose stable attribute conventions so searches survive releases. Limit expensive broad queries and protect trace access because metadata can still be sensitive. Correlate search evidence with unsampled native request metrics. See [TraceQL query construction](https://grafana.com/docs/tempo/latest/traceql/construct-traceql-queries/) and [Tempo HTTP API](https://grafana.com/docs/tempo/latest/api_docs/).

## 15. End State and Transition

The application and Collector remain in the Lab 36 configuration, fully head-sampled with no tail sampler. [Lab 38](Lab-38.md) changes that assumption and measures the resulting diagnostic loss.
