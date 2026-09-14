# Lab 13: Histograms, Buckets, and Quantiles

## Purpose and Scope

> **Primary Objective:** Calculate latency quantiles from real cumulative histogram buckets, validate the underlying counts, and explain interpolation and low-volume uncertainty.

An average cannot tell you whether a small fraction of requests was slow. A histogram preserves a bounded approximation of the distribution, but it does not retain each individual duration. Its usefulness depends on the bucket boundaries, the selected traffic and the amount of evidence.

You will generate a small mixture of fast and delayed requests, inspect exact snapshot deltas, and calculate p50, p95 and p99 with PromQL. The experiment uses the existing bounded demo endpoint; it does not add a new business feature or a monitoring component.

## 1. Inherited State and Starting Checks

```bash
source lab-notes/session.sh
source lab-notes/evidence.sh
source lab-notes/raw-metrics.sh
source lab-notes/metrics-session.sh
metrics_check
start_lab 13
api -fsS "$APP_URL/api/v1/demo/work?iterations=1000&delay_ms=0" >/dev/null
snapshot "$LAB_DIR/before.json"
pq 'up{job="fastapi"}' > "$LAB_DIR/starting-up.json"
```

Complete [Lab 12](Lab-12.md). Keep the same four services and 15-second scrape interval. Do not restart or rebuild the application between this lab's two raw snapshots. Do not run a second workload against the demo route during the experiment.

The warm-up request creates the relevant histogram child before the baseline snapshot. It is excluded from the exact 80-request delta below, though a Prometheus range can still include it.

## 2. Learning Objectives and Scope

You will verify cumulative buckets, derive non-overlapping bucket counts, calculate a weighted mean, preserve the `le` label when aggregating classic histograms, and explain why quantile estimates can differ from measured client timings.

No native-histogram migration, summary metric, recording rule, dashboard or alert is introduced. The application already uses a classic histogram with seconds as its unit. The metric path remains direct application exposition to Prometheus.

## 3. Relevant Events and Measurement Boundaries

| Observation | Measurement boundary |
|---|---|
| Client `time_total` | Client-visible transfer duration, including connection and network overhead |
| Application histogram observation | Server middleware duration for the completed response |
| Request-completion JSON record | A particular completion with its server duration and request ID |
| Scraped bucket sample | Accumulated count of observations at or below a boundary |
| Quantile query | Estimate derived from sampled bucket growth in a time range |

The `delay_ms` input is a requested sleep duration, not a promise about end-to-end latency. Scheduling, framework work and local contention add time. The route allows only one demo operation at a time; concurrent calls can return 429, which would change the distribution.

## 4. Inspect the Actual Bucket Schema

```bash
jq '[.[] | select(.name == "application_http_request_duration_seconds_bucket" and
  .labels.method == "GET" and .labels.route == "/api/v1/demo/work") |
  {le:.labels.le,value}]' "$LAB_DIR/before.json"
```

Finite boundaries are `0.005`, `0.01`, `0.025`, `0.05`, `0.1`, `0.25`, `0.5`, `1`, `2.5`, `5` and `10` seconds. The client also exposes the `+Inf` bucket, `_count`, `_sum` and a creation-time sample.

For one method/route label set, that means 12 bucket series plus count and sum: 14 measurement series, or 15 including `_created` with this client's default. The status code is not a histogram label in this application. Quantiles for this route combine all its response statuses; selecting `status_code="200"` on this histogram would invent a label and return no data.

## 5. Understand Cumulative Counts Before Querying Quantiles

A 0.4-second observation increments the 0.5, 1, 2.5, 5, 10 and `+Inf` buckets. It also increments `_count` once and adds its measured duration to `_sum`.

Therefore, do not add all bucket counts to get total requests. The `+Inf` bucket already counts every observation and should agree with `_count` for the same labels and snapshot. Adjacent cumulative bucket differences describe non-overlapping intervals.

Predict which interval should contain most fast requests and which should contain the delayed requests. Also predict whether p95 must equal exactly 0.4 seconds. Record the predictions before generating load.

## 6. Generate a Bounded Mixed-Latency Workload

```bash
record_change "begin_80_sequential_histogram_requests" planned
printf 'request_id,delay_ms,status,client_seconds\n' > "$LAB_DIR/client-timings.csv"
for n in $(seq 1 80); do
  delay=0
  if (( n % 5 == 0 )); then delay=400; fi
  rid="lab13-${n}-$(date +%s)"
  result=$(api -sS -o /dev/null -w '%{http_code} %{time_total}' \
    -H "X-Request-ID: $rid" \
    "$APP_URL/api/v1/demo/work?iterations=1000&delay_ms=$delay") || break
  read -r status seconds <<< "$result"
  printf '%s,%s,%s,%s\n' "$rid" "$delay" "$status" "$seconds" >> "$LAB_DIR/client-timings.csv"
  if [[ "$status" != 200 ]]; then
    printf 'Unexpected status %s; stop and inspect the ledger\n' "$status" >&2
    break
  fi
  sleep 0.5
done
snapshot "$LAB_DIR/after.json"
record_change "bounded_histogram_workload_finished" completed
capture_app_logs
```

Expected: 64 fast and 16 delayed successful requests, taking roughly 47 seconds plus overhead. The loop is intentionally sequential and capped at 80; it is not a stress test.

If the loop stops early, keep the evidence. Diagnose the failure, then start a fresh lab evidence directory and repeat the controlled experiment. Do not edit a partial ledger to look complete.

## 7. Validate the Client Ledger and Compare Client Quantiles

```bash
python3 - "$LAB_DIR/client-timings.csv" <<'PYTHON'
import csv, math, sys
rows = list(csv.DictReader(open(sys.argv[1])))
assert len(rows) == 80 and all(r["status"] == "200" for r in rows)
assert sum(r["delay_ms"] == "400" for r in rows) == 16
values = sorted(float(r["client_seconds"]) for r in rows)
print("client_mean_seconds", sum(values) / len(values))
for q in (0.5, 0.95, 0.99):
    print(f"client_nearest_rank_p{int(q*100)}_seconds", values[math.ceil(q * len(values)) - 1])
PYTHON
```

These are nearest-rank quantiles of the 80 recorded client durations. Other statistical quantile conventions interpolate individual values differently. State the method when comparing results.

They are not expected to match Prometheus exactly: the measurement boundary, selected window and approximation method differ. With only 80 observations, p99 is particularly sensitive to the slowest few requests.

## 8. Implement an Exact Histogram-Delta Check

```bash
cat > lab-notes/histogram_delta.py <<'PYTHON'
"""Compare one histogram across two snapshots without a process restart."""
import json
import math
import sys
from pathlib import Path

before, after = [json.loads(Path(name).read_text()) for name in sys.argv[1:3]]
expected = int(sys.argv[3])
prefix = "application_http_request_duration_seconds"
labels = {"method": "GET", "route": "/api/v1/demo/work"}


def select(rows, name):
    return [r for r in rows if r["name"] == name and all(r["labels"].get(k) == v for k, v in labels.items())]


def total(rows, name):
    return sum(r["value"] for r in select(rows, name))


starts = [[r["value"] for r in rows if r["name"] == "process_start_time_seconds"] for rows in (before, after)]
assert len(starts[0]) == 1 and starts[0] == starts[1], "Process restart: do not subtract snapshots"
count = total(after, prefix + "_count") - total(before, prefix + "_count")
seconds = total(after, prefix + "_sum") - total(before, prefix + "_sum")
old = {float(r["labels"]["le"]): r["value"] for r in select(before, prefix + "_bucket")}
new = {float(r["labels"]["le"]): r["value"] for r in select(after, prefix + "_bucket")}
buckets = [(bound, value - old.get(bound, 0.0)) for bound, value in sorted(new.items())]
assert count == expected, f"Expected {expected} observations, found {count}; check competing traffic"
assert buckets and math.isinf(buckets[-1][0]) and buckets[-1][1] == count
assert seconds >= 0 and all(v >= 0 for _, v in buckets)
previous = 0.0
print("upper_seconds,cumulative_observations,observations_in_this_interval")
for bound, value in buckets:
    assert value >= previous, "Cumulative bucket counts must not decrease"
    print(f"{bound},{value},{value - previous}")
    previous = value
print(f"observations={count:g}; observed_seconds={seconds:.6f}; mean_seconds={seconds/count:.6f}")
PYTHON
```

```bash
python3 lab-notes/histogram_delta.py "$LAB_DIR/before.json" "$LAB_DIR/after.json" 80 \
  | tee "$LAB_DIR/histogram-delta.txt"
```

Expected: 80 new observations; nondecreasing cumulative counts; `+Inf` delta equal to 80; and a positive mean. On an otherwise quiet VM, approximately 16 new observations should lie above 0.25 seconds, with most of those at or below 0.5 seconds. Treat those positions as predictions to test, not guaranteed results under contention.

The script rejects a changed process-start timestamp. Raw subtraction across a process reset is invalid. The zero default for a previously absent bucket is limited to this controlled child-creation comparison; it is not a general policy for missing Prometheus telemetry.

## 9. Query Reset-Aware Bucket Rates

```bash
pq 'sum by (le) (rate(application_http_request_duration_seconds_bucket{job="fastapi",method="GET",route="/api/v1/demo/work"}[2m]))' \
  | tee "$LAB_DIR/bucket-rates.json" | jq '.data.result'
pq 'sum(rate(application_http_request_duration_seconds_count{job="fastapi",method="GET",route="/api/v1/demo/work"}[2m]))' | jq .
```

Allow a successful scrape after the workload before capturing final query results. Bucket rates remain cumulative and have observations/second as their unit. Their `+Inf` value should match the count rate when evaluated at the same time.

Use `rate` on each original series before summing. Keep `le` during classic-histogram aggregation; removing it discards the bucket boundary required by `histogram_quantile`. The [official histogram guide](https://prometheus.io/docs/practices/histograms/) explains this aggregation pattern.

## 10. Calculate p50, p95 and p99

```bash
pq 'histogram_quantile(0.50, sum by (le) (rate(application_http_request_duration_seconds_bucket{job="fastapi",method="GET",route="/api/v1/demo/work"}[2m])))' \
  | tee "$LAB_DIR/p50.json" | jq .
pq 'histogram_quantile(0.95, sum by (le) (rate(application_http_request_duration_seconds_bucket{job="fastapi",method="GET",route="/api/v1/demo/work"}[2m])))' \
  | tee "$LAB_DIR/p95.json" | jq .
pq 'histogram_quantile(0.99, sum by (le) (rate(application_http_request_duration_seconds_bucket{job="fastapi",method="GET",route="/api/v1/demo/work"}[2m])))' \
  | tee "$LAB_DIR/p99.json" | jq .
```

Results are seconds. Multiply by 1000 only when you intentionally want milliseconds. On a quiet machine, p50 should remain in a fast bucket while p95 and p99 fall in a slower bucket. Query results can include the warm-up request and other observations inside the two-minute range.

These are estimates of the distribution in the selected window, not a list of slow request IDs. Return to the request ledger and JSON records when you need to identify particular completions.

## 11. Work Through the Interpolation

Suppose the delta contains exactly 64 observations at or below 0.25 seconds and all 80 at or below 0.5 seconds. The 95th-percentile rank is 76. It lies 12 observations into a 16-observation bucket, so linear interpolation gives:

```text
0.25 + (76 - 64) / (80 - 64) * (0.5 - 0.25) = 0.4375 seconds
```

The application may have actually observed sixteen durations clustered near 0.401 seconds. The histogram cannot know their exact positions inside that bucket. A p95 near 0.438 seconds is therefore compatible with that workload.

For this classic histogram, interpolation assumes a uniform distribution within the selected finite bucket. The top `+Inf` bucket cannot provide a finite upper bound; a quantile falling there uses the preceding finite boundary under Prometheus's documented behavior. A chart near the largest finite boundary can understate an unbounded tail. Inspect bucket population, not just the displayed percentile.

## 12. Calculate Mean Latency With the Same Population

```bash
pq 'sum(rate(application_http_request_duration_seconds_sum{job="fastapi",method="GET",route="/api/v1/demo/work"}[2m])) / sum(rate(application_http_request_duration_seconds_count{job="fastapi",method="GET",route="/api/v1/demo/work"}[2m]))' \
  | tee "$LAB_DIR/mean.json" | jq .
```

The numerator is accumulated seconds per second; the denominator is observations per second. Their ratio is seconds per observation. With the intended mixture, the mean should be much lower than p95.

Do not average per-instance means or percentiles without considering their different request counts. A high-traffic instance and an almost idle instance should not automatically receive equal weight in a service-level result.

## 13. Measure a Threshold Fraction at an Existing Boundary

```bash
pq '1 - sum(rate(application_http_request_duration_seconds_bucket{job="fastapi",method="GET",route="/api/v1/demo/work",le="0.25"}[2m])) / sum(rate(application_http_request_duration_seconds_count{job="fastapi",method="GET",route="/api/v1/demo/work"}[2m]))' \
  | tee "$LAB_DIR/fraction-over-250ms.json" | jq .
```

This estimates the fraction strictly above 250 ms. The `le="0.25"` bucket includes observations equal to the boundary. Under the intended workload, the result should be near 0.2, subject to window and observed scheduling effects.

A boundary-aligned fraction does not need within-bucket interpolation. If your future objective is 300 ms, this histogram has no exact 300 ms boundary; a percentile estimate does not create one. Add a deliberate bucket boundary through a reviewed instrumentation change when precision at that threshold matters.

## 14. Compare Global and Per-Route Quantiles

```bash
pq 'histogram_quantile(0.95, sum by (le,route,method) (rate(application_http_request_duration_seconds_bucket{job="fastapi"}[2m])))' | jq .
pq 'histogram_quantile(0.95, sum by (le) (rate(application_http_request_duration_seconds_bucket{job="fastapi"}[2m])))' | jq .
```

The first preserves route and method. The second intentionally combines the entire selected HTTP population, so a busy fast route can hide a slower low-volume route.

A correct combined quantile aggregates compatible bucket counts first. Averaging p95 values cannot reconstruct the combined distribution. Before combining histograms from different versions, verify compatible boundaries and measurement semantics.

## 15. Observe Low-Volume and Idle Uncertainty

```bash
pq 'sum(increase(application_http_request_duration_seconds_count{job="fastapi",method="GET",route="/api/v1/demo/work"}[2m]))' \
  > "$LAB_DIR/estimated-window-observations.json"
pq 'histogram_quantile(0.99, sum by (le) (rate(application_http_request_duration_seconds_bucket{job="fastapi",method="GET",route="/api/v1/demo/work"}[5s])))' \
  > "$LAB_DIR/too-short-window.json"
jq . "$LAB_DIR/estimated-window-observations.json" "$LAB_DIR/too-short-window.json"
```

The count gives essential context for a quantile. A p99 supported by tens of observations is not strong evidence about a stable rare tail. A five-second window usually has too few scrape samples here and can produce no quantile result.

After a long enough idle period, valid zero bucket rates lead to an undefined quantile (`NaN`); insufficient series may instead produce an empty result. Neither means zero latency. Preserve those distinctions in later dashboards and alerts.

## 16. Correlate a Slow Request With Its JSON Record

```bash
RID=$(awk -F, 'NR>1 && $2==400 {print $1; exit}' "$LAB_DIR/client-timings.csv")
jq -c --arg rid "$RID" 'select(.event_name=="request_completed" and .request_id==$rid) |
  {timestamp,request_id,event_id,route:."http.route",status:."http.status_code",duration_ms}' \
  "$LAB_DIR/app.jsonl" | tee "$LAB_DIR/one-delayed-completion.jsonl"
```

Compare `duration_ms / 1000` with that request's client timing and its expected histogram interval. The log record supplies individual-event context that the aggregate metric intentionally omits. Traces and profiles are not required to make this limited correlation.

## 17. Recovery, Troubleshooting and Evidence Review

```bash
metrics_check
api -fsS "$APP_URL/api/v1/demo/work?iterations=1000&delay_ms=0" > "$LAB_DIR/recovery-response.json"
snapshot "$LAB_DIR/recovered.json"
metric_sum "$LAB_DIR/recovered.json" application_http_requests_in_progress
record_change "histogram_experiment_recovery_verified" completed
```

Expected: readiness succeeds, the fast probe returns 200 and in-progress is zero after completion. Histogram counters retain previous observations; recovery does not reset them.

| Symptom | Check | Interpretation |
|---|---|---|
| Delta is not 80 | Ledger, competing demo traffic and process-start samples | The controlled population changed; repeat with fresh evidence |
| Bucket totals exceed 80 when added | Cumulative semantics | Use `+Inf` or `_count`, not the sum of buckets |
| Quantile is empty | Labels, preserved `le`, available samples | Correct selection before changing math |
| Quantile is NaN | Observation rate and bucket shape | No observations or an unusable distribution |
| p95 differs from client p95 | Boundary, window and interpolation method | A difference can be expected; compare equivalent populations first |
| Slow bucket exceeds 0.5 seconds | Actual completion durations and host contention | Delay is a lower component of latency, not an upper bound |
| 429 appears | Other concurrent demo work | Stop overlapping generators and preserve the partial run |

Keep original snapshots, timing ledger, log evidence and query responses together.

## 18. Knowledge Check

1. Why must le remain in classic-histogram aggregation?
2. Why is summing buckets not a request count?
3. Can average(p95) produce a service p95?
4. Why might p95 be 0.4375 when delayed requests take about 0.401 seconds?
5. Does NaN mean zero latency?

### Answer Guide

1. It identifies each cumulative boundary used to reconstruct the distribution.
2. One observation contributes to several cumulative buckets.
3. No; combine compatible bucket populations and calculate the quantile afterward.
4. The histogram retains counts inside a bucket and interpolates their unknown positions.
5. No; the quantile may be undefined because no observations are present.

## 19. Professional Scenario Exercise

A dashboard reports p99 at 500 ms after only six requests, while an engineer claims the service has a stable 500 ms tail. Review the sample population, bucket layout, query window and individual records. Write a conclusion that separates an observed slow response from a statistically well-supported tail claim.

## 20. Lab Notebook Template

Create a notebook in the evidence directory for this run:

```bash
test -e "$LAB_DIR/notebook.md" || printf '# Lab 13 Evidence\n' > "$LAB_DIR/notebook.md"
```

Use these headings and fill them with your predictions, commands, observed results and conclusions:

```markdown
# Lab 13 Evidence

## Predicted distribution
## Bucket schema and cardinality
## Bounded workload and client timings
## Exact snapshot invariants
## PromQL quantiles and observation count
## Interpolation calculation
## One-request correlation
## Recovery and uncertainty
```

## 21. Observable Completion Criteria

- [ ] The controlled workload contains 80 successful requests with 16 requested delays.
- [ ] Snapshot validation proves cumulative bucket monotonicity and +Inf/count agreement.
- [ ] Mean, p50, p95, p99 and a boundary-aligned threshold fraction are queried.
- [ ] At least one individual delayed completion is correlated with client evidence.
- [ ] Low-volume and empty/NaN results are explained.
- [ ] All four services are healthy and no workload remains in progress.

## 22. Production Implications

Choose histogram boundaries around operational questions, not merely convenient round numbers. More buckets improve resolution at a storage cost multiplied by every label combination. Preserve seconds internally, display units explicitly, and pair percentiles with volume. A service-wide quantile can hide route-specific behavior; retain enough scope to make it actionable.

## 23. End State and Transition

The same four-service stage remains running. Continue with [Lab 14: Host Monitoring with Node Exporter and USE](Lab-14.md) to inspect whether CPU, memory, storage or network evidence can help explain latency observed at the application boundary.
