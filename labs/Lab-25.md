# Lab 25: Define SLIs, SLOs, and Error Budgets

## 1. Purpose and Learning Outcomes

**In Plain Language:** You will define exactly which requests count toward availability and latency objectives. Implement those definitions, test exclusions and zero traffic, and compare a controlled ledger with raw metric changes. Then calculate a request-based error budget while distinguishing a short teaching window from a fully covered long-term service commitment.

> **Primary Objective:** Define measurable Items availability and latency populations, validate their implementation and calculate request budgets without overstating data coverage.

An SLI is a measurement with a defined population and observation boundary. An SLO adds a target and a time window. An error budget expresses how much bad service that objective permits.

This lab specifies two Items objectives, implements short-window measurements, tests eligibility with fixtures and real requests, and calculates budgets. It does not implement burn-rate alerts or an SLO operations dashboard; those belong to Lab 26. The local three-day retention cannot prove a complete 30-day SLO.

## 2. Starting Point and Lab Map

Use this guide inside the complete application repository. Run commands from the repository root in Bash unless a step says otherwise. Work through the starting checks in order; they load the helpers and state this lab needs.

Read the explanation before each step, run one complete code block at a time, and compare the actual result with the stated expectation. Save commands, observations, explanations, and unexpected results in your notebook.

### Key Terms

| **Term**     | **Plain-Language Meaning**                                              |
| ------------ | ----------------------------------------------------------------------- |
| SLI          | A measurement of service behavior for a defined eligible population.    |
| SLO          | A target for that measurement over a stated time window.                |
| Error budget | The allowed bad outcomes implied by the target and eligible population. |

### Lab Map

The arrows show the flow or dependency being studied. Branches identify paths to compare; this is a conceptual map, not a captured runtime trace.

```mermaid
flowchart TD
    U["User request attempt"] --> E["Network and edge boundary"]
    E --> A["FastAPI completed response"]
    A --> C["Status counter"]
    A --> H["Duration histogram"]
    C --> S["Availability SLI"]
    H --> L["Latency SLI"]
```

## 3. Guided Walkthrough

### Step 01. Inherited State and Starting Checks

**What You Are Doing:** Verify the complete stage and stop competing API traffic. Exact population checks require knowing which requests occurred between the snapshots.

**Practical Walkthrough:** Check the full alerting stage and stop competing API traffic before taking raw snapshots. Exact request-population assertions require knowing what occurred between the snapshots and keeping the app process stable. Retain prior historical data, but distinguish it from the new controlled sequence.

Stop unrelated workload generators and take both raw snapshots within one process lifetime. Historical data can remain in Prometheus, but the exact-delta experiment needs a controlled interval. Record unexpected traffic or resets before attempting to reconcile the planned request population.

```bash
source lab-notes/session.sh
source lab-notes/evidence.sh
source lab-notes/raw-metrics.sh
source lab-notes/metrics-session.sh
metrics_check
start_lab 25
```

Complete [Lab 24](Lab-24.md) first. Run commands from the repository root in the same Bash session. Keep the existing credentials, named volumes and checkpoint item. Keep nine services and six jobs healthy, the notification recorder stopped, and no lab silence active. Stop other API clients during the exact-population experiment.

**Understanding the Result:** Controlled raw deltas and time-window queries have different scopes. Establish the controlled population before comparing them.

### Step 02. Learning Objectives and Observation Boundary

**What You Are Doing:** Locate the observation boundary before choosing a target. These measurements describe completed server-observed responses and cannot automatically include requests that never reached that point.

**Practical Walkthrough:** Locate where completed responses become instrument observations. These SLIs cover server-observed completions, so requests that never reach the app or never reach that observation boundary are not automatically included. State that coverage before interpreting the result as user experience.

Identify the completion observer as the SLI's measurement boundary. A client failure before reaching that boundary may be absent from these counters. State this coverage limitation with the result so server-observed availability is not presented as a complete measurement of every user's attempted request.

You will distinguish specification from implementation, define eligible/good/bad events, use a histogram threshold instead of a percentile for a latency ratio, handle zero traffic honestly and calculate a request budget over an explicit observation window.

The lab map in Section 2 shows this relationship.

This implementation observes completed responses inside the app. Connection failures before the app, some disconnects and unobserved process failures are not fully represented. A production user-experience objective may need edge/client or synthetic observations as well. The [SRE workbook on implementing SLOs](https://sre.google/workbook/implementing-slos/) explains why the specification and measurement implementation should be distinguished.

**Understanding the Result:** An SLI's denominator defines its visibility. A high percentage does not prove success for unobserved requests.

### Step 03. Write the Two Explicit Contracts

**What You Are Doing:** Define eligible and good events separately for availability and latency. Their denominators intentionally differ, so equal percentages need not represent equal populations.

**Practical Walkthrough:** Read the two eligibility policies separately. Availability includes the stated successful and server-error response classes, while the latency policy includes the broader Items response population, including client errors. Keep demo routes excluded as specified and attach each target to its own denominator.

Build separate eligible-event lists for availability and latency. Check client errors against each policy rather than assuming both denominators are identical. Keep the Items route scope and demo exclusion explicit; a mathematically correct ratio can still implement the wrong policy if its population differs.

| **Field**              | **Availability**                                                    | **Latency**                                  |
| ---------------------- | ------------------------------------------------------------------- | -------------------------------------------- |
| Service scope          | Selected environment/service; normalized Items list and item routes | Same routes                                  |
| Eligible population    | Completed 2xx, 3xx and 5xx responses                                | All completed Items responses, including 4xx |
| Good event             | 2xx or 3xx                                                          | Duration ≤250 ms                             |
| Bad event              | 5xx                                                                 | Duration >250 ms                             |
| Proposed objective     | 99.5% good                                                          | 99% good                                     |
| Proposed policy window | Rolling 30 days                                                     | Rolling 30 days                              |
| Learning measurement   | Raw isolated counter deltas and a 10-minute Prometheus window       | Same, with the existing 0.25-second bucket   |

The duration histogram has no status-code label. Therefore this lab does **not** claim to measure successful-responses-only latency or the combined “successful and fast” population. A fast 503 can meet the latency threshold while failing availability; evaluate both contracts.

Availability excludes 4xx as a deliberate local policy, not a universal truth. A buggy release causing valid users to receive 4xx could be hidden by this choice. Document and revisit that risk. Demo, health, metrics, documentation and unmatched routes are excluded.

**Understanding the Result:** Different denominators are intentional. Do not force availability and latency counts to match by silently changing eligibility.

### Step 04. Save a Concrete Policy Document

**What You Are Doing:** Save the policy in a concrete document before implementing it. Targets, route scope, exclusions, and coverage limitations are part of the agreement being tested.

**Practical Walkthrough:** Write the policy document before creating expressions so route scope, good-event rules, exclusions, targets, and coverage are reviewable. Use the specified 99.5% availability and 99% latency target with its 250-millisecond boundary. This document is the reference when tests or live results need interpretation.

Read the saved policy as the reference contract for all following code and tests. Verify the availability target, latency target, threshold, and exclusions before writing expressions. If implementation and policy disagree later, resolve the discrepancy explicitly rather than silently changing the definition to match observed output.

```bash
cat > lab-notes/slo-policy.md <<'MARKDOWN'
# Items API learning SLO policy

- Owner: platform learning operator.
- Scope: one explicitly selected environment and service; normalized /api/v1/items and /api/v1/items/{item_id} routes.
- Availability: 99.5% of eligible completed 2xx/3xx/5xx responses are 2xx/3xx over a rolling 30-day window.
- Latency: 99% of all completed Items responses, including 4xx, finish within 250 ms over a rolling 30-day window.
- Measurement boundary: application middleware, not complete end-user/network experience.
- Missing traffic/data: undefined or incomplete, never automatically 100% successful.
- Current coverage: local retention is three days; this lab produces short-window learning measurements only.
- Budget policy: investigate accelerated consumption; when the measured budget is exhausted, prioritize reliability and review risky changes with the service owner.
- Review: reassess populations, thresholds, retained history and user expectations before production adoption.
MARKDOWN
```

**Command Note:** `<<'MARKDOWN'` writes the following block literally until `MARKDOWN`. Quoting the delimiter prevents Bash from expanding `$variables` inside the generated file; creation and execution are separate steps.

Targets are explicit teaching proposals. They need stakeholder/user validation before becoming a production commitment. An SLO is not an SLA, and this single-node learning deployment makes no high-availability promise.

**Understanding the Result:** The policy determines the implementation. A convenient query result should not retroactively redefine good service.

### Step 05. Implement the Short-Window SLI Rules

**What You Are Doing:** Implement the short-window expressions using the precise normalized Items routes. Keep reset handling, label alignment, and the chosen latency boundary consistent with the policy.

**Practical Walkthrough:** Implement recordings using the exact normalized Items route labels and matching populations. Apply reset handling before aggregation and use the existing histogram boundary for latency. Keep numerator and denominator windows aligned so their ratio represents the same request interval.

Inspect exact route-template matching and confirm the latency bucket exists at the required boundary. Calculate reset-aware values before aggregation and align windows in each ratio. The recordings should implement the two documented contracts, including their different eligibility, rather than merely produce similarly named percentages.

```bash
cat > lab-notes/prometheus/slo-recording-rules.yml <<'YAML'
groups:
- name: items-sli-learning
  interval: 15s
  rules:
  - record: service:slo_items_eligible_requests:rate5m
    expr: sum by (environment, service) (rate(application_http_requests_total{job="fastapi",route=~"/api/v1/items(/\\{item_id\\})?",status_code=~"2..|3..|5.."}[5m]))
  - record: service:slo_items_bad_requests:rate5m
    expr: sum by (environment, service) (rate(application_http_server_errors_total{job="fastapi",route=~"/api/v1/items(/\\{item_id\\})?"}[5m]))
  - record: service:slo_items_latency_observations:rate5m
    expr: sum by (environment, service) (rate(application_http_request_duration_seconds_count{job="fastapi",route=~"/api/v1/items(/\\{item_id\\})?"}[5m]))
  - record: service:slo_items_latency_good_observations:rate5m
    expr: sum by (environment, service) (rate(application_http_request_duration_seconds_bucket{job="fastapi",route=~"/api/v1/items(/\\{item_id\\})?",le="0.25"}[5m]))
  - record: service:slo_items_availability_error:ratio5m
    expr: (service:slo_items_bad_requests:rate5m / service:slo_items_eligible_requests:rate5m) and on (environment,
      service) (service:slo_items_eligible_requests:rate5m > 0)
  - record: service:slo_items_latency_error:ratio5m
    expr: ((service:slo_items_latency_observations:rate5m - service:slo_items_latency_good_observations:rate5m)
      / service:slo_items_latency_observations:rate5m) and on (environment, service) (service:slo_items_latency_observations:rate5m
      > 0)
YAML
```

```bash
python3 - <<'PYTHON'
from pathlib import Path
p=Path("lab-notes/prometheus/prometheus.yml");t=p.read_text()
anchor="- /etc/prometheus/labs/application-alerts.yml\n"
addition="- /etc/prometheus/labs/slo-recording-rules.yml\n"
if addition not in t:
    assert t.count(anchor)==1, "Inspect the expected Lab 24 configuration"
    p.write_text(t.replace(anchor,anchor+addition))
PYTHON
```

The route regex matches only the two normalized Items routes, including literal `{item_id}` braces. Raw counter rates are aggregated after reset handling. The initialized server-error counter supplies real zero-error observations after a route is exercised, avoiding invented zero series.

Within this one rule group, the first four rules produce rates and the final two compute matching ratios. Positive denominators are required. A rate gauge is not a count; do not sum sampled rates over time and call the result a request total without accounting for cadence/coverage.

**Understanding the Result:** Metric names alone do not establish policy compliance. Inspect route selection, status eligibility, boundary, labels, and units.

### Step 06. Test Population Definitions and Zero Traffic

**What You Are Doing:** Test the population definitions using known successes, server errors, client errors, and idle periods. Verify that zero traffic is not silently reported as demonstrated perfect service.

**Practical Walkthrough:** Run fixtures covering successes, server errors, client errors, and periods with no requests. Check each case against both eligibility definitions. Idle input must remain distinguishable from demonstrated perfect service, because no observed events cannot establish how future requests would behave.

Read each fixture's status population and expected eligibility before executing it. Include no-traffic cases and inspect whether the result is undefined or absent as designed. Lack of observed requests is insufficient evidence of perfect service, so keep idle behavior visible in tests and interpretation.

```bash
cat > lab-notes/prometheus/slo-tests.yml <<'YAML'
rule_files:
- slo-recording-rules.yml
evaluation_interval: 15s
fuzzy_compare: true
tests:
- name: different availability and latency populations
  interval: 15s
  input_series:
  - series: application_http_requests_total{job="fastapi",environment="local",service="items-info",route="/api/v1/items",method="GET",status_code="200"}
    values: 0+95x20
  - series: application_http_requests_total{job="fastapi",environment="local",service="items-info",route="/api/v1/items",method="GET",status_code="503"}
    values: 0+5x20
  - series: application_http_requests_total{job="fastapi",environment="local",service="items-info",route="/api/v1/items",method="GET",status_code="404"}
    values: 0+20x20
  - series: application_http_server_errors_total{job="fastapi",environment="local",service="items-info",route="/api/v1/items",method="GET"}
    values: 0+5x20
  - series: application_http_request_duration_seconds_count{job="fastapi",environment="local",service="items-info",route="/api/v1/items",method="GET"}
    values: 0+120x20
  - series: application_http_request_duration_seconds_bucket{job="fastapi",environment="local",service="items-info",route="/api/v1/items",method="GET",le="0.25"}
    values: 0+114x20
  promql_expr_test:
  - expr: service:slo_items_eligible_requests:rate5m
    eval_time: 5m
    exp_samples:
    - labels: service:slo_items_eligible_requests:rate5m{environment="local",service="items-info"}
      value: 6.666666666666667
  - expr: service:slo_items_bad_requests:rate5m
    eval_time: 5m
    exp_samples:
    - labels: service:slo_items_bad_requests:rate5m{environment="local",service="items-info"}
      value: 0.3333333333333333
  - expr: service:slo_items_latency_observations:rate5m
    eval_time: 5m
    exp_samples:
    - labels: service:slo_items_latency_observations:rate5m{environment="local",service="items-info"}
      value: 8.0
  - expr: service:slo_items_latency_good_observations:rate5m
    eval_time: 5m
    exp_samples:
    - labels: service:slo_items_latency_good_observations:rate5m{environment="local",service="items-info"}
      value: 7.6
  - expr: round(service:slo_items_availability_error:ratio5m, 0.000001)
    eval_time: 5m
    exp_samples:
    - labels: '{environment="local",service="items-info"}'
      value: 0.05
  - expr: round(service:slo_items_latency_error:ratio5m, 0.000001)
    eval_time: 5m
    exp_samples:
    - labels: '{environment="local",service="items-info"}'
      value: 0.05
- name: idle population stays undefined
  interval: 15s
  input_series:
  - series: application_http_requests_total{job="fastapi",environment="local",service="items-info",route="/api/v1/items",method="GET",status_code="200"}
    values: 0+0x20
  - series: application_http_requests_total{job="fastapi",environment="local",service="items-info",route="/api/v1/items",method="GET",status_code="503"}
    values: 0+0x20
  - series: application_http_requests_total{job="fastapi",environment="local",service="items-info",route="/api/v1/items",method="GET",status_code="404"}
    values: 0+0x20
  - series: application_http_server_errors_total{job="fastapi",environment="local",service="items-info",route="/api/v1/items",method="GET"}
    values: 0+0x20
  - series: application_http_request_duration_seconds_count{job="fastapi",environment="local",service="items-info",route="/api/v1/items",method="GET"}
    values: 0+0x20
  - series: application_http_request_duration_seconds_bucket{job="fastapi",environment="local",service="items-info",route="/api/v1/items",method="GET",le="0.25"}
    values: 0+0x20
  promql_expr_test:
  - expr: service:slo_items_eligible_requests:rate5m
    eval_time: 5m
    exp_samples:
    - labels: service:slo_items_eligible_requests:rate5m{environment="local",service="items-info"}
      value: 0
  - expr: service:slo_items_bad_requests:rate5m
    eval_time: 5m
    exp_samples:
    - labels: service:slo_items_bad_requests:rate5m{environment="local",service="items-info"}
      value: 0
  - expr: service:slo_items_latency_observations:rate5m
    eval_time: 5m
    exp_samples:
    - labels: service:slo_items_latency_observations:rate5m{environment="local",service="items-info"}
      value: 0
  - expr: service:slo_items_latency_good_observations:rate5m
    eval_time: 5m
    exp_samples:
    - labels: service:slo_items_latency_good_observations:rate5m{environment="local",service="items-info"}
      value: 0
  - expr: round(service:slo_items_availability_error:ratio5m, 0.000001)
    eval_time: 5m
    exp_samples: []
  - expr: round(service:slo_items_latency_error:ratio5m, 0.000001)
    eval_time: 5m
    exp_samples: []
YAML
```

```bash
chmod 644 lab-notes/prometheus/slo-recording-rules.yml lab-notes/prometheus/slo-tests.yml
dm run --rm -T --no-deps --entrypoint promtool prometheus check rules /etc/prometheus/labs/slo-recording-rules.yml
dm run --rm -T --no-deps --entrypoint promtool prometheus test rules /etc/prometheus/labs/slo-tests.yml
reload_prometheus
```

Predict the first fixture: each 15-second increment contains 95 successful responses, five server errors and twenty 4xx responses. Availability has 100 eligible and five bad events; latency has 120 observations with 114 within threshold. Both error fractions are 5%, but their denominators differ.

The idle fixture keeps source counters flat and expects no ratio, not a fabricated 100% SLI. Ratio assertions round only for floating-point comparison; the production rule values are not rounded. The fixtures validate semantics without manufacturing real production incidents.

**Understanding the Result:** Tests should verify exclusions as well as inclusions. Zero traffic needs its own interpretation rather than an automatic 100% success claim.

### Step 07. Prepare a Client Ledger for a Bounded Experiment

**What You Are Doing:** Prepare a client ledger that records the population independently of Prometheus. Predict eligible and excluded events before generating the sequence.

**Practical Walkthrough:** Prepare the client ledger and predict which planned responses belong to each SLI before running the sequence. Keep identifiers, timestamps, and statuses so the population can be independently reconciled. Latency quality still depends on actual measured durations and cannot be assigned solely from intended scenario names.

Save expected IDs, timestamps, routes, and statuses before taking the workload's first snapshot. Predict eligibility separately from latency goodness. The actual duration measurement decides whether an eligible request satisfies the threshold; a scenario called normal is not automatically a good latency event.

```bash
MISSING_ID=$(new_uuid)
: > "$LAB_DIR/slo-client.jsonl"
observe_slo_request() {
  local expected="$1" route="$2" url="$3" status
  shift 3
  status=$(api -sS -o /dev/null -w '%{http_code}' "$@" "$url") || return 1
  if [[ "$status" != "$expected" ]]; then printf 'Unexpected status %s, expected %s\n' "$status" "$expected" >&2; return 1; fi
  printf '{"route":"%s","status":%s}\n' "$route" "$status" >> "$LAB_DIR/slo-client.jsonl"
}
snapshot "$LAB_DIR/slo-before.json"
```

Predict the eligible and total populations for the next sequence before running it. Keep all other clients quiet. The helper uses only fixed normalized route strings in a local ledger; it does not add route IDs or request IDs to Prometheus labels.

**Understanding the Result:** The ledger records observed attempts and outcomes. Its eligibility mapping should follow the saved policy consistently.

### Step 08. Generate Success, Excluded Outcomes and Failures

**What You Are Doing:** Generate the controlled mixture, including outcomes deliberately excluded by one or both contracts. Real response duration determines latency quality; the test should not invent that result.

**Practical Walkthrough:** Run the prescribed mixture, retaining outcomes that one policy excludes or counts differently. Keep the process and traffic scope controlled between snapshots. For latency, use actual observations at the defined threshold rather than assuming every normal request is fast or every planned delay crosses it.

Run the mixture once in the documented order and retain unexpected statuses. Keep recovery attached to the dependency-failure portion. If actual outcomes differ from predictions, reconcile eligibility using what happened rather than assigning each request the status or latency class the script intended to produce.

```bash
for index in $(seq 1 30); do
  observe_slo_request 200 '/api/v1/items' "$APP_URL/api/v1/items?limit=1"
  sleep 1
done
for index in $(seq 1 5); do
  observe_slo_request 422 '/api/v1/items' "$APP_URL/api/v1/items" -X POST \
    -H 'Content-Type: application/json' -d '{"name":"","price":-1}'
  observe_slo_request 404 '/api/v1/items/{item_id}' "$APP_URL/api/v1/items/$MISSING_ID"
  observe_slo_request 200 '/api/v1/demo/work' "$APP_URL/api/v1/demo/work?iterations=1000&delay_ms=400"
done
(
  set -euo pipefail
  trap 'dm start postgres >/dev/null' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  record_change "slo_population_six_database_failures" planned
  dm stop postgres
  for index in $(seq 1 6); do
    observe_slo_request 503 '/api/v1/items' "$APP_URL/api/v1/items?limit=1"
    sleep 1
  done
)
wait_ready
snapshot "$LAB_DIR/slo-after.json"
metrics_check
record_change "slo_population_database_recovered" completed
capture_app_logs
```

**Command Note:** `trap ... EXIT` schedules cleanup when that shell exits. Keep it in the same block as the fault; the explicit recovery checks afterward confirm that restoration actually succeeded.

Expected ledger: 36 availability-eligible requests, six availability failures, and 46 Items latency observations. The ten 4xx responses count only for latency. The five intentionally slow demo calls count in neither Items SLI.

Do not assert a specific latency-good count; actual operation duration determines it. The app can reject a database connection quickly or after a timeout. This experiment changes no schema and creates no valid item rows. The trap restores PostgreSQL on exit.

**Understanding the Result:** Classify what happened, not what the workload intended to cause. Unexpected timings are valid experimental findings.

### Step 09. Verify the Population Against Raw Metric Deltas

**What You Are Doing:** Compare exact raw deltas with the ledger before interpreting windowed estimates. A mismatch points to traffic, process lifetime, or selection issues that require explanation.

**Practical Walkthrough:** Compare raw counter and histogram deltas with the ledger before evaluating the time-window ratios. Check process continuity, matching labels, and accidental extra requests if they disagree. Raw invariants help establish whether the intended event population was measured correctly.

Read the verifier's process-continuity, route, and label checks before running it. Compare the raw population with the complete ledger first. A failed invariant should be investigated before deriving a ratio, because time-window estimation cannot repair an incorrectly measured or selected event population.

```bash
cat > lab-notes/verify_slo_population.py <<'PYTHON'
"""Usage: verify_slo_population.py BEFORE_JSON AFTER_JSON CLIENT_JSONL"""

import json, math, sys
from pathlib import Path

if len(sys.argv) != 4:
    raise SystemExit(__doc__)
before, after = (json.loads(Path(x).read_text()) for x in sys.argv[1:3])
clients = [
    json.loads(x) for x in Path(sys.argv[3]).read_text().splitlines() if x.strip()
]
routes = {"/api/v1/items", "/api/v1/items/{item_id}"}
chosen = [x for x in clients if x["route"] in routes]
expected = {
    "eligible": sum(
        200 <= x["status"] < 400 or 500 <= x["status"] < 600 for x in chosen
    ),
    "bad": sum(500 <= x["status"] < 600 for x in chosen),
    "latency_observations": len(chosen),
}


def totals(samples):
    result = dict(eligible=0.0, bad=0.0, latency_observations=0.0, latency_good=0.0)
    for s in samples:
        label = s["labels"]
        name = s["name"]
        value = float(s["value"])
        if label.get("route") not in routes:
            continue
        if name == "application_http_requests_total":
            status = int(label["status_code"])
            if 200 <= status < 400 or 500 <= status < 600:
                result["eligible"] += value
        elif name == "application_http_server_errors_total":
            result["bad"] += value
        elif name == "application_http_request_duration_seconds_count":
            result["latency_observations"] += value
        elif (
            name == "application_http_request_duration_seconds_bucket"
            and label.get("le") == "0.25"
        ):
            result["latency_good"] += value
    return result


first, second = totals(before), totals(after)
delta = {k: second[k] - first[k] for k in first}
for k, v in expected.items():
    if not math.isclose(delta[k], v, abs_tol=1e-6):
        raise SystemExit(
            f"Population mismatch for {k}: metrics {delta[k]}, ledger {v}. Check other clients or process restart."
        )
assert 0 <= delta["latency_good"] <= delta["latency_observations"]
print(json.dumps({"client_ledger": expected, "raw_metric_delta": delta}, indent=2))
PYTHON
```

```bash
python3 lab-notes/verify_slo_population.py "$LAB_DIR/slo-before.json" "$LAB_DIR/slo-after.json" \
  "$LAB_DIR/slo-client.jsonl" > "$LAB_DIR/population-proof.json"
jq . "$LAB_DIR/population-proof.json"
```

This comparison uses raw counter differences over an isolated experiment, avoiding rate extrapolation. A mismatch is evidence to investigate: another client, an app restart, a wrong route contract or missing request completion. Do not change expected values just to make the assertion pass.

Use request logs to inspect unexpected statuses. The demo can visibly worsen the broad RED dashboard while leaving the Items SLO unchanged because the populations differ. That is an intentional scope demonstration.

**Understanding the Result:** Resolve population mismatches before blaming window extrapolation. Exact deltas are the first validation layer.

### Step 10. Query a Clearly Labeled Short Window

**What You Are Doing:** Evaluate the short-window SLI queries at a shared time. Their range may include earlier traffic and estimation effects beyond the isolated ledger.

**Practical Walkthrough:** Evaluate both SLI expressions at one shared timestamp and save their range windows. A range can include activity outside the isolated ledger and estimate changes at its boundaries. Explain those differences explicitly instead of expecting every live ratio to equal the ledger's simple fraction.

Use the shared evaluation timestamp and record the selected ten-minute range. Compare that range with the isolated workload's actual start and end. Earlier traffic and extrapolated boundaries can affect the live ratio, so label it as a short-window observation rather than an exact replay of the ledger.

```bash
cat > lab-notes/slo_queries.py <<'PYTHON'
"""Usage: slo_queries.py ENVIRONMENT SERVICE WINDOW (for example 10m)"""

import json, re, sys

if len(sys.argv) != 4 or not re.fullmatch(r"[1-9][0-9]*[smhdw]", sys.argv[3]):
    raise SystemExit(__doc__)
environment, service, window = sys.argv[1:]
sel = (
    'job="fastapi",environment='
    + json.dumps(environment)
    + ",service="
    + json.dumps(service)
    + ",route=~"
    + json.dumps(r"/api/v1/items(/\{item_id\})?")
)


def increase(metric, extra=""):
    return (
        "sum by (environment, service) (increase("
        + metric
        + "{"
        + sel
        + extra
        + "}["
        + window
        + "]))"
    )


eligible = increase("application_http_requests_total", ',status_code=~"2..|3..|5.."')
bad = increase("application_http_server_errors_total")
total = increase("application_http_request_duration_seconds_count")
good = increase("application_http_request_duration_seconds_bucket", ',le="0.25"')
print(
    json.dumps(
        {
            "availability_eligible": eligible,
            "availability_bad": bad,
            "latency_observations": total,
            "latency_good": good,
            "availability_sli": f"(1 - ({bad} / {eligible})) and on (environment, service) ({eligible} > 0)",
            "latency_sli": f"({good} / {total}) and on (environment, service) ({total} > 0)",
        },
        indent=2,
    )
)
PYTHON
```

```bash
python3 lab-notes/slo_queries.py "$LAB_ENVIRONMENT" "$LAB_SERVICE" 10m > "$LAB_DIR/slo-queries.json"
sleep 20
EVAL_AT=$(date -u +%s)
for name in availability_eligible availability_bad latency_observations latency_good availability_sli latency_sli; do
  query=$(jq -er --arg name "$name" '.[$name]' "$LAB_DIR/slo-queries.json")
  api -fsS --get --data-urlencode "query=$query" --data-urlencode "time=$EVAL_AT" \
    "$PROM_URL/api/v1/query" > "$LAB_DIR/$name-10m.json"
done
jq . "$LAB_DIR/availability_sli-10m.json" "$LAB_DIR/latency_sli-10m.json"
```

**Command Note:** `jq --arg` passes a shell value as a JSON-query string variable without inserting it into the query text. Where used, `-e` turns a false or null final result into a failing exit status.

All queries use the same evaluation time. `increase` handles observed counter resets and extrapolates across the selected range, so fractional counts are possible. The ten-minute window can contain earlier traffic; it need not equal this experiment's exact ledger.

Use `le="0.25"` directly for the threshold fraction. A p95 panel does not tell you exactly what fraction met a chosen threshold. Do not filter this histogram by a nonexistent status label.

**Understanding the Result:** Fixed evaluation time makes comparisons reproducible. Window scope and sampling still distinguish queries from the isolated request count.

### Step 11. Calculate and Interpret Request Error Budgets

**What You Are Doing:** Calculate the allowed, consumed, and remaining bad-event budget for a stated population. Negative remaining budget is a meaningful result when observed failures exceed the allowance.

**Practical Walkthrough:** Calculate the allowed bad events from the stated target and eligible population, then compare observed bad events with that allowance. Keep units explicit: a budget in events differs from a ratio. If consumption exceeds the allowance, retain the negative remaining result as evidence of overspend.

Calculate allowed bad events as eligible events multiplied by the allowed bad fraction. For 10,000 eligible requests at 99.5%, the allowance is 50; compare 40 and 70 against that same allowance. Preserve negative remaining budget when overspent instead of clipping away the evidence.

```bash
cat > lab-notes/budget.py <<'PYTHON'
"""Request budget calculator; supplied population must have an explicit time window."""

import argparse, json
from decimal import Decimal, InvalidOperation

p = argparse.ArgumentParser()
p.add_argument("--eligible", required=True)
p.add_argument("--bad", required=True)
p.add_argument("--target", required=True)
a = p.parse_args()
try:
    eligible, bad, target = (Decimal(x) for x in (a.eligible, a.bad, a.target))
except InvalidOperation:
    p.error("All values must be numeric")
if not all(x.is_finite() for x in (eligible, bad, target)):
    p.error("Values must be finite")
if not (eligible > 0 and 0 <= bad <= eligible and 0 < target < 1):
    p.error("Require eligible>0, 0<=bad<=eligible, 0<target<1")
allowed = eligible * (1 - target)
result = {
    "eligible": eligible,
    "bad": bad,
    "objective": target,
    "observed_sli": 1 - bad / eligible,
    "allowed_bad": allowed,
    "remaining_bad_budget": allowed - bad,
    "budget_consumed_fraction": bad / allowed,
}
print(json.dumps({k: str(v) for k, v in result.items()}, indent=2))
PYTHON
```

```bash
python3 lab-notes/budget.py --eligible 10000 --bad 40 --target 0.995 > "$LAB_DIR/budget-with-headroom.json"
python3 lab-notes/budget.py --eligible 10000 --bad 70 --target 0.995 > "$LAB_DIR/budget-exhausted.json"
cat "$LAB_DIR/budget-with-headroom.json" "$LAB_DIR/budget-exhausted.json"
ELIGIBLE=$(jq -er '.client_ledger.eligible' "$LAB_DIR/population-proof.json")
BAD=$(jq -er '.client_ledger.bad' "$LAB_DIR/population-proof.json")
python3 lab-notes/budget.py --eligible "$ELIGIBLE" --bad "$BAD" --target 0.995 \
  > "$LAB_DIR/experiment-budget-only.json"
```

For a request population N and objective T, allowed bad events are `N × (1−T)`. Remaining budget is allowed minus observed bad; consumed fraction is bad divided by allowed.

At 10,000 eligible requests and 99.5%, the allowance is 50. Forty errors consume 80% and leave ten; seventy errors consume 140% and leave −20. Preserve negative remaining budget rather than clamping away a breach.

These are request-weighted budgets, not automatically minutes of downtime. Converting the percentage to time assumes a different measurement model. The last calculation describes only the artificial experiment, not 30-day compliance. A zero-event population is rejected because its ratio is undefined.

**Understanding the Result:** A negative remaining budget is meaningful. Do not clamp it to zero when explaining how far the population exceeded its allowance.

### Step 12. Make Coverage and Policy Limitations Visible

**What You Are Doing:** Check retention and collection coverage before making a long-window claim. A successful query for thirty days does not establish that thirty days of data were available.

**Practical Walkthrough:** Inspect actual retention, collection continuity, and available history before making a long-window claim. This lab's three-day retention cannot establish a complete 30-day service record. A query accepting a 30-day range only proves the expression ran over whatever data was available.

Inspect retained history and known collection gaps before making a long-period compliance claim. A thirty-day expression can return a result despite incomplete coverage. With the lab's shorter retention, describe only the history actually available and keep that limitation separate from the arithmetic itself.

Inspect the actual retention flags from Lab 18. A successful `[30d]` query can still return a partial result when only three days are retained. HTTP success and a plausible percentage do not prove complete coverage.

A production reporting system needs sufficient retained data, monitored collection/rule gaps, a declared boundary and an agreed missing-data policy. Do not lengthen retention casually on a modest VM without capacity planning. This lab deliberately leaves retention unchanged.

The simple budget policy prioritizes investigation and reliability work when consumption is high or exhausted. It does not automatically ban every deployment; fixes, security changes and risk decisions need ownership. Lab 26 will add burn-rate alerting over multiple windows using these definitions.

**Understanding the Result:** Query range and evidence coverage are different. State the measured coverage limit beside any SLO conclusion.

### Step 13. Recovery and Troubleshooting

**What You Are Doing:** Restore the normal stage and verify the policy's expected distinctions. Diagnose different counts through their defined eligibility before changing a denominator to make them agree.

**Practical Walkthrough:** Restore normal services and verify the policy-specific outputs using their documented eligibility. Preserve the SLI definitions and tests for the burn-rate lab. If availability and latency counts differ, reconcile the included response classes before changing any expression.

Verify restored dependencies, inactive fault alerts, and fresh policy-specific output. Compare availability and latency populations using their separate contracts before treating a count difference as a bug. Keep the policy document and deterministic tests because the next lab uses these same definitions for burn rate.

```bash
source lab-notes/alert-session.sh
wait_rule_state PostgresUnavailable inactive
api -fsS "$PROM_URL/api/v1/rules?type=record" > "$LAB_DIR/final-recording-rules.json"
api -fsS "$APP_URL/health/ready" > "$LAB_DIR/final-readiness.json"
metrics_check
```

| **Symptom**                            | **Inspect**                                          | **Corrective Action**                                        |
| -------------------------------------- | ---------------------------------------------------- | ------------------------------------------------------------ |
| Availability and latency counts differ | Defined eligibility                                  | Expected for 4xx; do not silently change denominators        |
| Slow demo leaves SLI unchanged         | Items-only route scope                               | Expected; compare the broad RED panel                        |
| Latency appears good during 503s       | Separate status/latency populations                  | Evaluate availability as well; fast failure is still failure |
| Empty SLI                              | Zero traffic, missing series or insufficient history | Distinguish these cases before claiming success              |
| Window totals differ from ledger       | Extrapolation and earlier traffic                    | Use exact raw deltas for isolated population proof           |
| Budget remaining is negative           | Consumption exceeds allowance                        | Preserve the breach and explain its window                   |
| 30-day result from three-day storage   | Incomplete history                                   | Mark coverage insufficient rather than claiming compliance   |

Keep PostgreSQL healthy, retain the six new recording rules and preserve the policy/fixture evidence. There is no new notification policy in this lab.

**Understanding the Result:** Finish with a healthy current stage and an unchanged policy contract. Historical budget consumption remains part of the measured interval.

## 4. Troubleshooting and Diagnosis

Start with the first unexpected result. Record the exact symptom, identify which component produced it, and use the checks below before repeating the experiment. After a fix, repeat the original check and confirm recovery.

Use the recovery and troubleshooting checks in Step 13.

## 5. Review and Practice

Answer the knowledge questions in your own words before reading the answer guide. For the scenario, name the evidence you would collect and the conclusion each observation would support.

### Knowledge Check

1. Why can a fast 503 satisfy the latency threshold?
2. Why exclude demo calls here?
3. Why is a 30-day query not proof of 30-day coverage?
4. Why not average successive success percentages?

#### Answer Guide

1. Latency and availability measure different populations/outcomes; both must be evaluated.
2. The contract covers Items user functionality, not diagnostic work.
3. The backend can return the partial history it actually has.
4. Different intervals have different event counts; aggregate good and total events before dividing.

### Professional Scenario Exercise

A report says 100% availability during an hour with no requests and a scrape gap. Rewrite the conclusion with traffic and coverage qualifications, then specify what observation would support a defensible user-facing claim.

## 6. Completion Checklist

Mark each item only after you can point to its result or evidence file. Complete any cleanup shown here before moving on.

### Observable Completion Criteria

- [ ] Availability and latency populations are explicitly different and measurable.
- [ ] Six SLI recordings and zero-traffic fixtures validate.
- [ ] The real client ledger matches raw metric deltas.
- [ ] Threshold latency uses the actual 0.25-second bucket.
- [ ] Budget math handles exhaustion and undefined traffic honestly.
- [ ] Short-window evidence is not mislabeled as 30-day compliance.

## 7. Production Context and Next Lab

### Production Implications

SLOs are agreements about user outcomes, not decorative dashboard thresholds. Revisit eligibility, coverage, low-volume behavior, telemetry failures and ownership before production adoption. Keep request-based and time-based budgets distinct.

### End State and Transition

Keep nine services, six scrape jobs, twelve recording rules and the five operational alerts. [Lab 26](Lab-26.md) uses the defined SLIs for multi-window burn rates and an SLO operations dashboard; it is not implemented in this batch.
