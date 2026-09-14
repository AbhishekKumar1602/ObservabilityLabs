# Lab 12: Counter Math: `rate`, `irate`, and `increase`

## Purpose and Scope

> **Primary Objective:** Calculate throughput and estimated event counts from scraped counters, account for process resets, and select windows that support the question being asked.

A counter records accumulated observations. Prometheus sees occasional samples of that accumulation, not a timestamped list of the requests that caused it. This distinction becomes important when a process restarts, a scrape is missed, or a query asks about a window whose boundaries fall between samples.

You will generate controlled traffic, restart only the application, and compare raw counter changes with reset-aware PromQL. A small deterministic fixture makes the reset calculation repeatable even when the timing of the live experiment varies. Recording rules, dashboards and alerts remain outside this lab.

## 1. Inherited State and Clean Starting Check

```bash
source lab-notes/session.sh
source lab-notes/evidence.sh
source lab-notes/raw-metrics.sh
source lab-notes/metrics-session.sh
metrics_check
start_lab 12
snapshot "$LAB_DIR/starting-metrics.json"
api -fsS "$APP_URL/health/ready" > "$LAB_DIR/starting-readiness.json"
pq 'up{job=~"fastapi|prometheus"}' | jq .
```

Complete [Lab 11](Lab-11.md) first. The running stage contains FastAPI, PostgreSQL, Redis and Prometheus. Keep the 15-second scrape interval from Lab 10. Retain the checkpoint item and all database volumes.

Use `metrics_check`, not the three-service `baseline_check`. This lab does not rebuild the application or change its instrumentation.

## 2. Learning Objectives and Current Signal Path

By the end, you should be able to:

- State the units returned by each counter function.
- Explain why two or more usable samples are needed.
- Distinguish an application restart from a database reset.
- Apply reset handling before aggregation across instances.
- Explain why an estimated increase need not equal an integer request count.
- Separate a client attempt count, application completion count and sampled estimate.

Requests change application counters; Prometheus scrapes those counters; a query calculates from stored samples. The application remains the owner of instrumentation. There is no OTel metric exporter in this path.

## 3. Relevant Events and the Evidence Plan

| Occurrence | Evidence to preserve |
|---|---|
| A client attempts a request | A row in the bounded traffic ledger |
| Application completes a request | `request_completed` JSON record and RED counter change |
| Prometheus scrapes the application | Target state and timestamped samples |
| Application process restarts | Change record, container state and process start gauge |
| A counter decreases | Evidence of a reset within that series, not negative traffic |

A counter reset does not identify its cause by itself. A restart, an implementation change or changed metric semantics may require different explanations. In this experiment, the explicit restart record provides the causal context.

## 4. Compare the Three Functions

| Function | Result unit | Samples used | Useful interpretation |
|---|---|---|---|
| `rate(counter[2m])` | Events/second | Samples across the range | Smoothed recent throughput with reset handling and boundary extrapolation |
| `irate(counter[2m])` | Events/second | Last two usable samples | Recent sampled change, often more responsive and noisy |
| `increase(counter[2m])` | Events | Samples across the range | Estimated increase over the requested window |

For the same counter and window, `increase` is the corresponding `rate` multiplied by the range duration in seconds. It can be fractional because Prometheus extrapolates to time boundaries. `irate` is not an exact event-by-event arrival rate and does not average all samples in the window.

Use counter functions on counters. Applying them to the in-progress gauge can misinterpret legitimate decreases as resets. See the [Prometheus function reference](https://prometheus.io/docs/prometheus/latest/querying/functions/).

## 5. Inspect the Actual Scrape Evidence

```bash
pq 'application_http_requests_total{job="fastapi",method="GET",route="/api/v1/items",status_code="200"}[2m]' \
  > "$LAB_DIR/starting-counter-samples.json"
jq '.data.result[] | {metric,values}' "$LAB_DIR/starting-counter-samples.json"
pq 'count_over_time(application_http_requests_total{job="fastapi",method="GET",route="/api/v1/items",status_code="200"}[2m])' | jq .
```

Read adjacent sample timestamps and values. `count_over_time` counts stored samples, not requests. A counter value repeated across eight scrapes means eight observations of the same accumulated state; it does not mean eight business events.

If the selected child does not exist yet, make one successful list request and wait for two scrapes before proceeding.

## 6. Make Predictions Before Generating Traffic

Write predictions for these five questions:

1. Will 90 successful client requests always make `increase(...[3m])` return exactly 90?
2. Will the raw counter after a restart be greater than its pre-restart value?
3. Must Prometheus observe `up=0` during a short restart?
4. Will restarting FastAPI remove committed PostgreSQL rows?
5. Will a rate immediately become zero when the traffic generator stops?

Do not change the scrape interval to force an answer. The point is to explain the observation boundaries, including what the experiment cannot guarantee.

## 7. Create a Bounded Traffic Ledger

```bash
traffic_segment() {
  local segment="$1" n rid status elapsed
  for n in $(seq 1 45); do
    rid="lab12-${segment}-${n}-$(date +%s)"
    status=$(api -sS -o /dev/null -w '%{http_code} %{time_total}' \
      -H "X-Request-ID: $rid" "$APP_URL/api/v1/items?limit=1") || return 1
    read -r status elapsed <<< "$status"
    printf '%s,%s,%s,%s,%s\n' "$(date -u +%FT%TZ)" "$segment" "$rid" "$status" "$elapsed" \
      >> "$LAB_DIR/client-requests.csv"
    [[ "$status" == 200 ]] || return 1
    sleep 1
  done
}
printf 'timestamp,segment,request_id,status,client_seconds\n' > "$LAB_DIR/client-requests.csv"
record_change "begin_45_requests_before_application_restart" planned
traffic_segment before
snapshot "$LAB_DIR/before-restart.json"
pq 'process_start_time_seconds{job="fastapi"}' > "$LAB_DIR/process-start-before.json"
```

Expected: 45 successful rows. This is a closed-loop workload: one request completes, then the client waits one second. Latency and shell overhead mean it is slightly slower than exactly one request per second. It is not a constant-arrival-rate benchmark.

The ledger contains synthetic request IDs for correlation. They remain evidence fields, never metric labels.

## 8. Restart Only the Application

```bash
record_change "restart_only_fastapi_process" planned
RESTART_AT=$(date -u +%FT%TZ)
printf '%s\n' "$RESTART_AT" > "$LAB_DIR/restart-at.txt"
dm restart app
wait_ready
wait_target fastapi up
record_change "restart_only_fastapi_process" completed
snapshot "$LAB_DIR/after-restart.json"
traffic_segment after
wait_target fastapi up
pq 'process_start_time_seconds{job="fastapi"}' > "$LAB_DIR/process-start-after.json"
capture_app_logs
```

Do not restart PostgreSQL, Redis or Prometheus. The application remains a single worker, so its in-memory counters restart with that process. Docker may keep the same container identity during `restart`; process identity and container identity are not interchangeable.

A restart can fall entirely between two scrapes. In that case, `up` may never record zero even though a request during the interruption could have failed. Conversely, a reset can be missed if a counter grows beyond its old value before the next successful scrape. This workload makes a decrease likely by accumulating traffic before the restart, but recorded samples are the evidence.

## 9. Verify the Ledger and the Recorded Request Events

```bash
python3 - "$LAB_DIR" <<'PYTHON'
import csv, json, sys
from pathlib import Path
root = Path(sys.argv[1])
rows = list(csv.DictReader((root / "client-requests.csv").open()))
assert len(rows) == 90 and all(row["status"] == "200" for row in rows)
ids = {row["request_id"] for row in rows}
records = [json.loads(line) for line in (root / "app.jsonl").read_text().splitlines()]
matched = [r for r in records if r.get("event_name") == "request_completed" and r.get("request_id") in ids]
print({"client_successes": len(rows), "matching_completion_records": len(matched),
       "unique_recorded_request_ids": len({r["request_id"] for r in matched})})
PYTHON
```

In this small local run, expect 90 matching completion records. A mismatch is a prompt to inspect log retention, collection time and status handling; it is not permission to adjust the count manually.

A local ledger can count the requests generated by this exercise exactly. A service-wide counter includes other eligible requests, and a sampled estimate also depends on its query window.

## 10. Compare Throughput, Recent Change and Estimated Count

```bash
pq 'rate(application_http_requests_total{job="fastapi",method="GET",route="/api/v1/items",status_code="200"}[3m])' \
  | tee "$LAB_DIR/rate.json" | jq .
pq 'irate(application_http_requests_total{job="fastapi",method="GET",route="/api/v1/items",status_code="200"}[3m])' \
  | tee "$LAB_DIR/irate.json" | jq .
pq 'increase(application_http_requests_total{job="fastapi",method="GET",route="/api/v1/items",status_code="200"}[3m])' \
  | tee "$LAB_DIR/increase.json" | jq .
pq 'resets(application_http_requests_total{job="fastapi",method="GET",route="/api/v1/items",status_code="200"}[5m])' \
  | tee "$LAB_DIR/resets.json" | jq .
```

Expect nonnegative throughput and an estimated increase that is not required to equal 90. Query calls evaluate at slightly different times; use a fixed API `time` parameter when you need mathematically aligned comparisons.

`resets` should identify a decrease if the scraper observed both sides of this restart. It can exceed one if earlier restarts fall inside the five-minute range. Inspect the underlying samples and the change timestamp before attributing every decrease to this one action.

## 11. Demonstrate Why Window Length Matters

```bash
pq 'rate(application_http_requests_total{job="fastapi",method="GET",route="/api/v1/items",status_code="200"}[5s])' | jq .
pq 'rate(application_http_requests_total{job="fastapi",method="GET",route="/api/v1/items",status_code="200"}[1m])' | jq .
pq 'rate(application_http_requests_total{job="fastapi",method="GET",route="/api/v1/items",status_code="200"}[5m])' | jq .
```

A five-second range with 15-second scrapes usually contains fewer than two usable samples, so a counter rate has no result. A one-minute range is a reasonable initial four-scrape window here, though missed scrapes can still reduce evidence. A five-minute range is smoother and responds more slowly.

Record the sample count alongside an unexpected rate. Do not replace every absent value with zero: no usable samples and observed inactivity describe different conditions.

## 12. Handle Resets Before Aggregation

```bash
pq 'sum by (route,method) (rate(application_http_requests_total{job="fastapi"}[2m]))' | jq .
pq 'sum by (route,method) (increase(application_http_requests_total{job="fastapi"}[2m]))' | jq .
```

The order is deliberate: calculate a reset-aware change for each original counter series, then aggregate. If one instance resets while another grows, combining the raw counters first can hide the reset or create a misleading decrease.

There is only one application target today. The query shape still preserves the correct behavior when the deployment later grows. Keep service/environment grouping too when a query intentionally selects more than this one scoped workload.

## 13. Build a Recent Error Fraction With Aligned Labels

```bash
pq 'sum by (route,method) (rate(application_http_server_errors_total{job="fastapi"}[2m])) / sum by (route,method) (rate(application_http_requests_total{job="fastapi"}[2m]))' | jq .
```

This fraction uses matching route/method groups and the same window. The numerator counts server-side HTTP outcomes from Lab 8, including handled 503 responses. It is not the exception counter and does not count 4xx as server errors.

A zero denominator gives an undefined fraction. During inactivity, show “no eligible traffic” or gate on positive request rate rather than inventing 100% availability. A route child with no recent pair of samples can also be absent. Defining an SLI and its eligibility rules belongs to the later reliability labs.

## 14. Run a Deterministic Reset Fixture

Create a synthetic test file in the already mounted, nonsecret Prometheus configuration directory. It is test input only: it does not add a scraped metric or a recording rule to the running server.

```bash
cat > lab-notes/prometheus/counter-test.yml <<'YAML'
evaluation_interval: 15s
tests:
  - interval: 15s
    input_series:
      - series: 'lab_counter_total{instance="one"}'
        values: '0 15 30 2 17'
    promql_expr_test:
      - expr: resets(lab_counter_total[1m])
        eval_time: 1m
        exp_samples:
          - labels: '{instance="one"}'
            value: 1
      - expr: irate(lab_counter_total[1m])
        eval_time: 1m
        exp_samples:
          - labels: '{instance="one"}'
            value: 1
      - expr: rate(lab_counter_total[1m]) >= bool 0
        eval_time: 1m
        exp_samples:
          - labels: '{instance="one"}'
            value: 1
      - expr: increase(lab_counter_total[1m]) > bool 17
        eval_time: 1m
        exp_samples:
          - labels: '{instance="one"}'
            value: 1
YAML
```

```bash
chmod 644 lab-notes/prometheus/counter-test.yml
dm run --rm -T --no-deps --entrypoint promtool prometheus \
  test rules /etc/prometheus/labs/counter-test.yml \
  | tee "$LAB_DIR/counter-fixture-result.txt"
```

Expected: the test suite reports `SUCCESS`. The samples rise, reset from 30 to 2, then rise from 2 to 17. The last two samples are 15 seconds apart, so their `irate` is one event/second. The fixture also verifies that reset-aware estimated increase can exceed the final raw value.

The lower boundary of a PromQL range is excluded. At an evaluation time of one minute, the exact sample at time zero is not inside `[1m]`. This is another reason to reason from the function's window semantics rather than subtracting the first line displayed in an arbitrary file.

## 15. Observe Decay After Traffic Stops

Leave the application idle for two minutes while inspecting the same two-minute rate every 15–30 seconds. Do not issue extra list requests during the observation. Health and `/metrics` requests are excluded from the application's HTTP counters.

An `irate` can reach zero once the last two samples are equal, while a longer-window `rate` still includes earlier growth. Eventually both should become zero for a continuously scraped, unchanged counter. This is expected smoothing, not evidence that Prometheus is manufacturing requests.

Capture a final query response and its evaluation timestamp in the notebook.

## 16. Recovery and Proof of Recovery

```bash
metrics_check
api -fsS "$APP_URL/api/v1/items/$(cat lab-notes/checkpoint-item-id.txt)" \
  > "$LAB_DIR/checkpoint-after-restart.json"
api -fsS "$APP_URL/health/ready" > "$LAB_DIR/recovered-readiness.json"
pq 'up{job="fastapi"}' > "$LAB_DIR/recovered-up.json"
record_change "counter_reset_experiment_recovery_verified" completed
```

The checkpoint row should still exist. This demonstrates that process-local telemetry state and durable business state have different lifecycles. Keep the four-service stage running; no application files or migrations were changed.

## 17. Troubleshooting

| Symptom | Inspect | Explanation or correction |
|---|---|---|
| A rate is absent | Raw range vector and sample count | Widen a too-short window; verify the child exists and target is scraped |
| Increase is fractional | Boundary timestamps and window | Extrapolation is expected; use event records for a particular event ledger |
| No reset is reported | Actual pre/post samples and process start gauge | A scrape may have missed the decrease; use the deterministic fixture to verify math |
| Rate stays nonzero after load | Window length | Earlier growth remains inside the range |
| Negative manual difference | Restart evidence | Raw subtraction does not handle a counter reset |
| Error fraction is NaN | Request-rate denominator | There may be no traffic; preserve that distinction |
| Checkpoint request fails | Readiness, database state and original ID | A process restart should not remove committed rows; investigate before proceeding |

A `rate` result is an estimate supported by samples. Document uncertainty when gaps or restarts prevent a precise claim.

## 18. Knowledge Check

1. What are the units of rate and increase?
2. Why can increase return 89.6 for integer events?
3. Why should rate precede aggregation?
4. Can up stay at one during an application restart?
5. Does count_over_time count requests?

### Answer Guide

1. Rate is events per second; increase is an estimated number of events over the window.
2. Scrapes do not necessarily align with window boundaries, and the function extrapolates.
3. Reset handling must see each original counter before other instances can hide its decrease.
4. Yes, if the interruption falls between successful scrapes.
5. No. For these float series it counts stored samples in the selected range.

## 19. Professional Scenario Exercise

An operator reports “we served only 17 requests after 90 test requests” because the current counter equals 17. Explain the restart boundary, identify the durable evidence, and provide a reset-aware query. State what remains uncertain rather than promising an exact total from sampled telemetry.

## 20. Lab Notebook Template

Create a notebook in the evidence directory for this run:

```bash
test -e "$LAB_DIR/notebook.md" || printf '# Lab 12 Evidence\n' > "$LAB_DIR/notebook.md"
```

Use these headings and fill them with your predictions, commands, observed results and conclusions:

```markdown
# Lab 12 Evidence

## Hypothesis and selected series
## Scrape timestamps and usable sample count
## Client request ledger
## Restart change event and process identity
## Raw versus reset-aware results
## Window comparison and idle decay
## Recovery proof
## Remaining uncertainty
```

## 21. Observable Completion Criteria

- [ ] Ninety controlled successful requests are recorded with request IDs.
- [ ] One application-only restart has a timestamped change record.
- [ ] Raw samples, rate, irate, increase and reset results are preserved.
- [ ] The deterministic promtool fixture passes.
- [ ] A short-window empty result is distinguished from a zero rate.
- [ ] The checkpoint item and all four stage services are healthy.

## 22. Production Implications

Counters are well suited to throughput and aggregate trends, but they are not billing ledgers. Use durable transactional records for exact financial or audit counts. Choose rate windows in relation to scrape frequency, missing samples and response time requirements. Preserve original instance series until reset handling has occurred; do not solve query noise by hiding missing telemetry.

## 23. End State and Transition

The four-service metrics stage remains healthy. Continue with [Lab 13: Histograms, Buckets, and Quantiles](Lab-13.md), where the same reset-aware rate functions are applied to cumulative histogram buckets to estimate latency distributions.
