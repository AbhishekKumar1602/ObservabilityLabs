# Lab 26: Multi-Window Burn Rates and the SLO Operations Dashboard

## 1. Purpose and Learning Outcomes

**In Plain Language:** You will convert the SLO definitions into burn-rate observations and alerts. Burn rate compares the current bad-event fraction with the fraction allowed by the objective. Pairing long and short windows tests both sustained impact and whether it is still happening; deterministic fixtures let you test hours of policy behavior without running an outage lasting hours.

> **Primary Objective:** Detect fast and slow budget consumption using Lab 25’s SLI contracts, test the actual alert expressions and provision an operational SLO dashboard.

An objective tells you what good service means. A burn rate tells you how quickly observed bad service is consuming its allowance. This lab adds eight recording rules, two alert definitions, deterministic fixtures and a 16-panel dashboard.

The local three-day retention still cannot prove a complete 30-day objective. No Loki, traces, profiling or external notification integration starts here. The controlled alert experiment uses virtual time rather than hours of real dependency downtime.

## 2. Starting Point and Lab Map

Use this guide inside the complete application repository. Run commands from the repository root in Bash unless a step says otherwise. Work through the starting checks in order; they load the helpers and state this lab needs.

Read the explanation before each step, run one complete code block at a time, and compare the actual result with the stated expectation. Save commands, observations, explanations, and unexpected results in your notebook.

### Key Terms

| **Term**               | **Plain-Language Meaning**                                                      |
| ---------------------- | ------------------------------------------------------------------------------- |
| Burn rate              | Observed bad-event fraction divided by the fraction allowed by the SLO.         |
| Multi-window condition | Requiring both a longer and a shorter observation window to exceed a threshold. |
| Join labels            | The dimensions that must match when pairing results from the two windows.       |

### Lab Map

The arrows show the flow or dependency being studied. Branches identify paths to compare; this is a conceptual map, not a captured runtime trace.

```mermaid
flowchart TD
    A["FastAPI counters and histogram"] --> P["Prometheus scrape"]
    P --> R["SLI and burn recordings"]
    R --> E["Multi-window evaluation"]
    E --> M["Alertmanager"]
    R --> G["SLO dashboard"]
    P --> G
```

## 3. Guided Walkthrough

### Step 01. Inherited State and Starting Checks

**What You Are Doing:** Verify the healthy alerting and SLI stage before adding burn calculations. Preserve the existing contracts so a new alert policy does not quietly redefine good service.

**Practical Walkthrough:** Verify current SLI recordings, alert rules, and notification health before adding burn calculations. Keep the availability and latency policies from Lab 25 unchanged. Burn rate measures consumption relative to those existing allowances; it should not introduce a different hidden definition of bad events.

Confirm SLI recordings and alert delivery are healthy before adding more calculations. Preserve the existing good-event and eligibility definitions. Burn rate changes the interpretation relative to an allowance; it must not silently change which requests count as eligible or bad.

```bash
source lab-notes/session.sh
source lab-notes/evidence.sh
source lab-notes/raw-metrics.sh
source lab-notes/metrics-session.sh
metrics_check
start_lab 26
```

Complete [Lab 25](Lab-25.md). Use the same Linux Docker host, Bash session and repository root. Keep credentials, named volumes and the checkpoint item. Required host tools remain Docker Compose, Python 3, curl, jq, Git and ripgrep. Stop at a failed check and resolve it before proceeding. Nine services and six scrape jobs remain active.

**Understanding the Result:** A policy change and an alerting change are different actions. This lab changes the latter while preserving the former.

### Step 02. Learning Objectives and Evidence Boundaries

**What You Are Doing:** Follow measurements through SLI recordings, burn calculations, and alert evaluation. Each stage needs fresh source evidence and the same intended request population.

**Practical Walkthrough:** Follow request observations through SLI ratios, burn recordings, alert joins, and notifications. Each layer depends on fresh, correctly scoped inputs from the previous one. When a burn panel is empty, check traffic and rule health before assuming the service is consuming no budget.

Trace an empty or unexpected result through source traffic, SLI ratio, burn recording, alert matching, and notification handling. Inspect each intermediate value with its labels. This localizes the failure rather than treating an absent final panel as proof of zero budget consumption.

You will preserve an SLI population, calculate dimensionless burn, join long/short windows correctly, test recovery and missing data, control duplicate notifications and investigate ratios alongside traffic and collection health.

The lab map in Section 2 shows this relationship.

Metrics still enter Prometheus only through `/metrics`. Grafana displays queries; Prometheus owns rule state. A request failure, a changed rule, a firing alert and a delivered notification are separate events.

**Understanding the Result:** Absence can originate upstream of the burn expression. Preserve source-health context during diagnosis.

### Step 03. Preserve the Two Contracts and Save a Checkpoint

**What You Are Doing:** Save the current configuration and restate each SLI's allowance. Those fractions are the divisors that turn a bad-event ratio into a dimensionless burn rate.

**Practical Walkthrough:** Save the working configuration and restate each policy's allowed bad fraction. Dividing the observed bad fraction by that allowance produces burn rate: one means consumption at the budget's target pace. Keep the two SLI identities distinct because their allowances and eligible populations differ.

Save the approved configuration before installing burn rules. Calculate the allowed bad fraction separately for each SLI, then divide observed bad fraction by that allowance. Keep service, environment, and SLI identities attached so different budgets cannot be combined merely because their resulting numbers share a unit.

```bash
source lab-notes/alertmanager-session.sh
test -f lab-notes/slo-policy.md
test -f lab-notes/prometheus/slo-recording-rules.yml
test -f config/grafana/learning/dashboards/lab20-red.json
cp lab-notes/prometheus/prometheus.yml "$LAB_DIR/prometheus-before.yml"
cp lab-notes/alertmanager/alertmanager.yml "$LAB_DIR/alertmanager-before.yml"
api -fsS "$PROM_URL/api/v1/rules" > "$LAB_DIR/rules-before.json"
```

| **SLI**                    | **Eligible Population**                      | **Bad Events**        | **Allowance** |
| -------------------------- | -------------------------------------------- | --------------------- | ------------: |
| Availability, 99.5%        | Completed Items 2xx, 3xx, 5xx                | 5xx                   | 0.005         |
| Latency, 99% within 250 ms | All completed Items responses, including 4xx | Duration above 0.25 s | 0.01          |

The histogram has no status dimension. A fast 503 can meet latency and fail availability. Health, metrics, demo and unmatched routes remain excluded. Do not silently change denominators to make a dashboard look better.

For window W, `burn(W) = bad_fraction(W) / allowed_bad_fraction`. Availability failures of 8% yield `0.08/0.005 = 16`; 2% slow responses yield `0.02/0.01 = 2`. Burn is neither requests/second nor an exact remaining monthly budget.

**Understanding the Result:** Burn rate is dimensionless. Its numeric value depends on the specific SLI allowance used as the divisor.

### Step 04. Choose and Predict the Multi-Window Policy

**What You Are Doing:** Predict how the long and short windows work together. The long window describes sustained impact while the short window tests whether high consumption is continuing.

**Practical Walkthrough:** Read each long-and-short window pair together: 1 hour and 5 minutes at 14.4, and 6 hours and 30 minutes at 6. The longer window requires sustained impact while the shorter one checks continuing activity. Both conditions must match the intended SLI identity.

Treat each pair as two conditions on the same SLI: a longer sustained-impact window and a shorter continuing-impact window. Read both thresholds and ranges before predicting firing. A high value on only one side should not be mistaken for satisfaction of the paired-window policy.

| **Alert**        | **Long Window** | **Short Window** | **Both Burns Exceed** | **Local Severity** |
| ---------------- | --------------- | ---------------- | --------------------: | ------------------ |
| ItemsSLOBurnFast | 1 h             | 5 min            | 14.4                  | critical           |
| ItemsSLOBurnSlow | 6 h             | 30 min           | 6                     | warning            |

The long window measures sustained impact; the short window confirms that consumption remains elevated. Use AND within a pair. Each alert evaluates availability and latency separately through the bounded `sli` label.

For a 30-day reference period, `14.4×1/720=2%` and `6×6/720=5%`. These calibrations assume a reference traffic model. They do not measure an exact fraction of a real monthly request budget when traffic varies. The [SRE workbook](https://sre.google/workbook/alerting-on-slos/) explains the paired-window approach. This local policy assigns the slower tier warning; production urgency requires an explicit owner and response agreement.

There is no additional `for`: the windows qualify the signal. A long hold timer is not a substitute for an event-weighted long-window ratio. Zero traffic is undefined; very low traffic can produce a large burn from one failure. Record your expected alert state for a short spike, an old recovered incident and both windows high before running the tests.

**Understanding the Result:** A recent recovery can clear the short condition before the long history disappears. That is part of the paired-window design.

### Step 05. Install the Complete Burn Recordings

**What You Are Doing:** Install the complete set of windowed recordings. Calculate longer-window ratios from their event populations instead of averaging percentages from differently sized traffic periods.

**Practical Walkthrough:** Install the complete recording set and calculate each window from its eligible and bad event populations. Do not average short-period percentages to obtain a longer-period ratio, because intervals with different traffic volumes need different weights. Preserve the window label for inspection of intermediate results.

Inspect the eligible and bad populations for every range and preserve the window label on intermediate recordings. Recompute ratios from those populations for each window. Averaging shorter ratios would weight quiet and busy intervals equally, which would not represent the combined request population correctly.

```bash
cat > lab-notes/prometheus/slo-burn-recording.yml <<'YAML'
groups:
- name: items-slo-burn
  interval: 15s
  rules:
  - record: service:slo_items:burnrate
    expr: service:slo_items_availability_error:ratio5m / 0.005
    labels:
      window: 5m
      sli: availability
  - record: service:slo_items:burnrate
    expr: service:slo_items_latency_error:ratio5m / 0.01
    labels:
      window: 5m
      sli: latency
  - record: service:slo_items:burnrate
    expr: ((sum by (environment, service) (rate(application_http_server_errors_total{job="fastapi",route=~"/api/v1/items(/\\{item_id\\})?"}[30m]))
      / sum by (environment, service) (rate(application_http_requests_total{job="fastapi",route=~"/api/v1/items(/\\{item_id\\})?",status_code=~"2..|3..|5.."}[30m])))
      / 0.005) and on (environment, service) (sum by (environment, service) (rate(application_http_requests_total{job="fastapi",route=~"/api/v1/items(/\\{item_id\\})?",status_code=~"2..|3..|5.."}[30m]))
      > 0)
    labels:
      window: 30m
      sli: availability
  - record: service:slo_items:burnrate
    expr: (((sum by (environment, service) (rate(application_http_request_duration_seconds_count{job="fastapi",route=~"/api/v1/items(/\\{item_id\\})?"}[30m]))
      - sum by (environment, service) (rate(application_http_request_duration_seconds_bucket{job="fastapi",route=~"/api/v1/items(/\\{item_id\\})?",le="0.25"}[30m])))
      / sum by (environment, service) (rate(application_http_request_duration_seconds_count{job="fastapi",route=~"/api/v1/items(/\\{item_id\\})?"}[30m])))
      / 0.01) and on (environment, service) (sum by (environment, service) (rate(application_http_request_duration_seconds_count{job="fastapi",route=~"/api/v1/items(/\\{item_id\\})?"}[30m]))
      > 0)
    labels:
      window: 30m
      sli: latency
  - record: service:slo_items:burnrate
    expr: ((sum by (environment, service) (rate(application_http_server_errors_total{job="fastapi",route=~"/api/v1/items(/\\{item_id\\})?"}[1h]))
      / sum by (environment, service) (rate(application_http_requests_total{job="fastapi",route=~"/api/v1/items(/\\{item_id\\})?",status_code=~"2..|3..|5.."}[1h])))
      / 0.005) and on (environment, service) (sum by (environment, service) (rate(application_http_requests_total{job="fastapi",route=~"/api/v1/items(/\\{item_id\\})?",status_code=~"2..|3..|5.."}[1h]))
      > 0)
    labels:
      window: 1h
      sli: availability
  - record: service:slo_items:burnrate
    expr: (((sum by (environment, service) (rate(application_http_request_duration_seconds_count{job="fastapi",route=~"/api/v1/items(/\\{item_id\\})?"}[1h]))
      - sum by (environment, service) (rate(application_http_request_duration_seconds_bucket{job="fastapi",route=~"/api/v1/items(/\\{item_id\\})?",le="0.25"}[1h])))
      / sum by (environment, service) (rate(application_http_request_duration_seconds_count{job="fastapi",route=~"/api/v1/items(/\\{item_id\\})?"}[1h])))
      / 0.01) and on (environment, service) (sum by (environment, service) (rate(application_http_request_duration_seconds_count{job="fastapi",route=~"/api/v1/items(/\\{item_id\\})?"}[1h]))
      > 0)
    labels:
      window: 1h
      sli: latency
  - record: service:slo_items:burnrate
    expr: ((sum by (environment, service) (rate(application_http_server_errors_total{job="fastapi",route=~"/api/v1/items(/\\{item_id\\})?"}[6h]))
      / sum by (environment, service) (rate(application_http_requests_total{job="fastapi",route=~"/api/v1/items(/\\{item_id\\})?",status_code=~"2..|3..|5.."}[6h])))
      / 0.005) and on (environment, service) (sum by (environment, service) (rate(application_http_requests_total{job="fastapi",route=~"/api/v1/items(/\\{item_id\\})?",status_code=~"2..|3..|5.."}[6h]))
      > 0)
    labels:
      window: 6h
      sli: availability
  - record: service:slo_items:burnrate
    expr: (((sum by (environment, service) (rate(application_http_request_duration_seconds_count{job="fastapi",route=~"/api/v1/items(/\\{item_id\\})?"}[6h]))
      - sum by (environment, service) (rate(application_http_request_duration_seconds_bucket{job="fastapi",route=~"/api/v1/items(/\\{item_id\\})?",le="0.25"}[6h])))
      / sum by (environment, service) (rate(application_http_request_duration_seconds_count{job="fastapi",route=~"/api/v1/items(/\\{item_id\\})?"}[6h])))
      / 0.01) and on (environment, service) (sum by (environment, service) (rate(application_http_request_duration_seconds_count{job="fastapi",route=~"/api/v1/items(/\\{item_id\\})?"}[6h]))
      > 0)
    labels:
      window: 6h
      sli: latency
YAML
```

**Command Note:** `<<'YAML'` writes the following block literally until `YAML`. Quoting the delimiter prevents Bash from expanding `$variables` inside the generated file; creation and execution are separate steps.

These eight rules share one family and bounded `window`/`sli` labels. Five-minute values reuse Lab 25 recordings. Longer windows compute reset-aware rates from raw counters before aggregation. Averaging successive percentages would weight quiet and busy periods incorrectly.

Separate rule groups may observe a one-evaluation delay. The offline fixture specifies group order where exact timing matters.

**Understanding the Result:** Long-window ratios must reflect event weighting. An unweighted average can misrepresent busy and quiet periods.

### Step 06. Install the Complete Alert Rules

**What You Are Doing:** Install the paired-window alert expressions and inspect their join labels. Remove the window dimension for matching while retaining service, environment, and SLI identity.

**Practical Walkthrough:** Install the paired-window expressions and inspect their vector matching. Remove the differing window dimension for the join while retaining service, environment, and SLI identity. Evaluate each side separately if the join is empty despite apparently high values.

Evaluate the long-window and short-window operands separately before inspecting their join. Compare retained identity labels and the deliberately excluded window dimension. If the paired expression is empty, investigate matching and source availability instead of lowering thresholds to force an alert.

```bash
cat > lab-notes/prometheus/slo-burn-alerts.yml <<'YAML'
groups:
- name: items-slo-burn-alerts
  interval: 15s
  rules:
  - alert: ItemsSLOBurnFast
    expr: (max by (environment, service, sli) (service:slo_items:burnrate{window="1h"}) > 14.4) and on (environment,
      service, sli) (max by (environment, service, sli) (service:slo_items:burnrate{window="5m"}) > 14.4)
    labels:
      severity: critical
      team: platform
    annotations:
      summary: Items SLO fast budget burn
      description: Both 1h and 5m burn exceed 14.4x. SLI={{ $labels.sli }}, service={{ $labels.service }}, environment={{
        $labels.environment }}. Check traffic and coverage.
      runbook: labs/Lab-26.md
  - alert: ItemsSLOBurnSlow
    expr: (max by (environment, service, sli) (service:slo_items:burnrate{window="6h"}) > 6) and on (environment,
      service, sli) (max by (environment, service, sli) (service:slo_items:burnrate{window="30m"}) > 6)
    labels:
      severity: warning
      team: platform
    annotations:
      summary: Items SLO slow budget burn
      description: Both 6h and 30m burn exceed 6x. SLI={{ $labels.sli }}, service={{ $labels.service }}, environment={{
        $labels.environment }}. Check traffic and coverage.
      runbook: labs/Lab-26.md
YAML
```

```bash
python3 - <<'PYTHON'
from pathlib import Path
p=Path("lab-notes/prometheus/prometheus.yml");t=p.read_text()
anchor="- /etc/prometheus/labs/slo-recording-rules.yml\n"
addition="- /etc/prometheus/labs/slo-burn-recording.yml\n- /etc/prometheus/labs/slo-burn-alerts.yml\n"
if addition not in t:
    assert t.count(anchor)==1,"Expected the completed Lab 25 configuration"
    t=t.replace(anchor,anchor+addition)
p.write_text(t)
PYTHON
chmod 644 lab-notes/prometheus/slo-burn-recording.yml lab-notes/prometheus/slo-burn-alerts.yml
```

The `max by` removes `window` before matching. Retaining it in a default join prevents `1h` from matching `5m`; discarding `sli` could join unrelated objectives. Service and environment must also match. No unbounded request/event IDs appear in rule labels.

**Understanding the Result:** The two windows must refer to the same policy population. A label mismatch can suppress a valid comparison without a syntax error.

### Step 07. Create and Run the Deterministic Tests

**What You Are Doing:** Run fixtures that exercise the real recording and alert expressions over virtual time. Include recovery, missing input, and window matching so the tests cover more than one high value.

**Practical Walkthrough:** Run the fixtures over virtual time, including the six-hour cases, instead of waiting for a real six-hour outage. They exercise the actual recording and alert expressions with controlled inputs. Inspect recovery, missing data, and matching cases as well as high-burn scenarios.

Run virtual-time fixtures for the actual recording and alert expressions, including recovery and missing-input cases. Read expected labels as well as values. These tests make long-window behavior reviewable without creating a real multi-hour outage or waiting for the live environment to accumulate the fixture's history.

```bash
cat > lab-notes/build_burn_tests.py <<'PYTHON'
"""JSON-formatted YAML fixtures; Python standard library only."""
import json
from pathlib import Path
out=Path("lab-notes/prometheus")
def gauge(w,value,sli="availability",service="items-info"):
    return {"series":f'service:slo_items:burnrate{{environment="local",service="{service}",sli="{sli}",window="{w}"}}',"values":value}
def expected(fast,sli):
    long,short,threshold=("1h","5m","14.4") if fast else ("6h","30m","6")
    return {"exp_labels":{"environment":"local","service":"items-info","sli":sli,"team":"platform","severity":"critical" if fast else "warning"},"exp_annotations":{"summary":f'Items SLO {"fast" if fast else "slow"} budget burn',"description":f"Both {long} and {short} burn exceed {threshold}x. SLI={sli}, service=items-info, environment=local. Check traffic and coverage.","runbook":"labs/Lab-26.md"}}
tests=[]
def case(name,series,fast=False,slow=False,sli="availability"):
    tests.append({"name":name,"interval":"15s","input_series":series,"alert_rule_test":[{"eval_time":"1m","alertname":"ItemsSLOBurnFast" if kind else "ItemsSLOBurnSlow","exp_alerts":[expected(kind,sli)] if active else []} for kind,active in [(True,fast),(False,slow)]]})
case("healthy",[gauge(w,"0+0x4") for w in ["5m","30m","1h","6h"]])
case("short spike only",[gauge("5m","20+0x4"),gauge("1h","2+0x4")])
case("old incident only",[gauge("5m","0+0x4"),gauge("1h","20+0x4")])
case("fast confirmed",[gauge("5m","20+0x4"),gauge("1h","20+0x4")],fast=True)
case("slow confirmed",[gauge("30m","8+0x4"),gauge("6h","8+0x4")],slow=True)
case("both",[gauge(w,"20+0x4") for w in ["5m","30m","1h","6h"]],fast=True,slow=True)
case("recovery",[gauge("5m","20 20 0 0 0"),gauge("1h","20+0x4")])
case("strict threshold",[gauge("5m","14.4+0x4"),gauge("1h","14.4+0x4")])
case("different service",[gauge("5m","20+0x4",service="other-items"),gauge("1h","20+0x4")])
case("latency identity",[gauge("5m","20+0x4",sli="latency"),gauge("1h","20+0x4",sli="latency")],fast=True,sli="latency")
case("missing data",[])
(out/"burn-alert-tests.yml").write_text(json.dumps({"rule_files":["slo-burn-alerts.yml"],"evaluation_interval":"15s","tests":tests},indent=2)+"\n")
scope='environment="local",service="items-info"'
def counter(metric,extra,step):
    return {"series":f'{metric}{{job="fastapi",{scope},route="/api/v1/items",method="GET"{extra}}}',"values":f"0+{step}x1440"}
inputs=[counter("application_http_requests_total",',status_code="200"',92),counter("application_http_requests_total",',status_code="503"',8),counter("application_http_server_errors_total","",8),counter("application_http_request_duration_seconds_count","",100),counter("application_http_request_duration_seconds_bucket",',le="0.25"',98)]
checks=[{"expr":f'round(service:slo_items:burnrate{{window="{w}",sli="{sli}"}},0.000001)',"eval_time":"6h","exp_samples":[{"labels":f'{{{scope},sli="{sli}",window="{w}"}}',"value":burn}]} for w in ["5m","30m","1h","6h"] for sli,burn in [("availability",16),("latency",2)]]
suite={"rule_files":["slo-recording-rules.yml","slo-burn-recording.yml"],"evaluation_interval":"15s","fuzzy_compare":True,"group_eval_order":["items-sli-learning","items-slo-burn"],"tests":[{"name":"raw counter contract","interval":"15s","input_series":inputs,"promql_expr_test":checks},{"name":"idle is undefined","interval":"15s","input_series":[{**s,"values":"0+0x1440"} for s in inputs],"promql_expr_test":[{"expr":"service:slo_items:burnrate","eval_time":"6h","exp_samples":[]}]}]}
(out/"burn-recording-tests.yml").write_text(json.dumps(suite,indent=2)+"\n")
print("Wrote both burn rule test suites")
PYTHON
```

```bash
python3 lab-notes/build_burn_tests.py
chmod 644 lab-notes/prometheus/burn-alert-tests.yml lab-notes/prometheus/burn-recording-tests.yml
dm run --rm -T --no-deps --entrypoint promtool prometheus test rules \
  /etc/prometheus/labs/slo-tests.yml \
  /etc/prometheus/labs/burn-recording-tests.yml \
  /etc/prometheus/labs/burn-alert-tests.yml > "$LAB_DIR/burn-tests.txt" 2>&1
cat "$LAB_DIR/burn-tests.txt"
```

**Expected Result:** all suites report SUCCESS. JSON syntax is valid YAML, so the generator needs only standard Python. The recording suite advances six hours of virtual time through real raw-counter expressions. The alert suite isolates threshold and join behavior using recording inputs.

| **Case**                     | **Expected**                        |
| ---------------------------- | ----------------------------------- |
| Short high, long low         | No fast alert                       |
| Long high, short recovered   | No fast alert                       |
| Both fast windows high       | Fast alert                          |
| Both slow windows high       | Slow alert                          |
| Both pairs high              | Both alert definitions fire         |
| Exactly 14.4                 | No fast alert: comparison is strict |
| Different service identities | No cross-service join               |
| Idle population              | Burn absent, no claim of success    |

The raw fixture yields availability burn 16 and latency burn 2 in all windows. These are semantic tests, not proof of live scraping, dashboard rendering or external delivery.

**Understanding the Result:** Virtual-time fixtures establish rule behavior efficiently. They do not create six hours of live operational evidence.

### Step 08. Prove a Wrong Threshold Fails the Tests

**What You Are Doing:** Prove that an incorrect threshold produces an assertion failure. The broken candidate remains outside the running configuration, so this checks test sensitivity safely.

**Practical Walkthrough:** Run the isolated candidate with the deliberately incorrect threshold and confirm the expected assertion fails. Verify the fixture parsed and reached the relevant calculation. Keep the broken threshold outside the running rule set so test sensitivity is demonstrated without changing live policy.

Keep the wrong-threshold candidate isolated from the active rules. Confirm the test parsed and failed the intended behavioral assertion. That failure demonstrates sensitivity to policy changes; a syntax error or missing input file would not establish that the threshold test detects incorrect burn behavior.

```bash
python3 - <<'PYTHON'
import json
from pathlib import Path
p=Path("lab-notes/prometheus")
rule=(p/"slo-burn-alerts.yml").read_text();assert "> 14.4" in rule
(p/"burn-broken.yml").write_text(rule.replace("> 14.4","> 100"))
test=json.loads((p/"burn-alert-tests.yml").read_text());test["rule_files"]=["burn-broken.yml"]
(p/"burn-negative-tests.yml").write_text(json.dumps(test,indent=2)+"\n")
PYTHON
chmod 644 lab-notes/prometheus/burn-broken.yml lab-notes/prometheus/burn-negative-tests.yml
if dm run --rm -T --no-deps --entrypoint promtool prometheus test rules \
  /etc/prometheus/labs/burn-negative-tests.yml > "$LAB_DIR/expected-failure.txt" 2>&1; then
  echo 'Unexpected pass; inspect the negative control' >&2
  false
else
  cat "$LAB_DIR/expected-failure.txt"
fi
rm lab-notes/prometheus/burn-broken.yml lab-notes/prometheus/burn-negative-tests.yml
```

Confirm that missing expected alerts cause the failure; a syntax error is not the intended result. The broken file is never referenced by the running server. This keeps the experiment bounded and the application available.

**Understanding the Result:** Failure for the intended numeric reason shows the test can catch that defect. An unrelated syntax error would not prove it.

### Step 09. Apply Notification Control and Load the Rules

**What You Are Doing:** Apply the notification policy and load the rules, then inspect actual runtime state. Suppression of duplicate notifications does not change which rule conditions are firing.

**Practical Walkthrough:** Apply the validated notification configuration and load the rules, then inspect current firing and suppression state separately. Notification deduplication or inhibition can reduce messages while the underlying rules remain firing. Verify loaded configuration and actual rule health after the reload.

Verify accepted configuration and subsequent rule evaluations after loading. Inspect firing state separately from notification suppression or deduplication. Fewer messages can result from notification control while the source rules remain true, so message count alone cannot establish alert resolution.

```bash
python3 - <<'PYTHON'
from pathlib import Path
p=Path("lab-notes/alertmanager/alertmanager.yml");t=p.read_text()
addition="""  - source_matchers: ['alertname="ItemsSLOBurnFast"']
    target_matchers: ['alertname="ItemsSLOBurnSlow"']
    equal: [service, environment, sli]
"""
if addition not in t:
    assert t.count("templates:\n")==1 and "inhibit_rules:" in t
    t=t.replace("templates:\n",addition+"templates:\n")
p.write_text(t)
PYTHON
am check-config /etc/alertmanager/alertmanager.yml
dm kill -s HUP alertmanager
record_change 'activate paired-window Items burn rules' planned
reload_prometheus
wait_prometheus
api -fsS "$PROM_URL/api/v1/rules" > "$LAB_DIR/rules-after.json"
jq -e '[.data.groups[].rules[]|select(.health!="ok")]|length==0' "$LAB_DIR/rules-after.json"
record_change 'activate paired-window Items burn rules' completed
```

Repeat the API check after an evaluation cycle if the new groups are not yet visible. Expect 20 recording rules and seven alert definitions, including the earlier five operational alerts. Alert instances can be more numerous because SLIs/services differ.

Inhibition matches service, environment and SLI: a fast availability burn must not suppress slow latency evidence. It controls notifications, not Prometheus state. Existing local receivers route without real external credentials.

**Understanding the Result:** Notification policy controls delivery behavior. It does not change measured burn or make the source condition false.

### Step 10. Build the SLO Operations Dashboard

**What You Are Doing:** Provision panels that show burn beside traffic and collection context. A high ratio or absent curve needs supporting volume and source-health information to be interpreted correctly.

**Practical Walkthrough:** Provision the burn panels with traffic volume and collection context nearby. A large ratio based on sparse activity and a missing curve from absent input need different interpretations. Confirm panel labels identify the SLI and window so different policies are not visually combined.

Check that panels display SLI identity, window, burn rate, and traffic context together. Inspect missing-data behavior and units before trusting the visual summary. Sparse activity and unavailable inputs can produce very different operational interpretations even when neither appears as a large sustained curve.

```bash
cat > lab-notes/build_slo_dashboard.py <<'PYTHON'
import json
from pathlib import Path
from dashboard_factory import panel,save
s='environment=~"${environment:regex}",service=~"${service:regex}"'
routes=',route=~'+json.dumps(r'/api/v1/items(/\{item_id\})?')
def count(metric,extra=""):
    return f'sum by (environment, service) (increase({metric}{{job="fastapi",{s}{routes}{extra}}}[1h]))'
eligible=count("application_http_requests_total",',status_code=~"2..|3..|5.."')
bad=count("application_http_server_errors_total")
total=count("application_http_request_duration_seconds_count")
good=count("application_http_request_duration_seconds_bucket",',le="0.25"')
a=f'({bad} / {eligible})';lat=f'(({total} - {good}) / {total})'
panels=[]
def add(title,queries,unit,description,kind="timeseries"):
    panels.append(panel(len(panels)+1,title,queries,unit,description,kind))
add("Proposed availability objective",[("vector(0.995)","Target")],"percentunit","Configured 30-day policy target, not measured compliance.","stat")
add("Proposed latency objective",[("vector(0.99)","Target")],"percentunit","99% within 250ms, all completed Items responses.","stat")
add("Availability — observed 1h",[(f'(1-{a}) and on (environment,service) ({eligible}>0)',"Availability")],"percentunit","Uses available history and the 2xx/3xx/5xx population.","stat")
add("Latency — observed 1h",[(f'(1-{lat}) and on (environment,service) ({total}>0)',"Within 250ms")],"percentunit","All completed Items responses, including 4xx.","stat")
add("Availability budget headroom — observed 1h",[(f'(1-{a}/0.005) and on (environment,service) ({eligible}>0)',"Remaining fraction")],"none","Normalized to observed 1h population, not the monthly budget; preserve negative values.","stat")
add("Latency budget headroom — observed 1h",[(f'(1-{lat}/0.01) and on (environment,service) ({total}>0)',"Remaining fraction")],"none","1 full; 0 exhausted; negative overspent. Not 30-day compliance.","stat")
for sli in ["availability","latency"]:
    add(sli.title()+" burn by window",[(f'service:slo_items:burnrate{{{s},sli="{sli}"}}',"{{window}}")],"none","Compare the long and short windows together; burn is dimensionless.")
add("Eligible traffic — 5m rate",[(f'service:slo_items_eligible_requests:rate5m{{{s}}}',"Availability"),(f'service:slo_items_latency_observations:rate5m{{{s}}}',"Latency")],"reqps","Different populations are intentional; missing is not zero.")
add("Availability event estimates — 1h",[(eligible,"Eligible"),(bad,"Bad")],"short","Extrapolated estimates, not a transaction ledger.")
add("Application dependencies",[(f'application_dependency_up{{job="fastapi",{s}}}',"{{dependency}}")],"none","Latest app probe: 1 healthy, 0 unavailable, absent unknown.")
add("Server dependencies",[('pg_up{job="postgres"}',"Postgres"),('redis_up{job="redis"}',"Redis")],"none","Single configured database/cache servers; app variables do not select new servers.")
add("Successful stored scrapes — 1h",[(f'avg by (environment,service) (avg_over_time(up{{job="fastapi",{s}}}[1h]))',"Success fraction")],"percentunit","Fraction of available samples, not proof of full coverage.")
add("Stored scrape coverage estimate — 1h",[(f'clamp_max(avg by (environment,service) (count_over_time(up{{job="fastapi",{s}}}[1h]))/240,1)',"Stored / expected")],"percentunit","About 240 samples at 15s cadence. up=0 still counts; this is only a heuristic.")
add("Active SLO alerts",[(f'ALERTS{{{s},alertname=~"ItemsSLOBurnFast|ItemsSLOBurnSlow"}}',"{{alertname}} {{sli}} {{alertstate}}")],"none","Empty may mean no active alerts or absent evaluation data.")
add("Rule evaluation failures",[('sum(rate(prometheus_rule_evaluation_failures_total{job="prometheus"}[5m]))',"Failures/s")],"ops","Global evaluation health; identify the affected group through the rules API.")
out=Path("config/grafana/learning/dashboards/lab26-slo.json")
save(out,"lab26-slo","Lab 26 — Items SLO operations",panels)
d=json.loads(out.read_text());d["templating"]=json.loads(Path("config/grafana/learning/dashboards/lab20-red.json").read_text())["templating"]
d["description"]="Three-day retention cannot prove 30-day compliance. Operational windows may have partial history; inspect coverage and traffic."
d["links"]=[{"title":title,"type":"link","url":"/d/"+uid,"includeVars":True,"keepTime":True,"targetBlank":False} for uid,title in [("lab20-red","Application RED"),("lab21-use","Host and dependencies")]]
out.write_text(json.dumps(d,indent=2)+"\n")
PYTHON
```

```bash
python3 lab-notes/build_slo_dashboard.py
chmod 644 config/grafana/learning/dashboards/lab26-slo.json
python3 -m json.tool config/grafana/learning/dashboards/lab26-slo.json > /dev/null
wait_grafana
```

The Lab 22 provider watches this directory. After its polling interval, open **Lab 26 — Items SLO operations**, UID `lab26-slo`. It reuses bounded environment/service variables and links to RED and USE dashboards with time/variables preserved.

Target panels are explicitly configured constants. One-hour headroom describes the observed one-hour population, not the rolling monthly budget. Negative values remain visible. Scrape-count coverage is only an estimate: stored `up=0` samples do not provide application counter observations.

**Understanding the Result:** Read burn with its denominator and source health. A standalone ratio cannot explain all operational significance.

### Step 11. Observe the Live System and Capture Evidence

**What You Are Doing:** Observe the live windows without expecting earlier failures to disappear immediately. Record available history rather than assuming a long query range proves complete long-term coverage.

**Practical Walkthrough:** Observe the live windows and record how much history is actually available. Earlier failures remain in long windows after the service recovers, and a requested range can exceed retained history. Compare current short-window behavior with the longer context without claiming full coverage that was not collected.

Record the amount of available history alongside every long-window observation. Compare current short-window behavior with earlier failures still included in longer ranges. Do not imply complete one-hour or six-hour coverage solely because the query accepted that duration when the collected history is shorter.

```bash
for number in {1..40}; do
  api -fsS "$APP_URL/api/v1/items" > /dev/null
  sleep 0.25
done
pq 'service:slo_items:burnrate' > "$LAB_DIR/live-burn.json"
pq 'service:slo_items_eligible_requests:rate5m' > "$LAB_DIR/live-traffic.json"
api -fsS "$APP_URL/health/ready" > "$LAB_DIR/readiness.json"
api -fsS "$PROM_URL/api/v1/alerts" > "$LAB_DIR/prometheus-alerts.json"
api -fsS "$ALERTMANAGER_URL/api/v2/alerts" > "$LAB_DIR/alertmanager-alerts.json"
capture_app_logs
```

Allow two successful scrapes/evaluations when series are newly created. Earlier Lab 25 failures can remain in longer windows. Healthy traffic does not erase history immediately, and a six-hour query can compute from much less than six hours after startup.

Compare both windows of each pair. Correlate the selected SLI, traffic volume, dependency state and collection/rule health before escalating. Save the dashboard UTC range alongside API responses and request logs. Empty data is not automatically zero burn.

**Understanding the Result:** Live evidence has real retention and timing limits. Do not infer a complete long-term SLO record from a successful query alone.

### Step 12. Recovery and Troubleshooting

**What You Are Doing:** Diagnose burn output through source traffic, rule health, and join dimensions. Restore only temporary changes and verify that the intended recording and alert policies remain active.

**Practical Walkthrough:** Trace unexpected burn output back through traffic eligibility, source collection, rule evaluation, and join labels. Restore temporary experiment changes while keeping the intended recordings, alert policy, and dashboards active. Verify fresh healthy business and monitoring checks before closing the lab.

Verify fresh business success and healthy source, recording, and alert evaluations. For an unexpected burn result, inspect eligibility, traffic volume, labels, and range in that order. Preserve the intended policy and dashboards while removing only temporary test candidates or fault inputs.

| **Symptom**             | **Inspect**                       | **Action**                                      |
| ----------------------- | --------------------------------- | ----------------------------------------------- |
| High curves, no alert   | Join labels and strict thresholds | Compare service/environment/SLI and rule health |
| Burn absent             | Traffic and source series         | Distinguish idle from missing collection        |
| High burn after startup | Small population, partial history | Qualify impact using counts and coverage        |
| Slow alert still firing | Alertmanager inhibition           | Suppression does not edit Prometheus state      |
| Dashboard missing       | Provider mount and permissions    | Verify JSON path and Grafana logs               |
| Negative headroom       | Budget consumption                | Preserve the breach and state its window        |

```bash
metrics_check
api -fsS "$PROM_URL/api/v1/rules" > "$LAB_DIR/final-rules.json"
jq -e '[.data.groups[].rules[]|select(.health!="ok")]|length==0' "$LAB_DIR/final-rules.json"
git diff --check
```

Keep the correct rules/dashboard. If configuration rollback is required, restore the two backed-up files, validate them and send HUP. Remove the new dashboard if its recordings are no longer active. Unreferenced rule files do not execute. Do not delete volumes to recover a configuration fault.

**Understanding the Result:** The recovered stage should preserve the new policy. Historical burn values remain valid for their original windows.

## 4. Troubleshooting and Diagnosis

Start with the first unexpected result. Record the exact symptom, identify which component produced it, and use the checks below before repeating the experiment. After a fix, repeat the original check and confirm recovery.

Use the recovery and troubleshooting checks in Step 12.

## 5. Review and Practice

Answer the knowledge questions in your own words before reading the answer guide. For the scenario, name the evidence you would collect and the conclusion each observation would support.

### Knowledge Check

1. Why AND within each pair?
2. Why is a high burn incomplete evidence?
3. Why not replace a long window with for?
4. Why match sli in inhibition?

#### Answer Guide

1. It confirms sustained impact is still active.
2. Traffic may be tiny and history incomplete; unobserved attempts are missing.
3. A timer measures uninterrupted truth, not event-weighted history.
4. Availability and latency describe different outcomes.

### Professional Scenario Exercise

A release has an 18x hourly burn and a 0.4x five-minute burn. Write an update separating historical impact, current recovery, traffic and coverage confidence. Explain how your conclusion changes if recent traffic is absent.

## 6. Completion Checklist

Mark each item only after you can point to its result or evidence file. Complete any cleanup shown here before moving on.

### Observable Completion Criteria

- [ ] Both SLI populations are unchanged.
- [ ] Eight recordings and two alert definitions pass positive and negative tests.
- [ ] Window joins preserve service/environment/SLI identity.
- [ ] Notification inhibition cannot cross SLIs.
- [ ] All 16 panels have real sources and honest coverage labels.
- [ ] Nine services and six jobs remain healthy.

## 7. Production Context and Next Lab

### Production Implications

Set objectives, urgency, low-volume and missing-data policies with owners. Retain sufficient history and monitor rule failures before using reports for release decisions. Completed-response metrics do not cover every edge/client failure.

### End State and Transition

Keep nine services, six jobs, 20 recordings, seven alert definitions and three dashboards. [Lab 27](Lab-27.md) introduces individual event-record investigation through Loki.
