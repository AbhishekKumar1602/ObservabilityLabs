# Lab 07: Raw OpenMetrics Before Prometheus

## Purpose and Scope

> **Primary Objective:** Read and validate real metric exposition, distinguish families and samples, implement OpenMetrics content negotiation, and prove how request events change aggregate numeric state.

Lab 6 preserved individual observations with record and request IDs. This lab examines a different representation: the current state of counters, gauges and histograms.

Prometheus remains stopped. You will first inspect what the application actually sends, then extend its metrics endpoint to negotiate OpenMetrics using the installed client library. Calling a route `/metrics` does not establish its wire format.

## 1. Inherited State and Scope

Continue from Lab 6 with the structured logging envelope, its tests and evidence helper. Keep the original baseline overlay and the Lab 2 connection-error correction.

This lab covers exposition, metadata, types, labels, parsing and raw deltas. PromQL, a time-series database, alert evaluation and dashboards come later. Existing application metrics are already implemented; you will understand their contract before adding instrumentation.

## 2. Start From a Known State

```bash
source lab-notes/session.sh
source lab-notes/evidence.sh
baseline_check
start_lab 07
api -fsS -D "$LAB_DIR/original-headers.txt" "$APP_URL/metrics" \
  -o "$LAB_DIR/original.prom"
rg -i '^content-type:' "$LAB_DIR/original-headers.txt"
sed -n '1,24p' "$LAB_DIR/original.prom"
```

Expected initially: a successful response with a `text/plain` Prometheus exposition content type. The original implementation uses `prometheus_client.generate_latest`; it does not negotiate OpenMetrics simply because the caller requests it.

Keep exactly app, PostgreSQL and Redis running. The application is the metric producer; curl is only reading it.

## 3. Measurable Learning Objectives

By the end, you should be able to:

- identify a metric family, sample name, label set and value;
- distinguish exposition metadata from numeric samples;
- explain counters, gauges and classic histogram components;
- verify actual Content-Type and the OpenMetrics EOF marker;
- use the matching parser for each representation;
- demonstrate that individual requests modify aggregate state;
- prove metrics scrapes do not add business request counts here;
- distinguish an absent series from an observed zero; and
- preserve individual event evidence without putting its IDs into labels.

## 4. Occurrence, Observation and Sample

| Layer | Example | What is retained |
|---|---|---|
| Event | A GET completes | The actual occurrence |
| Instrument observation | Request counter increments; duration histogram observes seconds | Numeric state update in the application process |
| Exposed sample | A counter line currently has value 12 | Current value and labels |
| Log record | `request_completed` with a request ID | Selected details of one occurrence |
| Future Prometheus sample | The scraper stores counter value 12 at its scrape time | Time-series value, target labels and timestamp |

Five requests can occur between two scrapes. The counter may change from 12 to 17; that does not create five separately queryable request records in Prometheus. Request IDs belong in the event evidence, not the counter's labels.

There is no Prometheus retention yet. Refreshing curl reads current process state again; it does not give you a historical database.

## 5. Read the Exposition Grammar

```bash
rg '^# (HELP|TYPE) application_' "$LAB_DIR/original.prom"
rg '^application_dependency_up' "$LAB_DIR/original.prom"
rg '^application_cache_hits_total' "$LAB_DIR/original.prom"
```

A representative sample is:

```text
application_dependency_up{dependency="postgres"} 1.0
```

The metric name plus complete label set identifies the series. `1.0` is its current value. `HELP` documents meaning and `TYPE` declares an instrument type; neither is an observation of an individual request.

The endpoint normally omits explicit per-line timestamps. Do not confuse a `_created` sample's numeric Unix timestamp with a timestamp suffix on every exposition line. Prometheus will ordinarily assign a scrape timestamp when it stores samples.

Escaping and floating-point syntax are part of the format. Use the official parser instead of splitting every line on braces or assuming every value is an integer. See the [Prometheus exposition specification](https://prometheus.io/docs/instrumenting/exposition_formats/).

## 6. Inventory the Existing Families

| Family or sample prefix | Type | Meaning and boundary |
|---|---|---|
| `application_http_requests_total` | Counter | Completed server-observed request paths; health/metrics excluded |
| `application_http_request_duration_seconds` | Histogram | Server elapsed request time by method and normalized route |
| `application_http_requests_in_progress` | Gauge | Tracked requests currently active in this worker |
| `application_exceptions_total` | Counter | Classified handled failures; not every HTTP 4xx |
| `application_postgres_operation_duration_seconds` | Histogram | Application-side operation time, including its waits/transaction boundary |
| `application_cache_hits_total` / `_misses_total` | Counters | Valid hits versus misses, errors or deliberate bypass |
| `application_redis_errors_total` | Counter | Failed cache operations by bounded operation |
| `application_dependency_up` | Gauge | Application-observed dependency state |
| `process_*`, `python_*` | Runtime families | Process/Python observations exposed by this registry |

Some labelled instruments have no child series until that label combination is observed. A HELP/TYPE line alone does not prove a numeric sample exists. An unlabelled counter can legitimately be present at zero before its first event.

`application_dependency_up` is a probe observation, not a database exporter. Its value does not prove every future transaction will succeed.

## 7. Predict the Two Wire Representations

Before editing, request OpenMetrics explicitly and inspect the response:

```bash
api -fsS -H 'Accept: application/openmetrics-text; version=1.0.0' \
  -D "$LAB_DIR/before-openmetrics-headers.txt" "$APP_URL/metrics" \
  -o "$LAB_DIR/before-openmetrics.txt"
rg -i '^content-type:' "$LAB_DIR/before-openmetrics-headers.txt"
tail -n 2 "$LAB_DIR/before-openmetrics.txt"
```

On the unchanged baseline, the server still returns its fixed Prometheus text representation. This is an implementation observation, not a reason to rename the file and claim it is OpenMetrics.

Predict which fields will change after negotiation and which underlying values should remain the same. The representation should change; a negotiation request must not increment business counts.

## 8. Implement Real Format Negotiation

Use the installed `prometheus_client.exposition.choose_encoder` function. This adds no OpenTelemetry metric pipeline or new dependency. The guard refuses to patch an unfamiliar route.

```bash
python3 - <<'PYTHON'
from pathlib import Path
path = Path("app/app/main.py")
source = path.read_text()
changes = [
    ("from prometheus_client import CONTENT_TYPE_LATEST, generate_latest",
     "from prometheus_client.exposition import choose_encoder"),
    ("async def prometheus_metrics() -> Response:",
     "async def prometheus_metrics(request: Request) -> Response:"),
    ('        return Response(generate_latest(metrics.registry), headers={"Content-Type": CONTENT_TYPE_LATEST})',
     '        encoder, content_type = choose_encoder(request.headers.get("accept", ""))\n'
     '        return Response(encoder(metrics.registry), headers={"Content-Type": content_type, "Vary": "Accept"})'),
]
for old, new in changes:
    if new in source:
        continue
    if source.count(old) != 1:
        raise SystemExit("Expected metrics route not found; review existing edits")
    source = source.replace(old, new, 1)
path.write_text(source)
print("Metrics endpoint supports client-library format negotiation")
PYTHON
```

The endpoint passes the Accept header to the client library, uses its encoder and Content-Type together, and emits `Vary: Accept`. Do not manually append `# EOF` to a legacy document or change the header while leaving the encoder unchanged.

## 9. Test Both Formats and Scrape Exclusion

Create the focused exposition tests:

```bash
cat > app/tests/test_exposition.py <<'PYTHON'
from prometheus_client.openmetrics.parser import text_string_to_metric_families as openmetrics_families
from prometheus_client.parser import text_string_to_metric_families as prometheus_families


async def test_both_exposition_formats(client):
    await client.get("/api/v1/items?limit=1")
    plain = await client.get("/metrics", headers={"Accept": "text/plain; version=0.0.4"})
    modern = await client.get("/metrics", headers={"Accept": "application/openmetrics-text; version=1.0.0"})
    assert plain.status_code == modern.status_code == 200
    assert "text/plain" in plain.headers["content-type"]
    assert "application/openmetrics-text" in modern.headers["content-type"]
    assert plain.headers["vary"] == modern.headers["vary"] == "Accept"
    assert not plain.text.endswith("# EOF\n")
    assert modern.text.endswith("# EOF\n")
    filters = {"method": "GET", "route": "/api/v1/items", "status_code": "200"}
    values = []
    for response, parser in ((plain, prometheus_families), (modern, openmetrics_families)):
        values.append(
            next(
                sample.value
                for family in parser(response.text)
                for sample in family.samples
                if sample.name == "application_http_requests_total" and sample.labels == filters
            )
        )
    assert values == [1.0, 1.0]


async def test_metrics_scrapes_do_not_create_request_events(client, app):
    for _ in range(3):
        assert (await client.get("/metrics")).status_code == 200
    assert not any(
        sample.name == "application_http_requests_total"
        for family in app.state.metrics.registry.collect()
        for sample in family.samples
    )
PYTHON
```

```bash
make test
make lint
git diff --check
record_change "enable_metrics_format_negotiation" planned
dc up -d --build --no-deps app
baseline_check
record_change "enable_metrics_format_negotiation" completed
```

The application restarts with a fresh in-memory registry. Earlier raw counter values from this lab are therefore not the starting value for the next experiment. Capture a new baseline after the rebuild.

## 10. Capture and Verify Both Negotiated Results

```bash
api -fsS -H 'Accept: text/plain; version=0.0.4' \
  -D "$LAB_DIR/plain-headers.txt" "$APP_URL/metrics" -o "$LAB_DIR/plain.prom"
api -fsS -H 'Accept: application/openmetrics-text; version=1.0.0' \
  -D "$LAB_DIR/openmetrics-headers.txt" "$APP_URL/metrics" -o "$LAB_DIR/metrics.om"
rg -i '^content-type:|^vary:' "$LAB_DIR/plain-headers.txt" "$LAB_DIR/openmetrics-headers.txt"
tail -n 1 "$LAB_DIR/metrics.om"
dc exec -T app python -c 'import sys; from prometheus_client.openmetrics.parser import text_string_to_metric_families as parse; print("families=", len(list(parse(sys.stdin.read()))))' \
  < "$LAB_DIR/metrics.om"
```

Expected: legacy text for the first response, `application/openmetrics-text` for the second, `Vary: Accept`, and the final line `# EOF` in the OpenMetrics document. A successful matching parser is stronger evidence than visually spotting one familiar line.

The format is not storage ownership. Either negotiated representation is still exposed directly by FastAPI for Prometheus to scrape.

## 11. Add a Reusable Raw-Snapshot Parser

Create a local diagnostic script; it runs inside the app image so the host needs no extra Python dependencies:

```bash
cat > lab-notes/metric_snapshot.py <<'PYTHON'
"""Parse exposition received on stdin; run inside the app image."""
import json
import sys
from prometheus_client.parser import text_string_to_metric_families

families = list(text_string_to_metric_families(sys.stdin.read()))
print(json.dumps([
    {"name": sample.name, "labels": sample.labels, "value": sample.value}
    for family in families for sample in family.samples
]))
PYTHON
```

```bash
cat > lab-notes/raw-metrics.sh <<'BASH'
# Requires the Lab 1 session and Lab 6 evidence helpers.
snapshot() {
  local destination="$1"
  api -fsS -H 'Accept: text/plain; version=0.0.4' "$APP_URL/metrics" \
    | dc exec -T app python -c "$(cat "$LAB_ROOT/lab-notes/metric_snapshot.py")" \
    > "$destination"
}

metric_sum() {
  local filters="${3:-}"
  [[ -n "$filters" ]] || filters='{}'
  python3 - "$1" "$2" "$filters" <<'PYTHON'
import json, sys
path, name, raw_filter = sys.argv[1:]
wanted = json.loads(raw_filter)
with open(path, encoding="utf-8") as stream:
    samples = json.load(stream)
selected = [sample for sample in samples if sample["name"] == name
            and all(sample["labels"].get(key) == value for key, value in wanted.items())]
print(sum(sample["value"] for sample in selected))
PYTHON
}
BASH
```

```bash
source lab-notes/raw-metrics.sh
snapshot "$LAB_DIR/first-snapshot.json"
jq '.[0:8]' "$LAB_DIR/first-snapshot.json"
```

`snapshot` explicitly requests legacy text and uses that format's parser. The JSON contains sample names, label dictionaries and values, not just family names. It includes runtime samples as well as application samples.

`metric_sum` sums an explicitly selected set of samples and returns zero for an empty set. That convenience is valid for the controlled counter-before-first-observation experiments below; it is **not** a general claim that missing telemetry means healthy or zero. Check sample presence separately when interpreting an outage.

## 12. Measure Five Request Events Without Prometheus

Warm the list route once so its label child exists, then isolate five further requests:

```bash
api -fsS "$APP_URL/api/v1/items?limit=1" >/dev/null
snapshot "$LAB_DIR/before.json"
for n in 1 2 3 4 5; do
  api -fsS -H "X-Request-ID: lab07-$n-$(new_uuid)" \
    "$APP_URL/api/v1/items?limit=1" >/dev/null
done
snapshot "$LAB_DIR/after.json"
FILTER='{"method":"GET","route":"/api/v1/items","status_code":"200"}'
BEFORE=$(metric_sum "$LAB_DIR/before.json" application_http_requests_total "$FILTER")
AFTER=$(metric_sum "$LAB_DIR/after.json" application_http_requests_total "$FILTER")
python3 - "$BEFORE" "$AFTER" <<'PYTHON'
import sys
before, after = map(float, sys.argv[1:])
print("request_counter_delta=", after-before)
assert after-before == 5
PYTHON
capture_app_logs
jq -s '[.[] | select(.event_name == "request_completed" and ((.request_id // "") | startswith("lab07-")))] | length' \
  "$LAB_DIR/app.jsonl"
```

Under isolated traffic, the numeric delta and matching completion-record count are both five. The numeric sample cannot enumerate those five request IDs. The captured logs can, subject to their own emission and retention boundaries.

If the delta exceeds five, inspect concurrent traffic before editing the counter. If the logs show fewer records, inspect capture timing, level and rotation rather than subtracting missing log entries from metric state.

## 13. Prove Scraping Does Not Count as Business Traffic

```bash
snapshot "$LAB_DIR/before-scrapes.json"
for n in 1 2 3; do api -fsS "$APP_URL/metrics" >/dev/null; done
snapshot "$LAB_DIR/after-scrapes.json"
python3 - "$LAB_DIR/before-scrapes.json" "$LAB_DIR/after-scrapes.json" <<'PYTHON'
import json, sys
def counters(path):
    return sorted((json.dumps(s["labels"], sort_keys=True), s["value"])
                  for s in json.load(open(path)) if s["name"] == "application_http_requests_total")
assert counters(sys.argv[1]) == counters(sys.argv[2])
print("Scrape requests did not change application request counts")
PYTHON
```

Do not expect every runtime metric to remain identical. Formatting and serving metrics consumes CPU and time; process counters can change. The meaningful assertion is that this implementation excludes `/metrics`, `/health/live` and `/health/ready` from its HTTP business instrumentation.

## 14. Read a Classic Histogram Without Calculating a Percentile Yet

```bash
api -fsS "$APP_URL/metrics" > "$LAB_DIR/histograms.prom"
rg '^application_http_request_duration_seconds_(bucket|sum|count)' "$LAB_DIR/histograms.prom"
```

| Component | Meaning |
|---|---|
| `_bucket{le="0.1"}` | Count of observations at or below 0.1 seconds |
| `_bucket{le="+Inf"}` | Count of all observations |
| `_count` | Total observation count for the method/route |
| `_sum` | Sum of observed seconds |
| `_created`, when exposed | Instrument-child creation time, not the request duration |

Buckets are cumulative across boundaries. One fast request increments several bucket counters, but remains one observation. Never add every bucket count and call the result request volume.

This is a **classic** histogram with explicit `_bucket` series. OpenMetrics negotiation alone does not turn it into a native histogram. Quantiles and interpolation are Lab 13 topics.

## 15. Bounded Interpretation Exercise: Zero Versus Missing

```bash
snapshot "$LAB_DIR/presence.json"
jq '[.[] | select(.name == "application_dependency_up")]' "$LAB_DIR/presence.json"
jq '[.[] | select(.name == "metric_that_is_not_implemented")]' "$LAB_DIR/presence.json"
```

Expected: observed dependency samples and an empty array for the deliberately nonexistent name. You have not discovered an exporter failure metric or proved zero errors by querying an invented name.

Identify one labelled route child not yet observed after the rebuild. Explain why no child exists while its family metadata may still be present. Generate one matching request and compare again. Use a known bounded route; do not add request IDs as labels to force uniqueness.

## 16. Recovery and Final Verification

```bash
baseline_check
CHECKPOINT_ID=$(cat lab-notes/checkpoint-item-id.txt)
api -fsS "$APP_URL/api/v1/items/$CHECKPOINT_ID" | jq '{id,name,price}'
snapshot "$LAB_DIR/final.json"
make test
```

No new business rows were created. The permanent change is real content negotiation plus its tests. Keep the snapshot helpers for the next labs. There is still no Prometheus container or duplicate metric exporter.

## 17. Troubleshooting Runbook

| Symptom | Check and correction |
|---|---|
| OpenMetrics request still returns legacy text | Confirm the patch, image rebuild and running route signature; read Content-Type rather than guessing from filename |
| Parser reports malformed exposition | Match parser to Content-Type, preserve the original response, and inspect whether an HTTP error body was saved instead |
| Counter child is absent | Generate the relevant route/method/status event and inspect again; distinguish family metadata from samples |
| Counter delta exceeds your request count | Check other clients and the exact selected labels; avoid summing all routes |
| Counter delta becomes negative | A process restarted between snapshots; discard that delta and record the reset |
| Samples have no timestamps at line end | Normal for this exposition; a future scraper assigns sample time |
| Histogram bucket counts appear too large | Buckets overlap cumulatively; use `_count` for observations |
| Host import of prometheus_client fails | Run the parser inside the app image as instructed |
| Missing series was displayed as zero | Check presence explicitly; the local summing helper is not a production missing-data policy |

Diagnose transport response, content negotiation, parsing and metric meaning separately. A valid 200 response is necessary but not sufficient evidence of correct instrumentation.

## 18. Knowledge Check

1. What identifies one time series?
2. Does HELP count an event?
3. How do instrument observation and stored metric sample differ?
4. Can five requests produce only one later scraped sample?
5. Why is changing a Content-Type header alone insufficient?
6. Does OpenMetrics imply native histograms?
7. Why must cumulative buckets not be summed as traffic?
8. Does a missing child necessarily mean instrumentation is disabled?
9. Can process CPU change during repeated metrics reads?
10. Why are counter differences unsafe across a restart?

### Answer Guide

1. Metric name and complete label set.
2. No; it is descriptive metadata.
3. An observation updates state; a stored sample captures numeric state at a sample time.
4. Yes; the sample can reflect their accumulated effect.
5. The body must use the corresponding encoder and format.
6. No; this implementation retains classic histogram series.
7. One observation increments multiple buckets.
8. No; a labelled child may not have been instantiated yet.
9. Yes; serving exposition itself performs work.
10. The process registry can reset; a raw subtraction cannot reconstruct the lost boundary.

## 19. Professional Scenario Exercise

A colleague says the metric counter contains every request event because its value is 500. Explain what evidence the number supplies, which request details it cannot recover, and what reset, sampling and retention assumptions are needed before comparing it with 500 log lines.

## 20. Lab Notebook Template

Create a notebook in the evidence directory for this run:

```bash
test -e "$LAB_DIR/notebook.md" || printf '# Lab 07 Evidence\n' > "$LAB_DIR/notebook.md"
```

Use these headings and fill them with your predictions, commands, observed results and conclusions:

```markdown
# Lab 07 Evidence

## Starting state and original Content-Type
## Observation versus event record
## Family/sample/type inventory
## Negotiation implementation and tests
## Plain versus OpenMetrics evidence
## Five-request prediction and delta
## Matching log evidence
## Scrape exclusion proof
## Histogram component interpretation
## Zero versus missing
## Recovery and remaining uncertainty
```

## 21. Observable Completion Criteria

- [ ] The original wire format was observed before editing.
- [ ] Both legacy text and OpenMetrics now parse with their matching parsers.
- [ ] Content-Type, Vary and EOF behavior are verified.
- [ ] Tests prove scrapes do not create request events.
- [ ] Five isolated requests produce a delta of five.
- [ ] Logs preserve request identities absent from the metric samples.
- [ ] Histogram bucket/count/sum semantics are explained correctly.
- [ ] Missing samples are distinguished from observed zero.
- [ ] Only the baseline services are running and the checkpoint survives.

## 22. Production Implications

Exposition is an API contract consumed by scrapers. Format negotiation must be tested, labels must remain bounded, and process-local state must be interpreted with lifecycle context. Raw snapshots are useful for controlled experiments but do not replace a time-series database, and cumulative counters do not become durable event ledgers.

## 23. End State and Transition

Keep `lab-notes/metric_snapshot.py` and `lab-notes/raw-metrics.sh`. The negotiated endpoint remains enabled and all telemetry backends remain stopped.

Next: [Lab 08 — Instrument RED Metrics](Lab-8.md). You will make request/error/duration ownership explicit and validate success, failure and in-progress behavior at the instrumentation boundary.
