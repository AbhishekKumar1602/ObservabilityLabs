# Lab 36: Custom Spans, Events, Attributes, Status, and Redaction

## Purpose and Scope

> **Primary Objective:** Add safe diagnostic meaning to the two-service trace, prove its parent tree, and verify redaction in stored telemetry.

Lab 35 established context propagation. A framework span can show an HTTP call without explaining the operation it serves. This lab adds two stable application spans, bounded diagnostic attributes and timed events to that existing call.

    You will implement normal, slow and intentionally failed scenarios, compare a span event with an independently emitted log, and test a Collector privacy rule using a harmless sentinel. This is a local learning route, not an authentication or public fault-injection API. CRUD, database transactions and cache behavior remain the earlier implementation. Sampling, search and Collector outage experiments follow in Labs 37–40.

## 1. Inherited State and Starting Checks

Complete [Lab 35](Lab-35.md) first. Run from the repository root in one Bash session; keep the existing credentials, named volumes, checkpoint item, dashboards and earlier evidence.

```bash
source lab-notes/session.sh
source lab-notes/evidence.sh
source lab-notes/metrics-session.sh
source lab-notes/platform-session.sh
source lab-notes/traces-session.sh
load_app_settings
start_lab 36
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
unset LAB_TRUST_TRACE_CONTEXT
cp app/app/downstream.py "$LAB_DIR/downstream.py.before"
cp app/app/downstream_client.py "$LAB_DIR/downstream_client.py.before"
cp lab-notes/tracing/collector.yml "$LAB_DIR/collector.yml.before"
cp lab-notes/tracing/inspect_trace.py "$LAB_DIR/inspect_trace.py.before"
cp lab-notes/tracing/verify_boundary.py "$LAB_DIR/verify_boundary.py.before"
dp exec -T app python - <<'CHECK'
from app.config import Settings
s=Settings()
assert s.otel_enabled and s.demo_enabled and s.trace_sample_ratio == 1.0
assert not s.pyroscope_enabled
print('Learning route and full head sampling enabled')
CHECK
```

Do not continue with Lab 35’s context-stripping fault active. The new client checks that upstream and downstream trace IDs agree. If the check fails, restore the downstream container with `dp up -d --no-deps --force-recreate lab-downstream` before investigating custom spans.

## 2. Learning Objectives and Design Predictions

Predict the effect of each signal before editing code:

| Element | Purpose here | Value discipline |
|---|---|---|
| `demo.evaluate` span | Time the complete upstream operation | Stable name; no request ID in the name |
| `demo.compute` span | Time the downstream work | One span per operation, not per loop iteration |
| `lab.scenario` attribute | Select normal, slow or error work | Exactly three values |
| `app.outcome` attribute | Explain success or failure | `success` or `failed` |
| Span event | Locate a meaningful instant inside a span | Fixed event name and safe attributes |
| Span status | Mark failed operation for trace search/policies | `ERROR` on failure; success can stay `UNSET` |
| JSON log | Independently record operation completion | Correlation IDs, no payload or credentials |

The new parent chain is upstream SERVER → `demo.evaluate` INTERNAL → HTTPX CLIENT → downstream SERVER → `demo.compute` INTERNAL. The two custom spans must share the same trace ID. Their IDs differ because they are distinct operations.

**Prediction checkpoint:** the slow scenario should add about 250 ms of downstream waiting; the error scenario should return downstream 503 and upstream 502. An error event alone would not set span status. Marking a span `ERROR` alone would not write a log. No real secret is needed to test redaction.

## 3. Implement the Upstream Operation

```bash
cat > app/app/downstream_client.py <<'PYTHON'
"""Bounded learning scenarios; one pooled, instrumented HTTP client per process."""
import logging
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, Response
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.metrics import NoOpMeterProvider
from opentelemetry.trace import Status, StatusCode

router = APIRouter(prefix='/api/v1/demo', tags=['learning'])
logger = logging.getLogger(__name__)
Scenario = Literal['normal', 'slow', 'error']


async def start_downstream(app) -> None:
    if not app.state.settings.demo_enabled:
        return
    client = httpx.AsyncClient(base_url='http://lab-downstream:8001', trust_env=False,
        timeout=httpx.Timeout(2.0, connect=0.5),
        limits=httpx.Limits(max_connections=10, max_keepalive_connections=5))
    provider = app.state.telemetry.provider
    if provider is not None:
        HTTPXClientInstrumentor.instrument_client(client, tracer_provider=provider,
                                                 meter_provider=NoOpMeterProvider())
    app.state.downstream_client = client


async def stop_downstream(app) -> None:
    client = getattr(app.state, 'downstream_client', None)
    if client is not None:
        HTTPXClientInstrumentor.uninstrument_client(client)
        await client.aclose()


@router.get('/downstream')
async def call_downstream(request: Request, response: Response,
        value: Annotated[int, Query(ge=0, le=100)] = 7,
        scenario: Scenario = 'normal') -> dict[str, object]:
    if not request.app.state.settings.demo_enabled:
        raise HTTPException(404, 'Not found')
    with request.app.state.tracer.start_as_current_span('demo.evaluate', attributes={
        'app.operation': 'evaluate', 'lab.scenario': scenario,
        'lab.secret_probe': 'LAB_ONLY_REMOVE_ME_36'},
        record_exception=False, set_status_on_exception=False) as span:
        span.add_event('downstream.requested', {'app.operation': 'evaluate'})
        result: dict[str, object] | None = None
        reason = 'none'
        status = 200
        try:
            downstream_response = await request.app.state.downstream_client.get(
                '/evaluate', params={'value': value, 'scenario': scenario},
                headers={'X-Request-ID': request.state.request_id})
            result = downstream_response.json()
            if not isinstance(result, dict):
                raise ValueError('Expected object')
            if downstream_response.status_code != 200:
                status, reason = 502, 'dependency_rejected'
        except httpx.TimeoutException:
            status, reason = 504, 'dependency_timeout'
        except (httpx.HTTPError, ValueError):
            status, reason = 502, 'dependency_unavailable'
        outcome = 'success' if status == 200 else 'failed'
        span.set_attribute('app.outcome', outcome)
        span.add_event('downstream.received', {'app.outcome': outcome, 'app.reason': reason})
        if status != 200:
            span.set_status(Status(StatusCode.ERROR))
        context = span.get_span_context()
        # Log while the custom span is current. No response body or exception string is logged.
        logger.log(logging.INFO if status == 200 else logging.WARNING,
            'demo_operation_completed', extra={'operation': 'evaluate', 'http.status_code': status})
        response.status_code = status
        return {'request_id': request.state.request_id,
            'upstream_trace_id': f'{context.trace_id:032x}',
            'trace_sampled': context.trace_flags.sampled, 'operation_recording': span.is_recording(),
            'scenario': scenario, 'outcome': outcome, 'downstream': result}
PYTHON
```

This replaces the Lab 35 module while retaining its lifecycle function names, pooled client and router. `main.py` already calls those functions; do not install a second client or instrumentation layer. The tracer comes from `app.state.tracer`, which uses the application's private provider. A module-level global tracer would risk using an unconfigured provider in this repository.

`record_exception=False` and `set_status_on_exception=False` prevent this custom span context manager from automatically copying raw exception details. Expected failures are caught and converted into a fixed status and reason. Existing automatic instrumentation and the Collector privacy filter still need review; these two flags are not a universal redaction mechanism. Success stays `UNSET`, allowing a later failure to set `ERROR`.

The local response includes the trace ID and sampling flags for later experiments. They describe SDK context and recording decisions, not proof of Tempo storage. The `lab.secret_probe` value is a public, artificial string; never replace it with an actual credential.

## 4. Implement the Downstream Operation

```bash
cat > app/app/downstream.py <<'PYTHON'
"""Internal two-service trace exercise; no database, credentials, or public port."""
import asyncio
import json
import socket
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from time import perf_counter
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import FastAPI, Query, Request, Response
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.metrics import NoOpMeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import Status, StatusCode
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.types import ASGIApp, Receive, Scope, Send

from app.logging_config import configure_logging
from app.middleware import SAFE_ID


class DownstreamSettings(BaseSettings):
    model_config = SettingsConfigDict(extra='ignore')
    service_name: Literal['lab-downstream'] = 'lab-downstream'
    environment: Literal['local', 'test'] = 'local'
    trust_trace_context: bool = True
    log_level: Literal['INFO', 'WARNING', 'ERROR'] = 'INFO'


class ContextBoundary:
    def __init__(self, app: ASGIApp, enabled: bool) -> None:
        self.app, self.enabled = app, enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] == 'http' and not self.enabled:
            scope = dict(scope)
            scope['headers'] = [(key, value) for key, value in scope.get('headers', [])
                                if key.lower() not in {b'traceparent', b'tracestate'}]
        await self.app(scope, receive, send)


def create_app() -> ASGIApp:
    settings = DownstreamSettings()
    configure_logging(settings)
    provider = TracerProvider(resource=Resource.create({
        'service.name': settings.service_name, 'service.version': '1.0.0',
        'deployment.environment.name': settings.environment, 'service.instance.id': socket.gethostname()}),
        sampler=ParentBased(TraceIdRatioBased(1.0)))
    provider.add_span_processor(BatchSpanProcessor(
        OTLPSpanExporter(endpoint='http://otel-collector:4317', insecure=True, timeout=3),
        max_queue_size=1024, schedule_delay_millis=1000))
    tracer = provider.get_tracer('lab.downstream', '1.0.0')

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            await asyncio.to_thread(provider.shutdown)

    app = FastAPI(title='Trace context learning service', lifespan=lifespan)

    @app.get('/health/live')
    async def live() -> dict[str, str]:
        return {'status': 'alive'}

    @app.get('/evaluate')
    async def evaluate(request: Request, response: Response,
            value: Annotated[int, Query(ge=0, le=100)] = 7,
            scenario: Literal['normal', 'slow', 'error'] = 'normal') -> dict[str, object]:
        started = perf_counter()
        values = request.headers.getlist('x-request-id')
        supplied = values[0] if len(values) == 1 else ''
        request_id = supplied if SAFE_ID.fullmatch(supplied) else str(uuid4())
        status = 503 if scenario == 'error' else 200
        with tracer.start_as_current_span('demo.compute', attributes={
            'app.operation': 'double', 'lab.scenario': scenario},
            record_exception=False, set_status_on_exception=False) as span:
            span.add_event('work.started', {'app.stage': 'compute',
                                          'lab.secret_probe': 'LAB_ONLY_REMOVE_ME_36'})
            await asyncio.sleep(0.25 if scenario == 'slow' else 0.025)
            outcome = 'failed' if status == 503 else 'success'
            span.set_attribute('app.outcome', outcome)
            if status == 503:
                span.set_status(Status(StatusCode.ERROR))
                span.add_event('work.rejected', {'app.reason': 'controlled_failure'})
            else:
                span.add_event('work.completed', {'app.stage': 'compute'})
        # This completion log belongs to the enclosing server span, not the ended compute span.
        context = trace.get_current_span().get_span_context()
        trace_id = f'{context.trace_id:032x}'
        response.status_code = status
        response.headers['X-Request-ID'] = request_id
        print(json.dumps({
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'level': 'WARNING' if status == 503 else 'INFO',
            'logger': 'lab.downstream', 'service': settings.service_name,
            'environment': settings.environment, 'message': 'request_completed',
            'event_name': 'request_completed', 'event_id': str(uuid4()), 'schema_version': 1,
            'request_id': request_id, 'http.method': 'GET', 'http.route': '/evaluate',
            'http.status_code': status, 'duration_ms': round((perf_counter()-started)*1000, 3),
            'trace_id': trace_id, 'span_id': f'{context.span_id:016x}',
            'trace_sampled': context.trace_flags.sampled}, separators=(',', ':')), flush=True)
        return {'service': settings.service_name, 'result': value * 2 if status == 200 else None,
                'trace_id': trace_id, 'trace_sampled': context.trace_flags.sampled}

    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider, meter_provider=NoOpMeterProvider(),
        excluded_urls=r'.*/health/live$', exclude_spans=['receive', 'send'])
    return ContextBoundary(app, settings.trust_trace_context)
PYTHON
```

The previous arbitrary `delay_ms` diagnostic parameter is replaced by the bounded `scenario` enum; use `scenario=slow` for the controlled delay. `value` remains bounded to 0–100. This keeps repeatable experiments small and prevents a caller from requesting an indefinite sleep.

`work.rejected` is a span event with a safe reason, not a Python exception dump. The downstream completion log is emitted after the compute span closes, so its span ID points to the enclosing server span. The upstream operation log is emitted while `demo.evaluate` is current. Both are valid choices if documented.

The shared formatter may classify `demo_operation_completed` as `application_log` under its existing event-name allowlist. Search the JSON `message` for that log; do not invent a new indexed Loki label. The explicit downstream completion record preserves the established `request_completed` event contract.

## 5. Extend Redaction Before Export

```bash
cat > lab-notes/tracing/redact36.py <<'PYTHON'
"""Extend existing privacy filters; preserve every unrelated pipeline and policy."""
from pathlib import Path
import yaml

path = Path('lab-notes/tracing/collector.yml')
config = yaml.safe_load(path.read_text())
statements = config['processors']['transform/traces']['trace_statements']
for context, rule in [
    ('span', 'delete_key(span.attributes, "lab.secret_probe")'),
    ('span', 'delete_key(resource.attributes, "lab.secret_probe")'),
    ('spanevent', 'delete_key(spanevent.attributes, "lab.secret_probe")')]:
    group = next((g for g in statements if g['context'] == context), None)
    if group is None:
        group = {'context': context, 'statements': []}
        statements.append(group)
    if rule not in group['statements']:
        group['statements'].append(rule)
path.write_text(yaml.safe_dump(config, sort_keys=False))
PYTHON
```

```bash
lab-notes/.tools/bin/python lab-notes/tracing/redact36.py
dp run --rm -T --no-deps otel-collector validate --config=/etc/otelcol-contrib/config.yaml
dp up -d --no-deps --force-recreate otel-collector
wait_backend otel-collector:13133 /
dp build app lab-downstream
dp up -d --no-deps --force-recreate app lab-downstream
wait_ready
wait_backend lab-downstream:8001 /health/live
```

The installer adds deletion rules at resource, span and span-event scopes without replacing earlier filters for SQL text, full URLs or exception messages. Redaction runs before export. Editing a bind-mounted file alone does not reload Collector configuration; validation followed by recreation makes the change effective.

This is defense in depth. Data already existed in the SDK and crossed the app-to-Collector connection. The primary control is to avoid capturing sensitive data at the source. The trace transform does not sanitize independent JSON log bodies; verify the logging path separately. Existing Tempo data is not retroactively rewritten.

## 6. Upgrade the Evidence Helpers for Nested Spans and Events

```bash
cat > lab-notes/tracing/inspect_trace.py <<'PYTHON'
"""Normalize Tempo OTLP JSON, preserving the keys used by Labs 33-35."""
import base64
import json
import re
import sys
from pathlib import Path


def hex_id(value, size):
    if not value:
        return ''
    if re.fullmatch('[0-9a-fA-F]{'+str(size*2)+'}', value):
        return value.lower()
    decoded = base64.b64decode(value, validate=True)
    assert len(decoded) == size
    return decoded.hex()


def decode(value):
    key, result = next(iter(value.items()))
    if key == 'arrayValue':
        return [decode(item) for item in result.get('values', [])]
    if key == 'kvlistValue':
        return attrs(result.get('values', []))
    return int(result) if key == 'intValue' else result


def attrs(values):
    return {item['key']: decode(item['value']) for item in values}


def flatten(document):
    output = []
    for batch in document.get('batches', document.get('resourceSpans', [])):
        resource = attrs(batch.get('resource', {}).get('attributes', []))
        for scope in batch.get('scopeSpans', batch.get('instrumentationLibrarySpans', [])):
            scope_name = scope.get('scope', scope.get('instrumentationLibrary', {})).get('name', '')
            for span in scope.get('spans', []):
                start, end = int(span['startTimeUnixNano']), int(span['endTimeUnixNano'])
                output.append({'service': resource.get('service.name'),
                    'environment': resource.get('deployment.environment.name'), 'resource': resource,
                    'scope': scope_name, 'name': span['name'], 'kind': span.get('kind'),
                    'trace_id': hex_id(span['traceId'], 16), 'span_id': hex_id(span['spanId'], 8),
                    'parent_span_id': hex_id(span.get('parentSpanId', ''), 8),
                    'start_ns': start, 'end_ns': end, 'duration_ms': (end-start)/1e6,
                    'status': span.get('status', {}), 'attributes': attrs(span.get('attributes', [])),
                    'events': [{'name': e['name'], 'time_ns': int(e.get('timeUnixNano', 0)),
                                'attributes': attrs(e.get('attributes', []))}
                               for e in span.get('events', [])]})
    return output


if __name__ == '__main__':
    rows = flatten(json.loads(Path(sys.argv[1]).read_text()))
    assert rows, 'No spans: inspect the raw response and retry the bounded lookup'
    print(json.dumps(rows, indent=2))
PYTHON
```

```bash
cat > lab-notes/tracing/verify_boundary.py <<'PYTHON'
"""Prove remote parentage, allowing INTERNAL spans between server and HTTP client."""
import json
import sys
from pathlib import Path
from inspect_trace import flatten

rows = flatten(json.loads(Path(sys.argv[1]).read_text()))
by_id = {row['span_id']: row for row in rows}
children = [r for r in rows if r['service'] == 'lab-downstream' and r['kind'] in (2, 'SPAN_KIND_SERVER')]
assert len(children) == 1, 'Expected exactly one downstream server'
child = children[0]
parent = by_id[child['parent_span_id']]
assert parent['kind'] in (3, 'SPAN_KIND_CLIENT') and parent['service'] != 'lab-downstream'
assert parent['trace_id'] == child['trace_id']
ancestor, seen = parent, set()
while ancestor['kind'] not in (2, 'SPAN_KIND_SERVER'):
    assert ancestor['span_id'] not in seen, 'Parent cycle'
    seen.add(ancestor['span_id'])
    ancestor = by_id[ancestor['parent_span_id']]
assert ancestor['service'] == parent['service'] and ancestor['trace_id'] == child['trace_id']
print(json.dumps({'trace_id': child['trace_id'], 'caller_client_span': parent['span_id'],
    'downstream_server_span': child['span_id'], 'upstream_server_span': ancestor['span_id'],
    'parent_edge_valid': True}, indent=2))
PYTHON
```

The normalizer preserves the Lab 33 keys and adds resource data, timestamps and events. The parent verifier now walks through INTERNAL ancestors to find the upstream SERVER. The Lab 35 assertion that the CLIENT was a direct child of the SERVER is no longer appropriate after intentionally adding `demo.evaluate`; the remote CLIENT-to-SERVER edge must still be exact.

Missing ancestors immediately after lookup can mean partial arrival from independent SDK batches. Retry a bounded lookup and validate the full tree before concluding that context propagation failed.

## 7. Generate a Bounded, Reusable Workload

```bash
cat > lab-notes/tracing/scenario_load.py <<'PYTHON'
"""Bounded standard-library caller. No traceparent: every request starts a new root."""
import argparse
import json
import time
import uuid
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

parser = argparse.ArgumentParser()
parser.add_argument('base_url')
parser.add_argument('output')
parser.add_argument('--count', type=int, default=12)
parser.add_argument('--only', choices=['normal', 'slow', 'error'])
args = parser.parse_args()
assert 1 <= args.count <= 90, 'Bounded experiment: 1-90 requests'
opener = build_opener(ProxyHandler({}))
with open(args.output, 'x', encoding='utf-8') as output:
    for number in range(args.count):
        scenario = args.only or ('normal', 'slow', 'error')[number % 3]
        request_id = 'scenario-' + str(uuid.uuid4())
        url = args.base_url.rstrip('/') + '/api/v1/demo/downstream?' + urlencode({'scenario': scenario})
        request = Request(url, headers={'X-Request-ID': request_id})
        start = time.time_ns()
        try:
            response = opener.open(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            status = response.status
            document = json.load(response)
        row = {'request_id': request_id, 'scenario': scenario, 'status': status,
               'start_ns': start, 'end_ns': time.time_ns(), 'response': document}
        output.write(json.dumps(row, separators=(',', ':')) + '\n')
        output.flush()
        expected = 502 if scenario == 'error' else 200
        assert status == expected, (scenario, status, document)
        assert document['request_id'] == request_id
        assert document['downstream']['trace_id'] == document['upstream_trace_id']
        assert document['downstream']['trace_sampled'] == document['trace_sampled']
        time.sleep(0.05)
print(json.dumps({'requests': args.count, 'file': args.output, 'complete': True}))
PYTHON
```

```bash
python3 lab-notes/tracing/scenario_load.py "$APP_URL" "$LAB_DIR/scenarios.jsonl" --count 9
jq -s 'group_by(.scenario) | map({scenario:.[0].scenario,requests:length,statuses:map(.status)|unique})' "$LAB_DIR/scenarios.jsonl"
```

Expect three requests per scenario, HTTP 200 for normal/slow and 502 for the intentionally failed dependency. The generator accepts those expected failures instead of aborting as `curl -f` would. Any unexpected status or broken propagation stops the run and retains its partial ledger.

The file is created exclusively: rerunning with the same path fails rather than overwriting evidence. Choose a new filename for another run. The caller supplies only a validated request ID, not `traceparent`; this distinction matters for head sampling in Lab 38. Ninety requests is the enforced ceiling, not the recommended default.

## 8. Inspect Events, Status and the Stored Redaction Result

```bash
TRACE_ID=$(jq -rs '[.[] | select(.scenario=="error")][0].response.upstream_trace_id' "$LAB_DIR/scenarios.jsonl")
for attempt in {1..8}; do
  fetch_trace "$TRACE_ID" "$LAB_DIR/error-trace.json"
  if python3 lab-notes/tracing/verify_boundary.py "$LAB_DIR/error-trace.json" > "$LAB_DIR/parent-proof.json"; then break; fi
  sleep 1
done
python3 lab-notes/tracing/verify_boundary.py "$LAB_DIR/error-trace.json"
python3 lab-notes/tracing/inspect_trace.py "$LAB_DIR/error-trace.json" > "$LAB_DIR/error-spans.json"
jq '.[] | select(.name=="demo.evaluate" or .name=="demo.compute") | {service,name,attributes,status,events}' "$LAB_DIR/error-spans.json"
python3 - "$LAB_DIR/error-trace.json" "$LAB_DIR/error-spans.json" <<'CHECK'
import json,sys
from pathlib import Path
raw=Path(sys.argv[1]).read_text(); spans=json.loads(Path(sys.argv[2]).read_text())
assert 'LAB_ONLY_REMOVE_ME_36' not in raw and 'lab.secret_probe' not in raw
custom=[s for s in spans if s['name'] in {'demo.evaluate','demo.compute'}]
assert len(custom)==2
assert all(s['status'].get('code') in (2,'STATUS_CODE_ERROR') for s in custom)
assert all(s['attributes']['app.outcome']=='failed' for s in custom)
assert any(e['name']=='work.rejected' for s in custom for e in s['events'])
print('Custom status, event and stored redaction verified')
CHECK
```

In Grafana Explore, select Tempo and open the trace ID. Expand both custom spans and their events. Then select Loki and run the following query, replacing the literal example trace ID with the known ID:

```logql
{service_name=~"fastapi-items|lab-downstream", deployment_environment_name="local"}
  | json | trace_id="0123456789abcdef0123456789abcdef"
```

If your configured service/environment differ, use `$LAB_SERVICE` and `$LAB_ENVIRONMENT` as their actual values. The labels above are the normalized OTel resource labels established in the logging labs. `trace_id` is parsed from JSON at query time, not an indexed label. Use the existing derived field to move from a completion log to Tempo.

Find the upstream `demo_operation_completed` message and downstream `request_completed` record. You should not find `work.rejected` as an independently shipped log solely because it is a span event. Compare the trace's ERROR statuses with the recorded 502/503 statuses and the application's native HTTP metrics. These are separate signals describing the same controlled outcome.

## 9. Recovery and Troubleshooting

The normal end state retains the new scenario route and redaction rules. Send a healthy canary:

```bash
api -fsS "$APP_URL/api/v1/demo/downstream?scenario=normal" | jq .
wait_ready
dp logs --since=3m --tail=100 --no-color app lab-downstream otel-collector > "$LAB_DIR/component-logs.txt"
```

| Symptom | Evidence and next action |
|---|---|
| Custom spans absent | Confirm sampling 1.0, active provider, rebuilt image and complete trace arrival. |
| Different trace IDs | Check `LAB_TRUST_TRACE_CONTEXT`, instrumentation ownership and the downstream container's recreated environment. |
| Events visible but no Loki event record | Expected: span events are part of trace export, not automatic log export. |
| Error response but custom status unset | Inspect explicit `set_status`; catching an exception does not automatically mark the custom span. |
| Sentinel survives | Check active bind mount, transform scope/order and recreation; stop generating further probe traces until fixed. |
| Route returns 422 | Use only the three allowed scenarios and a numeric value in range. |
| Known trace missing | Inspect SDK export warnings, Collector export and Tempo readiness before changing the code. |

If you must undo this lab, restore the five `*.before` files to their original paths, validate/recreate the Collector and rebuild/recreate both app services. Do not delete named volumes to undo a code or configuration change. Stop before Lab 37 if you roll back: the following labs require the custom operation schema.

## 10. Knowledge Check

1. Why use the application-owned tracer rather than a new global provider?
2. Does adding an exception event necessarily set ERROR or create a log?
3. Why can a completion log point to a different span than the work event?
4. What does Collector-side redaction fail to protect?

### Answer Guide

1. It preserves the configured resource, sampler and export ownership without creating a second provider.
2. No. Status, span events and independent log emission are separate actions.
3. The active span changes as nested scopes open and close; both can share the same trace ID.
4. It cannot undo capture at the source, transit before the Collector, another pipeline or already stored data.

## 11. Professional Scenario Exercise

A reviewer suggests putting each item ID in the span name and recording every HTTP response body as an event. Write a safer design using fixed operation names, bounded outcomes and allowlisted diagnostic fields. Prove one failure using its request ID, parent tree, span status and completion log; include why a real password is unnecessary for a redaction test.

## 12. Lab Notebook Template

Create a notebook in the evidence directory for this run:

```bash
test -e "$LAB_DIR/notebook.md" || printf '# Lab 36 Evidence\n' > "$LAB_DIR/notebook.md"
```

Use these headings and fill them with your predictions, commands, observed results and conclusions:

```markdown
# Lab 36 Evidence

## Prediction and starting state
## Configuration and bounded workload
## Evidence with timestamps and identifiers
## Explanation and competing hypotheses
## Recovery proof
## Production decision and remaining uncertainty
```

## 13. Observable Completion Criteria

- [ ] Three scenarios execute with expected statuses and matching cross-service trace IDs.
- [ ] Tempo shows the five-span chain including two custom INTERNAL spans.
- [ ] Error spans carry explicit ERROR status and the expected safe event.
- [ ] The harmless sentinel is absent from the stored resource/span/event payload.
- [ ] Completion logs correlate with traces; events and logs are distinguished.
- [ ] Normal readiness and the existing metrics/log pipelines remain healthy.

## 14. Production Implications

Instrument meaningful boundaries and budget attribute/event counts. Span attributes do not automatically create Prometheus time series, but unbounded values still increase privacy and storage costs and can become labels in later derived metrics. Keep this fault route restricted to learning environments. Review automatic instrumentation separately from custom code. See the [Python tracing API](https://opentelemetry-python.readthedocs.io/en/latest/api/trace.html) and [Collector transform processor](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/v0.160.0/processor/transformprocessor).

## 15. End State and Transition

Keep the scenario route, updated evidence helpers and redaction rules. Head sampling remains 1.0 and no tail sampler is active. [Lab 37](Lab-37.md) searches these known operations and explains their critical path.
