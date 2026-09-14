# Lab 11: PromQL Selectors, Matchers, and Aggregation

## Purpose and Scope

> **Primary Objective:** Query the collected series with deliberate label scope, preserve useful dimensions during aggregation, and distinguish filtered results, numeric zero, missing data and query errors.

Prometheus can return a syntactically valid number that answers the wrong question. This lab focuses on selecting the intended workload and retaining the dimensions needed to interpret it.

You will use the Prometheus query API and expression browser. Grafana is still stopped. Counter-rate functions are intentionally deferred to Lab 12 so that label and result-type mistakes are visible before more math is added.

## 1. Inherited State and Starting Checks

```bash
source lab-notes/session.sh
source lab-notes/evidence.sh
source lab-notes/raw-metrics.sh
source lab-notes/metrics-session.sh
metrics_check
start_lab 11
api -fsS "$PROM_URL/api/v1/targets?state=active" > "$LAB_DIR/starting-targets.json"
pq 'up{job=~"fastapi|prometheus"}' | jq .
```

Expected: the four-service stage from Lab 10, with healthy FastAPI and Prometheus targets. Keep the target file restored to `app:8000`. An unexplained missing target is a prerequisite failure, not a PromQL exercise.

## 2. Scope and Learning Objectives

You will select by metric name and labels, use exact/negative/regex matchers, inspect instant and range results, group and aggregate deliberately, and demonstrate how label mismatch can remove a binary-operation result.

By completion, explain every returned label set and distinguish no data from zero and a failed query. You will not add recording rules, dashboards, alerts, exporters or new business endpoints.

## 3. Query Evaluation Is Another Observation Boundary

Relevant events are still application requests, dependency changes and Prometheus scrapes. A query evaluates stored samples; it does not send a new business request or force a new scrape.

| Object | Meaning |
|---|---|
| Selector | Chooses stored series by name and label matchers |
| Instant vector | At most one selected value per series at an evaluation time |
| Range vector | A sequence of stored samples per selected series over a lookback range |
| Instant query API | Evaluates one expression at one time |
| Range query API | Repeats an expression at evenly spaced evaluation steps |
| Aggregation | Combines selected series while retaining/removing chosen labels |

Classic histogram bucket/count/sum series are ordinary numeric series to PromQL. Native histogram samples are a separate feature; this application does not expose them. See the [PromQL basics reference](https://prometheus.io/docs/prometheus/latest/querying/basics/).

## 4. Create a Small, Explainable Set of Request Series

```bash
api -fsS -H 'Content-Type: application/json' \
  -d '{"name":"Lab 11 selector subject","price":"11.00"}' \
  "$APP_URL/api/v1/items" -o "$LAB_DIR/item.json"
ITEM_ID=$(jq -er '.id' "$LAB_DIR/item.json")
for n in 1 2 3; do
  api -fsS "$APP_URL/api/v1/items?limit=1" >/dev/null
  api -sS -o /dev/null "$APP_URL/api/v1/items/$(new_uuid)"
done
api -sS -o /dev/null "$APP_URL/lab11-unmatched"
api -sS -H 'Content-Type: application/json' -d '{"name":"","price":"-1"}' \
  "$APP_URL/api/v1/items" -o "$LAB_DIR/validation.json"
snapshot "$LAB_DIR/raw-series.json"
```

Predict which route/method/status combinations now exist. Allow a scrape before expecting new children in Prometheus; target health alone does not guarantee that your most recent event has already been collected.

The invalid and missing-item requests are intentional bounded observations. Their IDs must not become label values.

## 5. Begin With Exact Selectors

```bash
pq 'application_http_requests_total{job="fastapi"}' \
  | tee "$LAB_DIR/all-request-series.json" | jq '.data.result[] | {metric,value}'
pq 'application_http_requests_total{job="fastapi",method="GET",route="/api/v1/items",status_code="200"}' \
  | jq .
pq 'application_items_mutations_total{job="fastapi",operation="create"}' | jq .
```

List the metric's own labels separately from `job`, `instance`, `environment` and `service` added by scraping. Do not assume an internal discovery label such as `__address__` is available on every stored sample.

These values are cumulative counts since the relevant process/child began. They are not requests per second or counts limited to the last five minutes.

## 6. Use Matchers With Explicit Intent

```bash
pq 'application_http_requests_total{job="fastapi",status_code=~"4.."}' | jq .
pq 'application_http_requests_total{job="fastapi",method=~"GET|POST"}' | jq .
pq 'application_http_requests_total{job="fastapi",route!="__unmatched__"}' | jq .
pq 'application_http_requests_total{job="fastapi",status_code!~"2.."}' | jq .
```

| Matcher | Effect in these queries |
|---|---|
| `=` | Exact string match |
| `!=` | Excludes an exact value; also consider labels that are absent |
| `=~` | Regex match over the complete label value |
| `!~` | Rejects a regex match |

PromQL regex matchers are fully anchored. `status_code=~"4"` does not mean “contains 4” or “all 4xx”; use the intended full pattern. Multiple matchers in one selector are combined as conditions on each series.

Avoid a broad regex when an exact known value expresses the question. More complex syntax is not automatically better query scope.

## 7. Test Missing-Label Semantics

```bash
pq 'application_dependency_up{job="fastapi",label_not_defined_here=""}' | jq .
pq 'application_dependency_up{job="fastapi",label_not_defined_here!="present"}' | jq .
```

Both can match series where that label is absent. This is why a negative matcher is not always a safe way to restrict a population to objects that actually carry a label.

Write a positive scope first, such as the expected job and service/environment, then add exclusions. A query that silently includes missing-label populations can look correct when only one target exists and become misleading after more exporters are introduced.

## 8. Inspect Metric Names Without Inventing Them

```bash
pq 'count by (__name__) ({job="fastapi",__name__=~"application_.*"})' \
  | tee "$LAB_DIR/family-series-counts.json" | jq '.data.result'
```

This counts selected **series**, not events. Histogram bucket and creation-time names are separate entries. Compare with the raw-snapshot inventory from Lab 7.

`__name__` is the metric-name label available to selectors. Other double-underscore names commonly belong to discovery/relabeling internals and have different lifecycles. Do not add imagined labels to make an expression return data.

## 9. Preserve the Dimensions Needed for the Question

```bash
pq 'sum by (route, method, status_code) (application_http_requests_total{job="fastapi"})' | jq .
pq 'sum by (route, method) (application_http_requests_total{job="fastapi"})' | jq .
pq 'sum by (status_code) (application_http_requests_total{job="fastapi"})' | jq .
pq 'sum(application_http_requests_total{job="fastapi"})' | jq .
```

Predict how many result groups should remain after each aggregation. The final sum discards route and status detail, so it cannot identify which route returned 404 even when the total is numerically correct.

Grouping is a semantic decision. In a future multi-service or multi-environment query, retain service/environment unless your question intentionally combines them. This single-target exercise does not justify dropping those dimensions from every production dashboard.

## 10. Compare by and without

```bash
pq 'sum by (route, method) (application_http_requests_total{job="fastapi"})' | jq '.data.result[].metric'
pq 'sum without (status_code, instance) (application_http_requests_total{job="fastapi"})' | jq '.data.result[].metric'
```

`by` keeps only the specified grouping labels; `without` removes the listed labels and preserves the other grouping dimensions. The second expression retains job/service/environment, so its result label sets need not match the first expression even when some numeric totals are equal.

When you later divide vectors, their labels determine which series can match. Inspect result labels before assuming missing output is a missing scrape.

## 11. Observe a Label-Matching Mistake Safely

```bash
pq 'application_http_server_errors_total{job="fastapi"} / application_http_requests_total{job="fastapi"}' \
  | tee "$LAB_DIR/mismatched-division.json" | jq .
pq 'sum by (method,route) (application_http_server_errors_total{job="fastapi"}) / sum by (method,route) (application_http_requests_total{job="fastapi"})' \
  | tee "$LAB_DIR/aligned-division.json" | jq .
```

The first operands have different label sets because the request counter includes `status_code`. Default vector matching can therefore produce no matching output. The second expression intentionally aligns labels first.

The second result is a **process-lifetime cumulative fraction**, not a recent incident error rate. It also needs meaningful nonzero denominators. Lab 12 replaces cumulative values with reset-aware rates over the same time range before calculating an operational error ratio.

Do not add `group_left` or `group_right` merely to silence a matching problem. First state the intended relationship and aggregation scope.

## 12. Prove Why Job Scope Matters

```bash
pq 'process_resident_memory_bytes{job=~"fastapi|prometheus"}' | jq .
pq 'sum(process_resident_memory_bytes{job=~"fastapi|prometheus"})' | jq .
pq 'process_resident_memory_bytes{job="fastapi"}' | jq .
```

The first query observes two different processes. The sum is combined resident process memory, not FastAPI memory and not total host RAM. Shared pages and process accounting also make summing RSS different from a complete physical-memory model.

An unscoped familiar metric name can include more populations as exporters are added. Record your intended job/service/time scope alongside every saved query.

## 13. Separate Instant Results From Range Evaluation

```bash
pq 'application_http_requests_total{job="fastapi",method="GET",route="/api/v1/items",status_code="200"}[2m]' \
  | tee "$LAB_DIR/raw-range-vector.json" | jq '.data.resultType,.data.result'
END=$(date +%s)
START=$((END-120))
api -fsS --get \
  --data-urlencode 'query=application_http_requests_total{job="fastapi",method="GET",route="/api/v1/items",status_code="200"}' \
  --data-urlencode "start=$START" --data-urlencode "end=$END" \
  --data-urlencode 'step=15' "$PROM_URL/api/v1/query_range" \
  | tee "$LAB_DIR/repeated-evaluation.json" | jq '.data.resultType,.data.result'
```

The instant API can return a range-vector expression. The range API instead repeats an instant-vector/scalar expression at evaluation steps. Its matrix-shaped response does not mean you supplied a raw range-vector expression.

A finer query step can repeat the latest eligible sample; it does not increase scrape frequency or reconstruct request events between scrapes. Missing initial points can simply mean the series did not exist during that part of the window.

## 14. Predict Boolean Filtering During Cache Degradation

You will stop only Redis, then compare the two real dependency gauges. Predict the output labels and numeric values of these expressions:

```promql
application_dependency_up{job="fastapi"} == 0
```

```promql
application_dependency_up{job="fastapi"} == bool 0
```

The first filters samples by a condition while retaining matching sample values. The second returns a numeric truth value for each matched input series. Neither invents a sample for missing telemetry.

## 15. Run the Bounded Gauge Experiment

```bash
(
  set -euo pipefail
  trap 'dc start redis >/dev/null' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  record_change "stop_redis_for_selector_experiment" planned
  dc stop redis
  api -fsS "$APP_URL/health/ready" | tee "$LAB_DIR/degraded-ready.json" | jq .
  wait_metric 'application_dependency_up{job="fastapi",dependency="redis"}' 0
  pq 'application_dependency_up{job="fastapi"} == 0' > "$LAB_DIR/filter-result.json"
  pq 'application_dependency_up{job="fastapi"} == bool 0' > "$LAB_DIR/bool-result.json"
  jq '.data.result' "$LAB_DIR/filter-result.json" "$LAB_DIR/bool-result.json"
  wait_target fastapi up
)
wait_ready
wait_metric 'application_dependency_up{job="fastapi",dependency="redis"}' 1
record_change "restore_redis_after_selector_experiment" completed
```

Expected: the filtered expression returns only Redis with value zero. The bool expression returns Redis with value one and PostgreSQL with value zero. FastAPI remains scrapeable and readiness is degraded, not unavailable.

The extra wait matters: the direct readiness response updates application-observed state, but Prometheus still needs a scrape before a query sees it.

## 16. Distinguish No Data, Zero and Query Failure

```bash
pq 'application_dependency_up{job="fastapi",dependency="not-configured"}' | jq .
pq 'application_dependency_up{job="fastapi",dependency="redis"} == bool 0' | jq .
api -sS --get --data-urlencode 'query=sum(' \
  -o "$LAB_DIR/syntax-error.json" -w 'HTTP %{http_code}\n' "$PROM_URL/api/v1/query"
jq . "$LAB_DIR/syntax-error.json"
```

The first is a successful query with an empty vector. The second, after recovery, is a present series with numeric zero. The third is a failed query with an error response and non-success HTTP status.

Do not use unconditional `or vector(0)` to turn every missing population into healthy-looking data. Missing-series handling requires an explicit availability and alerting policy.

## 17. Recover, Clean Up and Preserve Useful Queries

```bash
api -fsS -X DELETE "$APP_URL/api/v1/items/$ITEM_ID" -o /dev/null
metrics_check
pq 'up{job=~"fastapi|prometheus"}' > "$LAB_DIR/final-up.json"
pq 'application_dependency_up{job="fastapi"}' > "$LAB_DIR/final-dependencies.json"
capture_app_logs
```

Keep the query expressions in your notebook with their intended scope, result type and units. A screenshot without the expression/time window is incomplete investigation evidence. No permanent configuration change was needed in this lab.

## 18. Troubleshooting Runbook

| Symptom | Check |
|---|---|
| Empty result for a real metric | Confirm job, exact label names/values, time, staleness and whether the child exists |
| Regex does not match expected status | Remember full-value matching; use `4..`, not `4` |
| Negative matcher returns more series than expected | Check absent-label matching and add explicit positive scope |
| Sum hides the offending route | Retain route/status before aggregating to a service total |
| Division unexpectedly returns no data | Inspect operand label sets and intended matching before adding group modifiers |
| Value looks like a huge request rate | You queried a cumulative counter; rate functions come next |
| Memory query grows after adding a target | Scope the intended job instead of silently summing different processes |
| Range API rejects a raw range vector | Use an instant-vector/scalar expression for repeated range evaluation |
| Redis recovery is not immediately visible | Compare direct readiness with scrape time and wait for fresh collection |
| Query error displayed as zero | Inspect API status/error fields; syntax failure is not a measured value |

Use the smallest selector that establishes data presence, then add one matcher or aggregation at a time.

## 19. Knowledge Check

1. Does an instant query force a scrape?
2. Why does status_code=~"4" not select 404?
3. Can a matcher for an empty label value match an absent label?
4. What does count by (__name__) count here?
5. What difference matters between by and without?
6. Why can two populated vectors divide to an empty result?
7. Is a cumulative error fraction a recent error rate?
8. Does a query step of one second imply one-second collection?
9. What does ==0 return for a matching zero gauge?
10. Can ==bool0 establish anything about an absent input series?

### Answer Guide

1. No; it evaluates stored samples.
2. Regex matchers match the complete value.
3. Yes.
4. Series grouped by sample metric name, not business events.
5. by keeps specified labels; without removes specified labels while preserving others.
6. Their label sets may not match under default vector matching.
7. No; it mixes process-lifetime history and resets.
8. No; evaluation can reuse the latest eligible scraped value.
9. The matching series with its original zero value.
10. No; bool transforms present inputs, not missing evidence.

## 20. Professional Scenario Exercise

A chart labelled “FastAPI memory” increases when Prometheus restarts, and a 5xx fraction panel is empty despite populated counters. Diagnose both using metric scope and label matching. Show the intermediate selectors you would inspect before editing dashboard units or filling missing values with zero.

## 21. Lab Notebook Template

Create a notebook in the evidence directory for this run:

```bash
test -e "$LAB_DIR/notebook.md" || printf '# Lab 11 Evidence\n' > "$LAB_DIR/notebook.md"
```

Use these headings and fill them with your predictions, commands, observed results and conclusions:

```markdown
# Lab 11 Evidence

## Current target and scrape state
## Small workload and expected label sets
## Exact selectors
## Regex and missing-label observations
## Aggregation outputs and retained dimensions
## Vector-matching experiment
## Job scope and memory interpretation
## Instant versus range API evidence
## Redis gauge prediction and result
## Zero/absence/error distinction
## Recovery and saved query intent
```

## 22. Observable Completion Criteria

- [ ] Exact route/method/status selections return explained series.
- [ ] Regex and missing-label semantics are demonstrated.
- [ ] Series count is distinguished from event count.
- [ ] Aggregation retains or discards labels deliberately.
- [ ] A vector-matching failure is explained and corrected by aligned scope.
- [ ] Process memory is scoped to the intended job.
- [ ] Instant/range query behavior is compared with captured API output.
- [ ] The Redis experiment distinguishes filtering from bool truth values.
- [ ] No data, zero and syntax failure remain distinct.
- [ ] Redis, app targets and checkpoint state are healthy after cleanup.

## 23. Production Implications

PromQL correctness includes population, dimensions, units and time semantics, not only parser acceptance. A syntactically valid sum can combine unrelated processes, and a missing result can come from label matching rather than missing telemetry. Preserve query intent as operational documentation before using expressions in dashboards or alerts.

## 24. End State and Transition

Leave the four-service metrics stage healthy and retain the same discovery configuration.

Next: [Lab 12 — Counter Math: rate, irate, and increase](Lab-12.md). You will turn correctly scoped cumulative state into meaningful throughput and interval estimates, including across an application restart.
