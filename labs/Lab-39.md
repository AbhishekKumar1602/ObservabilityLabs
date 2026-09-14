# Lab 39: Tail Sampling in the Collector

## 1. Purpose and Learning Outcomes

**In Plain Language:** You will move trace selection to the Collector so it can consider observed errors and duration before deciding what to retain. Exact synthetic fixtures first prove the policies, followed by real requests. You will then examine decision delay and incomplete arrival so deliberate sampling is not confused with failed delivery.

> **Primary Objective:** Retain observed errors and slow traces using explicit Collector policies, then reduce ordinary trace retention without confusing sampling with delivery failure.

Head sampling could not see a later failure. Tail sampling waits for trace data and applies policies to what has arrived. This lab installs one trace-complete tail sampler in the existing Collector and verifies its decisions with deterministic fixtures and the real two-service workload.

You will separate policy correctness from statistical baseline retention and explain decision delay, late spans, memory and routing constraints. This remains one single-node Collector, not a highly available sampling tier. No new metrics exporter, service graph or profiling component is introduced.

## 2. Starting Point and Lab Map

Use this guide inside the complete application repository. Run commands from the repository root in Bash unless a step says otherwise. Work through the starting checks in order; they load the helpers and state this lab needs.

Read the explanation before each step, run one complete code block at a time, and compare the actual result with the stated expectation. Save commands, observations, explanations, and unexpected results in your notebook.

### Key Terms

| **Term**            | **Plain-Language Meaning**                                                   |
| ------------------- | ---------------------------------------------------------------------------- |
| Tail sampling       | Choosing retention after waiting for trace data and inspecting what arrived. |
| Decision wait       | The interval allowed for trace data to accumulate before a policy decision.  |
| Trace-aware routing | Sending a trace's related spans to the same decision process.                |

### Lab Map

The arrows show the flow or dependency being studied. Branches identify paths to compare; this is a conceptual map, not a captured runtime trace.

```mermaid
flowchart TD
    A["OTLP receiver"] --> B["Memory limiter and redaction"]
    B --> C["Tail sampling decisions"]
    C --> D["Batch and persistent exporter queue"]
    D --> E["Tempo"]
```

## 3. Guided Walkthrough

### Step 01. Inherited State and Starting Checks

**What You Are Doing:** Restore full head sampling before testing the Collector's policy. Tail sampling cannot recover span data that an upstream head sampler never recorded.

**Practical Walkthrough:** Restore full head sampling and verify complete input traces before activating Collector selection. Tail sampling makes decisions from spans that actually reach it; it cannot recover spans discarded earlier by the SDK. Keep transport health separate from the policy comparison.

Check the running app's sampler setting after removing temporary shell overrides, then send a new known request and retrieve its required spans. Use new evidence rather than an old trace stored during full sampling. Save the Collector's pre-change configuration so a later missing trace can be investigated against an explicit before-state and the exact policy that was activated.

Complete [Lab 38](Lab-38.md) first. Run from the repository root in one Bash session; keep the existing credentials, named volumes, checkpoint item, dashboards and earlier evidence.

```bash
source lab-notes/session.sh
source lab-notes/evidence.sh
source lab-notes/metrics-session.sh
source lab-notes/platform-session.sh
source lab-notes/traces-session.sh
load_app_settings
start_lab 39
dp config --quiet
dp ps -a
wait_ready
wait_backend loki:3100 /ready
wait_backend tempo:3200 /ready
wait_backend otel-collector:13133 /
wait_backend lab-downstream:8001 /health/live
```

`dp` is the stage-aware Compose helper from Lab 31. It preserves the learning overlays and the current project. Plain `docker compose up` would use a different set of settings. `start_lab` creates a new `LAB_DIR`; all observations in this guide belong to that directory. Host tools remain Bash, Python 3, curl and jq; YAML edits use the isolated `lab-notes/.tools/bin/python` environment already installed in Lab 31.

The inherited platform has thirteen services, nine Prometheus scrape jobs and four existing dashboards. These labs add no service or scrape job. Pyroscope remains disabled until the profiling phase. The application still exposes native Prometheus metrics; traces travel through the Collector; Docker's fluentd logging driver sends JSON stdout to the same Collector and then Loki.

```bash
unset LAB_HEAD_SAMPLE_RATIO LAB_TRUST_TRACE_CONTEXT
cp lab-notes/tracing/collector.yml "$LAB_DIR/collector.before-tail.yml"
dp exec -T app python - <<'CHECK'
from app.config import Settings
assert Settings().trace_sample_ratio==1.0
print('Root head sampler supplies complete candidate traces')
CHECK
python3 lab-notes/tracing/scenario_load.py "$APP_URL" "$LAB_DIR/before-tail.jsonl" --count 3
LEDGER_JSON=$(cat "$LAB_DIR/before-tail.jsonl")
dp exec -T app python - "$LEDGER_JSON" --wait 25 --require-all < lab-notes/tracing/retention_audit.py > "$LAB_DIR/before-tail-audit.json"
```

Resolve any baseline missing spans first. A tail sampler cannot recover spans the SDK never recorded. Sending all root traces to the Collector increases upstream trace transport and processor work compared with 25% head sampling, even when fewer traces reach Tempo.

**Understanding the Result:** The sampler needs the intended input population. Upstream omission would invalidate claims about tail-policy retention.

### Step 02. Learning Objectives and Policy Design

**What You Are Doing:** Define error, slow-trace, and ordinary-baseline policies and their combination. A trace can satisfy more than one positive retention condition.

**Practical Walkthrough:** Read the positive policies together: retain error traces, retain traces meeting the 200-millisecond slow threshold, and retain a probabilistic ordinary baseline. More than one condition can match a trace. Predict fixtures by their actual duration and status rather than only by scenario name.

Predict retention from actual duration and error status, then consider the ordinary probabilistic policy separately. Positive policies can overlap, so a retained nominally normal request may have qualified as slow. Inspect its stored attributes before assigning the result to baseline probability.

The intended trace pipeline is:

The lab map in Section 2 shows this relationship.

| **Policy**                           | **Decision**                       |
| ------------------------------------ | ---------------------------------- |
| Any observed ERROR span              | Keep the trace                     |
| Trace elapsed extent at least 200 ms | Keep the trace                     |
| Remaining ordinary traces            | Keep approximately 10% by trace ID |

These positive policies combine as OR, not as an ordered “first match then stop” routing table. A trace can satisfy both error and latency policies. The probabilistic baseline does not reduce the ERROR/latency policies to 10%.

**Prediction Checkpoint:** the slow and error scenarios should remain available, while some normal traces disappear. Both services will still report `trace_sampled=true` because head sampling is 1.0. Tail decisions are made later and are not propagated back to the already completed request.

**Understanding the Result:** A normal scenario can still qualify as slow on a busy host. Retention does not necessarily identify which single policy matched.

### Step 03. Install the Version-Matched Tail Processor

**What You Are Doing:** Install the tail processor supported by the selected Collector version in the intended pipeline position. Its wait and cache settings affect when a known trace becomes queryable.

**Practical Walkthrough:** Install the version-compatible processor at the documented point in the active trace pipeline, preserving logs and export settings. Review decision wait, capacity, and cache behavior before loading. The ten-second wait begins from the processor's observation boundary, not from your client's request start.

Review processor placement, decision wait, pending capacity, and decision-cache settings in the active trace pipeline. Preserve logs and exporter configuration. The wait starts when sampling state observes the trace, so client start time alone cannot establish the exact retention-decision deadline.

```bash
cat > lab-notes/tracing/tail_config.py <<'PYTHON'
"""Enable trace-complete tail sampling without changing the log pipeline."""
import argparse
from pathlib import Path
import yaml

parser = argparse.ArgumentParser()
parser.add_argument('--baseline-percent', type=float, choices=[0.0, 10.0], default=10.0)
args = parser.parse_args()
path = Path('lab-notes/tracing/collector.yml')
config = yaml.safe_load(path.read_text())
config['processors']['tail_sampling'] = {
    'sampling_strategy': 'trace-complete', 'decision_wait': '10s',
    'num_traces': 1000, 'expected_new_traces_per_sec': 20,
    'decision_cache': {'sampled_cache_size': 10000, 'non_sampled_cache_size': 10000},
    'policies': [
        {'name': 'keep-errors', 'type': 'status_code', 'status_code': {'status_codes': ['ERROR']}},
        {'name': 'keep-slow', 'type': 'latency', 'latency': {'threshold_ms': 200}},
        {'name': 'ordinary-baseline', 'type': 'probabilistic',
         'probabilistic': {'sampling_percentage': args.baseline_percent}}]}
pipeline = config['service']['pipelines']['traces']
assert pipeline['receivers'] == ['otlp'] and pipeline['exporters'] == ['otlp_grpc/tempo']
pipeline['processors'] = ['memory_limiter', 'transform/traces', 'tail_sampling', 'batch']
path.write_text(yaml.safe_dump(config, sort_keys=False))
PYTHON
```

**Command Note:** `<<'PYTHON'` writes the following block literally until `PYTHON`. Quoting the delimiter prevents Bash from expanding `$variables` inside the generated file; creation and execution are separate steps.

```bash
lab-notes/.tools/bin/python lab-notes/tracing/tail_config.py --baseline-percent 0
dp run --rm -T --no-deps otel-collector validate --config=/etc/otelcol-contrib/config.yaml
dp up -d --no-deps --force-recreate otel-collector
wait_backend otel-collector:13133 /
```

The pinned `otel/opentelemetry-collector-contrib:0.160.0` contains `tail_sampling`. The explicit `trace-complete` strategy and decision-cache settings are validated against that distribution. Do not paste this configuration into an arbitrary older core Collector image.

`decision_wait=10s` starts when a trace is first observed; it is not a guarantee that every span has arrived. `num_traces=1000` bounds trace entries, not total bytes. `expected_new_traces_per_sec=20` helps size internal structures; it is not a rate limiter. The two caches retain earlier decisions for later spans after a trace leaves the waiting buffer.

We first use 0% ordinary retention to make the policy fixture deterministic. After verifying the positive controls and one negative control, we install the intended 10% baseline. The existing logs pipeline is untouched; sampling is only in the traces pipeline, after privacy filtering and before export batching.

**Understanding the Result:** Configured wait is one component of visibility delay. SDK, batching, export, and backend processing add other boundaries.

### Step 04. Prove the Policies with Exact OTLP Fixtures

**What You Are Doing:** Send exact-duration and exact-status fixtures through the Collector receiver. This isolates policy mechanics before variable application and host timing enter the experiment.

**Practical Walkthrough:** Send the exact-status and exact-duration OTLP fixtures with the ordinary baseline initially disabled as directed. Their controlled fields isolate the error and latency policies from variable application execution. Audit every fixture ID after the decision interval and retain both expected keeps and drops.

Keep the ordinary baseline disabled for deterministic policy fixtures. Verify positive error and duration controls before interpreting an absent expected-drop trace. Audit the exact IDs after the decision and export interval so a still-pending trace is not mislabeled as an intentional drop.

```bash
cat > lab-notes/tracing/policy_probe.py <<'PYTHON'
"""Deterministic OTLP fixtures through the Collector; never sent directly to Tempo."""
import json
import secrets
import time
from urllib.request import Request, urlopen

now = time.time_ns()
spans, cases = [], []
for scenario, duration_ms, code in [('normal', 25, 1), ('slow', 250, 1), ('error', 25, 2)]:
    trace_id = secrets.token_hex(16)
    cases.append({'scenario': scenario, 'trace_id': trace_id})
    spans.append({'traceId': trace_id, 'spanId': secrets.token_hex(8), 'name': 'policy.fixture',
                  'kind': 1, 'startTimeUnixNano': str(now-duration_ms*1000000),
                  'endTimeUnixNano': str(now), 'status': {'code': code},
                  'attributes': [{'key': 'lab.scenario', 'value': {'stringValue': scenario}}]})
body = {'resourceSpans': [{'resource': {'attributes': [
    {'key': 'service.name', 'value': {'stringValue': 'lab-sampling-fixture'}},
    {'key': 'deployment.environment.name', 'value': {'stringValue': 'local'}}]},
    'scopeSpans': [{'scope': {'name': 'lab.policy.fixture'}, 'spans': spans}]}]}
request = Request('http://otel-collector:4318/v1/traces',
    data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'}, method='POST')
with urlopen(request, timeout=5) as response:
    result = json.load(response)
assert not int(result.get('partialSuccess', {}).get('rejectedSpans', 0)), result
print(json.dumps({'cases': cases, 'accepted_http': 200}, indent=2))
PYTHON
```

```bash
cat > lab-notes/tracing/verify_policy.py <<'PYTHON'
"""Confirm positive policy controls before interpreting the deliberately absent fixture."""
import argparse
import json
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

parser=argparse.ArgumentParser()
parser.add_argument('fixture_json')
args=parser.parse_args()
cases=json.loads(args.fixture_json)['cases']
results={}
for case in sorted(cases,key=lambda c:c['scenario']=='normal'):
    scenario,trace_id=case['scenario'],case['trace_id']
    deadline=time.monotonic()+30
    while True:
        try:
            request=Request('http://tempo:3200/api/traces/'+trace_id,headers={'Accept':'application/json'})
            with urlopen(request,timeout=3) as response:
                document=json.load(response)
            spans=[s for b in document.get('batches',document.get('resourceSpans',[]))
                   for scope in b.get('scopeSpans',b.get('instrumentationLibrarySpans',[]))
                   for s in scope.get('spans',[])]
            assert any(s['name']=='policy.fixture' for s in spans), 'Incomplete fixture response'
            code=200
        except HTTPError as error:
            if error.code!=404: raise
            code=404
        if scenario=='normal' or code==200 or time.monotonic()>=deadline:
            break
        time.sleep(1)
    expected=404 if scenario=='normal' else 200
    assert code==expected,(scenario,code,expected)
    results[scenario]={'trace_id':trace_id,'http_status':code}
print(json.dumps(results,indent=2))
PYTHON
```

```bash
dp exec -T app python - < lab-notes/tracing/policy_probe.py > "$LAB_DIR/policy-fixtures.json"
FIXTURE_JSON=$(cat "$LAB_DIR/policy-fixtures.json")
dp exec -T app python - "$FIXTURE_JSON" < lab-notes/tracing/verify_policy.py > "$LAB_DIR/policy-proof.json"
jq . "$LAB_DIR/policy-proof.json"
```

These are deliberately labeled `lab-sampling-fixture` spans, sent to the Collector's OTLP/HTTP receiver, never directly to Tempo. Their timestamps define exactly 25 ms normal, 250 ms slow and 25 ms ERROR operations. A synthetic fixture tests policy mechanics; it is not claimed to be an application request, measured latency or a native application metric.

The verification first waits for the slow/error positive controls, then requires HTTP 404 for the normal negative control with ordinary retention at zero. Other HTTP errors fail the test. This is stronger than immediately declaring an absent trace “sampled out” before any decision window has elapsed. Expected output is `slow:200`, `error:200`, `normal:404` with your generated IDs.

A successful OTLP HTTP response establishes intake acceptance, not durable storage. The later lookup verifies the selected fixture's backend arrival. In a fault-free run these controls isolate the policy; with transport errors or early eviction, investigate those before interpreting the negative control.

**Understanding the Result:** Exact fixtures test policy mechanics. Real application scenarios later add timing variability and additional span structure.

### Step 05. Enable the Ordinary Baseline and Run Real Requests

**What You Are Doing:** Enable the ordinary baseline and generate the real scenario population. Inspect unexpectedly retained normal traces before assuming the probability rule is wrong; they may satisfy another policy.

**Practical Walkthrough:** Enable the ten-percent ordinary baseline and run the finite real workload. Compare retained trace attributes and durations with all positive policies before attributing a result to probability. Keep head sampling at full input so the Collector receives the population being assessed.

Enable the documented ordinary percentage and retain full head sampling for the real workload. Compare each retained trace with every positive policy. Use actual duration and status rather than scenario names when explaining why the Collector kept a particular operation.

```bash
lab-notes/.tools/bin/python lab-notes/tracing/tail_config.py --baseline-percent 10
dp run --rm -T --no-deps otel-collector validate --config=/etc/otelcol-contrib/config.yaml
dp up -d --no-deps --force-recreate otel-collector
wait_backend otel-collector:13133 /
python3 lab-notes/tracing/scenario_load.py "$APP_URL" "$LAB_DIR/tail-ledger.jsonl" --count 60
LEDGER_JSON=$(cat "$LAB_DIR/tail-ledger.jsonl")
dp exec -T app python - "$LEDGER_JSON" --wait 35 < lab-notes/tracing/retention_audit.py > "$LAB_DIR/tail-audit.json"
python3 - "$LAB_DIR/tail-audit.json" <<'CHECK'
import json,sys
data=json.load(open(sys.argv[1]))
assert data['head_sampled']==60
for scenario in ['normal','slow','error']:
    rows=[r for r in data['traces'] if r['scenario']==scenario]
    kept=sum(r['complete'] for r in rows)
    print(scenario,'complete',kept,'of',len(rows))
    if scenario in {'slow','error'}:
        assert kept==len(rows), 'Investigate missing/late spans, eviction or export failure'
print('Retained span count:',data['span_count'],'retained event count:',data['event_count'])
CHECK
```

Expect all twenty slow and twenty error traces, plus some ordinary traces. Ten percent of the twenty normal traces has an expected value of two, not a guaranteed count. Host contention can push nominally normal trace duration over 200 ms, legitimately retaining more under the latency policy. Open a retained normal trace before blaming the probabilistic rule.

Overall retention is much greater than 10% for this intentionally failure-heavy workload. The workload mix, overlapping policies and slow-host effects determine the total. A result of 42 retained traces would be plausible, not a required answer.

Compare with the Lab 38 head-sampling evidence: error and slow diagnostic coverage is now prioritized, but every candidate trace first reaches the Collector. State the tradeoff in both transport/processor work and backend volume.

**Understanding the Result:** Finite random results need not equal exactly ten percent. Unexpectedly retained normal traces may meet the slow or error policy.

### Step 06. Observe Decision Delay and Remaining Evidence

**What You Are Doing:** Measure eventual visibility and inspect the actual sampler metrics. End-to-end delay includes more stages than the configured tail decision wait.

**Practical Walkthrough:** Measure when known traces become queryable and inspect the sampler's actual exported metrics. Allow the documented decision interval plus downstream delivery time. Do not substitute a guessed metric name or declare loss at the exact instant the decision wait expires.

Measure known-ID availability across the decision and export interval and inspect actual sampler metrics. Avoid declaring loss immediately at the nominal wait boundary because downstream batching and storage add delay. Keep lookup timestamps so the observed latency is reviewable.

```bash
python3 lab-notes/tracing/scenario_load.py "$APP_URL" "$LAB_DIR/delay-ledger.jsonl" --count 1 --only error
DELAY_TRACE=$(jq -r '.response.upstream_trace_id' "$LAB_DIR/delay-ledger.jsonl")
if backend tempo:3200 "/api/traces/$DELAY_TRACE" > "$LAB_DIR/early-trace.json" 2> "$LAB_DIR/early-lookup.error"; then
  echo 'Trace was already visible; inspect elapsed wall time before claiming an immediate decision'
else
  cat "$LAB_DIR/early-lookup.error"
fi
fetch_trace "$DELAY_TRACE" "$LAB_DIR/decided-trace.json"
python3 lab-notes/tracing/verify_boundary.py "$LAB_DIR/decided-trace.json"
backend otel-collector:8888 /metrics > "$LAB_DIR/collector-tail.prom"
rg 'tail_sampling|otelcol_receiver_accepted_spans|otelcol_exporter_sent_spans' "$LAB_DIR/collector-tail.prom"
```

A quick lookup often returns 404 while the decision buffer is waiting. Use the recorded request completion time and the eventual lookup time to describe observed visibility delay; it includes SDK batching, tail waiting and backend ingestion. Do not report exactly ten seconds as a guaranteed end-to-end delay.

Discover the actual exposed tail-sampling metric names/labels in the captured exposition before writing a query. Policy counters can overlap because one trace may satisfy several policies; summing them need not equal unique retained traces. Receiver and exporter counters describe different stages and cannot be equated after intentional filtering.

In Loki, find a normal request that the audit did not find in Tempo. Its completion log and sampled flag can still exist. Unlike the Lab 38 zero-ratio example, `trace_sampled=true` here describes the head decision while the later tail policy omits the trace. Native Prometheus HTTP metrics still count it.

**Understanding the Result:** Eventual visibility is an end-to-end observation. Sampler counters describe only their own processing boundary.

### Step 07. Reason About Late Spans and Capacity

**What You Are Doing:** Reason about late spans, incomplete decisions, and memory capacity. A distributed sampling tier must keep related spans together for meaningful trace-level decisions.

**Practical Walkthrough:** Consider what happens when spans arrive late or after a cached decision and when pending traces exceed capacity. Meaningful trace-level decisions require related spans to reach the same sampling state. These limits explain why a distributed collector tier needs consistent trace routing and sufficient memory.

Follow a late span relative to the decision and cache lifetime, then consider pending-trace capacity. Related spans need access to the same sampling state for meaningful trace-level decisions. Describe those assumptions explicitly before extrapolating this single-Collector exercise to a distributed deployment.

With one Collector, both services naturally send their spans to the same decision process. A production tier with multiple samplers requires trace-aware routing; round-robin distribution can split one trace across independent decisions. Merely sharing exporter storage does not solve that problem.

A decision based on incomplete data can miss a late ERROR span. A decision cache helps later spans follow an earlier decision; it cannot retroactively reconsider an already dropped trace or reconstruct missing data. Size the wait using observed request duration and arrival delay, then verify it under load.

Waiting traces and decision caches consume memory. The exporter’s persistent queue begins after the sampler and batch processor; it does not persist undecided traces. Restarting the Collector during the decision window can lose that in-memory evidence. The rough concurrent-trace estimate `arrival rate × wait` is only a starting point: span counts, attribute sizes and bursts affect actual bytes.

Do not claim an exact event population or unbiased error rate from tail-selected traces. This selection intentionally favors slow and failed work; keep native Prometheus metrics for service-level denominators.

**Understanding the Result:** Tail sampling is not unlimited retrospective reconstruction. Late data and capacity pressure can affect completeness and decisions.

### Step 08. Recovery and Troubleshooting

**What You Are Doing:** Verify the approved policies and fresh retained canaries after experiments. Classify an absent trace using sampling, intake, and export evidence rather than assigning every absence to one cause.

**Practical Walkthrough:** Restore the approved policy and run fresh error or slow canaries known to qualify for retention. Verify logs, business readiness, and trace export as well. Classify any missing trace using head decisions, intake, tail selection, and export evidence rather than labeling every absence as transport loss.

Run new error or slow canaries that qualify under the approved policy and verify their complete traces. Check business and log paths independently. For an absent trace, classify head sampling, Collector intake, tail policy, and export before calling the result transport loss.

```bash
wait_ready
wait_backend tempo:3200 /ready
wait_backend otel-collector:13133 /
dp logs --since=5m --tail=150 --no-color otel-collector > "$LAB_DIR/collector-tail.log"
lab-notes/.tools/bin/python - <<'CHECK'
import yaml
c=yaml.safe_load(open('lab-notes/tracing/collector.yml'))
t=c['processors']['tail_sampling']
assert t['policies'][-1]['probabilistic']['sampling_percentage']==10
assert c['service']['pipelines']['traces']['processors'].index('tail_sampling') < c['service']['pipelines']['traces']['processors'].index('batch')
print('Intended 10% ordinary baseline retained')
CHECK
```

| **Symptom**                                               | **Investigate**                                                                                        |
| --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| All ordinary fixture traces retained during the 0% test   | Wrong active config, sampler missing from pipeline, stale fixture IDs or actual ERROR/duration fields. |
| Error fixture absent                                      | Intake failure, wrong status code, memory refusal, early eviction, timing or export/backend failure.   |
| App errors missing despite a correct fixture              | Head ratio/propagation, late error spans and actual application status instrumentation.                |
| Normal request retained unexpectedly                      | Probabilistic baseline or actual trace duration above the latency threshold.                           |
| More policy matches than traces                           | Policies overlap; do not sum matches as a unique trace count.                                          |
| Exporter queue survives but some traces vanish on restart | Tail decision state and upstream batch buffers are separate in-memory stages.                          |

Normal completion keeps tail sampling enabled with ERROR, 200 ms latency and 10% ordinary policies. For rollback, copy `$LAB_DIR/collector.before-tail.yml` to `lab-notes/tracing/collector.yml`, validate and recreate the Collector. Keep that rollback file; do not delete data volumes. If you roll back, reapply the verified tail configuration before Lab 40.

**Understanding the Result:** Keep the policy active for later labs. A retained fresh canary proves the current path for that known qualifying case.

## 4. Troubleshooting and Diagnosis

Start with the first unexpected result. Record the exact symptom, identify which component produced it, and use the checks below before repeating the experiment. After a fix, repeat the original check and confirm recovery.

Use the recovery and troubleshooting checks in Step 08.

## 5. Review and Practice

Answer the knowledge questions in your own words before reading the answer guide. For the scenario, name the evidence you would collect and the conclusion each observation would support.

### Knowledge Check

1. Why cannot tail sampling repair head-sampled-away errors?
2. Does a 10% ordinary policy imply 10% overall retention?
3. Does file_storage persist the tail decision buffer?
4. What happens when a decisive error arrives after the original drop decision?

#### Answer Guide

1. Unrecorded spans never reach the Collector, so it has no error evidence to evaluate.
2. No. ERROR and latency policies retain additional traces, often with overlap.
3. No. It persists the configured exporter queue; undecided tail state remains in memory.
4. A cached drop can continue to omit that trace; the decision cannot reconstruct or retroactively retain its missing earlier data.

### Professional Scenario Exercise

A team adds two tail-sampling Collectors behind a round-robin load balancer and sees incomplete error traces. Draw the point where trace-aware routing is required, identify which memory and delivery limits still exist, and design a bounded test with known IDs before increasing the replica count.

## 6. Completion Checklist

Mark each item only after you can point to its result or evidence file. Complete any cleanup shown here before moving on.

### Observable Completion Criteria

- [ ] Head sampling is 1.0 and the pre-policy baseline is complete.
- [ ] The exact pinned Collector validates the tail configuration.
- [ ] Deterministic slow/error fixtures survive and the ordinary 0% fixture is absent.
- [ ] The real workload retains complete slow/error traces under the final policies.
- [ ] The notebook distinguishes head flags, tail choices, export success and backend lookup.
- [ ] The final ordinary baseline is 10%, with logs and native metrics healthy.

## 7. Production Context and Next Lab

### Production Implications

Tail sampling trades backend reduction and diagnostic preference for upstream volume, memory, decision delay and routing complexity. Test policy overlap, long requests and late spans. This single-node lab proves mechanics, not high availability. See the [version-pinned tail sampling processor](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/v0.160.0/processor/tailsamplingprocessor) and [OpenTelemetry sampling concepts](https://opentelemetry.io/docs/concepts/sampling/).

### End State and Transition

Keep full head sampling and the verified tail policies. [Lab 40](Lab-40.md) interrupts Tempo export and examines exactly which buffers, retries and memory protections preserve or lose trace data.
