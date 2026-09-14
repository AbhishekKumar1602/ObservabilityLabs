# Lab 34: Automatic FastAPI, SQLAlchemy, and Redis Instrumentation

## Purpose and Scope

> **Primary Objective:** Enable the existing framework and dependency instrumentation, add supported HTTPX instrumentation, and explain different trace shapes for a cache miss and hit.

Automatic instrumentation observes framework or client-library operations without adding span code to every route. It does not automatically understand business intent, and it should not duplicate an existing instrumentation layer.

This lab enables the repository's existing `Telemetry` implementation and instruments a real HTTPX client. You will compare a cold and warm GET for one lab-owned item, locate framework/dependency scopes, verify privacy filters and correlate completion logs with trace IDs. No new downstream service is introduced until Lab 35; the HTTPX probe here runs as a short-lived client process.

## 1. Inherited State and Starting Checks

Complete [Lab 33](Lab-33.md) first. Run every command from the repository root in the same Bash session. Retain the credentials, named volumes and existing application checkpoint item. 

```bash
source lab-notes/session.sh
source lab-notes/evidence.sh
source lab-notes/metrics-session.sh
source lab-notes/platform-session.sh
load_app_settings
start_lab 34
dp config --quiet
dp ps -a
wait_ready
wait_backend loki:3100 /ready
wait_backend otel-collector:13133 /
```

`dp` is the stage-aware Compose helper introduced in Lab 31. It reads the same project and named volumes, adding only the overlays that exist. Use it throughout these labs: running plain `docker compose up` would enable the original full-stack settings instead of the learning stage. `start_lab` creates a fresh evidence directory and sets `LAB_DIR`; keep that directory for this run.

```bash
source lab-notes/traces-session.sh
wait_backend tempo:3200 /ready
cp app/requirements.in "$LAB_DIR/requirements.in.before"
cp app/requirements-dev.in "$LAB_DIR/requirements-dev.in.before"
cp app/requirements.txt "$LAB_DIR/requirements.txt.before"
cp app/requirements-dev.txt "$LAB_DIR/requirements-dev.txt.before"
cp app/app/telemetry.py "$LAB_DIR/telemetry.py.before"
```

Twelve services and nine scrape jobs should be active. Review `app/app/telemetry.py`: it already creates a resource and provider, a bounded batch processor, FastAPI instrumentation, SQLAlchemy instrumentation on `engine.sync_engine`, and Redis instrumentation on the pooled client. It passes a no-op meter provider where supported so Prometheus remains the owner of application metrics.

## 2. Learning Objectives and Predicted Trace Shapes

You will distinguish server/client/internal spans, recognize instrumentation scopes, explain why an async SQLAlchemy engine instruments its synchronous proxy, and compare dependency calls without treating every slow HTTP request as slow SQL.

| Layer | Cold GET | Warm GET |
|---|---|---|
| Probe's instrumented HTTPX client | HTTP client span | HTTP client span |
| FastAPI | Server span | Server span |
| Existing `items.get` block | Internal application span | Internal application span |
| Redis | Cache lookup; normally cache population after a miss | Cache lookup |
| PostgreSQL/SQLAlchemy | Item lookup on a miss | Normally no item query |

**Prediction checkpoint:** the warm request should return the same item, have a Redis lookup, and avoid the item database read. Its latency may be lower, but two requests are not statistically reliable latency evidence. Trace structure is the primary comparison.

The existing Items custom spans predate these labs. You are inspecting them, not extending business-span design; that is Lab 36. Excluding health URLs from FastAPI server instrumentation does not necessarily suppress separate Redis/SQL spans created by background probes. Compare the two known trace IDs rather than counting every span in the backend.

## 3. Add the HTTPX Runtime Integration and Regenerate Locks

```bash
python3 - <<'PYTHON'
from pathlib import Path
p=Path('app/requirements.in'); text=p.read_text()
for requirement in ['httpx==0.28.1','opentelemetry-instrumentation-httpx==0.65b0']:
    if requirement not in text.splitlines(): text += '\n'+requirement+'\n'
p.write_text(text)
PYTHON
docker run --rm --user "$(id -u):$(id -g)"   -v "$PWD/app:/work" -w /work python:3.12.14-slim-bookworm   sh -ec 'python -m venv /tmp/locks && /tmp/locks/bin/pip install --no-cache-dir pip-tools==7.5.0 && /tmp/locks/bin/pip-compile --generate-hashes --resolver=backtracking --output-file=requirements.txt requirements.in && /tmp/locks/bin/pip-compile --generate-hashes --resolver=backtracking --output-file=requirements-dev.txt requirements-dev.in'
```

HTTPX was previously a test dependency. It becomes a runtime dependency because the next lab makes a real outbound application call. The instrumentation pin matches the repository's 0.65b0 family; do not install a random latest version beside SDK 1.44.0. Regenerating both lock files retains the Dockerfile's hash-checked installation. The temporary lock container has no application credentials and writes the mounted files as your host UID.

Review the dependency diff before building. If your package index cannot supply a pin, stop at this compatibility gate and verify the available release family; do not bypass `--require-hashes`, copy invented hashes, or silently mix instrumentation versions.

## 4. Enable One Instrumentation Owner

```bash
cat > lab-notes/compose.auto-tracing.yaml <<'YAML'
services:
  app:
    environment:
      OTEL_ENABLED: "true"
      OTEL_EXPORTER_OTLP_ENDPOINT: http://otel-collector:4317
      TRACE_SAMPLE_RATIO: "1.0"
      PYROSCOPE_ENABLED: "false"
      OTEL_PROPAGATORS: tracecontext
YAML
```

```bash
dp config --quiet
dp build app
dp up -d --no-deps --force-recreate app
wait_ready
dp exec -T app python - <<'PYTHON'
from importlib.metadata import version
from app.config import Settings
s=Settings()
assert s.otel_enabled and s.trace_sample_ratio==1.0 and not s.pyroscope_enabled
for name, expected in [('opentelemetry-sdk','1.44.0'),('opentelemetry-instrumentation-httpx','0.65b0'),('httpx','0.28.1')]:
    actual=version(name); print(name,actual); assert actual==expected
print('Tracing enabled; profiles remain disabled')
PYTHON
```

A recreate is required because `restart` does not reread changed container environment. One process still runs one Uvicorn worker. Preserve the app's ordinary readiness behavior; Tempo is not a required business dependency.

Do not additionally wrap Uvicorn in `opentelemetry-instrument`. The code already owns instrumentation and provider creation. Double wrapping can create duplicate spans, conflicting providers and confusing metrics. `ParentBased(TraceIdRatioBased(1.0))` keeps these small learning traces; the repository still supports configurable sampling for later labs.

## 5. Create One Item and Force One Cache Miss

```bash
api -fsS -X POST "$APP_URL/api/v1/items" -H 'Content-Type: application/json'   -d '{"name":"lab34-trace-item","description":"temporary tracing comparison"}'   > "$LAB_DIR/item.json"
TRACE_ITEM_ID=$(jq -er '.id' "$LAB_DIR/item.json")
TRACE_CACHE_KEY=$(cache_key "$TRACE_ITEM_ID")
rcli DEL "$TRACE_CACHE_KEY"
```

`DEL` targets exactly the lab-owned item's namespaced cache key. It does not remove PostgreSQL state or flush other users' cached data. Creation may already have invalidated the key, so Redis can return 0; either 0 or 1 is consistent with the next GET being a miss.

The existing cache TTL is short. Run the next two reads together before expiration. Avoid a concurrent client updating/deleting this item.

## 6. Generate Two Automatically Instrumented Client Requests

```bash
cat > lab-notes/tracing/trace_client.py <<'PYTHON'
"""Instrument one real HTTPX client. One output row per request, no sensitive response bodies."""
import argparse, json, os, socket, uuid
import httpx
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.metrics import NoOpMeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

parser=argparse.ArgumentParser()
parser.add_argument('path'); parser.add_argument('--count', type=int, default=1)
args=parser.parse_args()
assert args.path.startswith('/api/v1/') and 1 <= args.count <= 5
provider=TracerProvider(resource=Resource.create({
    'service.name':'lab-http-client','service.version':'1.0.0',
    'deployment.environment.name':os.environ.get('ENVIRONMENT','local'),'service.instance.id':socket.gethostname()}),
    sampler=ParentBased(TraceIdRatioBased(1.0)))
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint='http://otel-collector:4317',insecure=True,timeout=3),schedule_delay_millis=500))
context={}
def request_hook(span, request):
    context['trace_id']=f'{span.get_span_context().trace_id:032x}'
    context['client_span_id']=f'{span.get_span_context().span_id:016x}'
try:
    with httpx.Client(base_url='http://app:8000', timeout=5, trust_env=False) as client:
        HTTPXClientInstrumentor.instrument_client(client,tracer_provider=provider,meter_provider=NoOpMeterProvider(),request_hook=request_hook)
        for _ in range(args.count):
            request_id='tracing-'+uuid.uuid4().hex[:16]
            response=client.get(args.path,headers={'X-Request-ID':request_id})
            print(json.dumps({**context,'request_id':request_id,'status':response.status_code,'returned_id':response.headers.get('X-Request-ID')}),flush=True)
            assert response.status_code==200 and response.headers.get('X-Request-ID')==request_id
        HTTPXClientInstrumentor.uninstrument_client(client)
    assert provider.force_flush(timeout_millis=5000), 'SDK flush timeout'
finally:
    provider.shutdown()
PYTHON
```

```bash
dp exec -T app python - "/api/v1/items/$TRACE_ITEM_ID" --count 2   < lab-notes/tracing/trace_client.py > "$LAB_DIR/client-traces.jsonl"
cat "$LAB_DIR/client-traces.jsonl"
COLD_TRACE=$(sed -n '1p' "$LAB_DIR/client-traces.jsonl" | jq -er '.trace_id')
WARM_TRACE=$(sed -n '2p' "$LAB_DIR/client-traces.jsonl" | jq -er '.trace_id')
fetch_trace "$COLD_TRACE" "$LAB_DIR/cold-trace.json"
fetch_trace "$WARM_TRACE" "$LAB_DIR/warm-trace.json"
python3 lab-notes/tracing/inspect_trace.py "$LAB_DIR/cold-trace.json" > "$LAB_DIR/cold-spans.json"
python3 lab-notes/tracing/inspect_trace.py "$LAB_DIR/warm-trace.json" > "$LAB_DIR/warm-spans.json"
```

The probe instruments one HTTPX client instance using the supported `HTTPXClientInstrumentor` API. Instrumentation creates the client span and injects W3C context; the request hook only records IDs for verification. A private tracer provider identifies the emitting probe as `lab-http-client`, distinct from the server process even though this diagnostic client runs through `docker exec`.

The normal application server extracts the propagated context. A new client span begins a trace for each GET because the probe has no enclosing current span. No manually copied `traceparent` string is needed.

## 7. Read the Cold and Warm Traces as Evidence

```bash
python3 - "$LAB_DIR/cold-spans.json" "$LAB_DIR/warm-spans.json" <<'PYTHON'
import json,sys
for file in sys.argv[1:]:
    rows=json.load(open(file))
    sql=[r for r in rows if 'sqlalchemy' in r['scope']]
    redis=[r for r in rows if 'redis' in r['scope']]
    httpx=[r for r in rows if 'httpx' in r['scope']]
    server=[r for r in rows if r['kind'] in (2,'SPAN_KIND_SERVER')]
    assert server and redis and httpx, 'Allow async batches to arrive; re-fetch before diagnosing'
    print(file, {'spans':len(rows),'sqlalchemy':len(sql),'redis':len(redis),'httpx':len(httpx),'server':len(server)})
    for row in rows:
        print(' ',row['service'],row['scope'],row['name'],round(row['duration_ms'],3),row['parent_span_id'])
PYTHON
```

Expected: both have an HTTPX CLIENT span, a FastAPI SERVER span, a Redis operation and the inherited `items.get` span. The cold trace normally includes SQLAlchemy spans; the warm trace normally does not include an item query. Connection spans or semantic-convention names can vary—use scope and attributes as well as the displayed span name.

If either trace is partial, fetch it again after a few seconds and regenerate its span table. If the warm trace queries PostgreSQL, investigate TTL expiration, Redis failure, serialization rejection or a concurrent invalidation. A trace is an observation of what happened; it is not obliged to match your prediction.

Open both IDs in Grafana. SQLAlchemy instruments `database.engine.sync_engine` because SQLAlchemy's async layer adapts the underlying engine's event hooks. The engine/pool is still created once, not per request. Redis instrumentation wraps the existing pooled client; it does not create a second cache connection strategy.

Do not sum every span duration to estimate wall time: parent and child spans overlap, and concurrent spans can overlap one another.

## 8. Prove Log Correlation and Stored-Trace Privacy

```bash
SCOPE=$(log_select)
TRACE_LOG_QUERY="$SCOPE | json tid="trace_id",event="event_name" | __error__="" | tid="$COLD_TRACE" | event="request_completed""
for attempt in {1..45}; do
  backend loki:3100 /loki/api/v1/query_range query "$TRACE_LOG_QUERY" since 10m limit 100     > "$LAB_DIR/correlated-log.json"
  if jq -e '[.data.result[].values[]]|length>0' "$LAB_DIR/correlated-log.json" >/dev/null; then break; fi
  sleep 1
done
jq -e '[.data.result[].values[]]|length>0' "$LAB_DIR/correlated-log.json"
python3 - "$LAB_DIR/cold-spans.json" "$LAB_DIR/warm-spans.json" <<'PYTHON'
import json,sys
for path in sys.argv[1:]:
    for span in json.load(open(path)):
        prohibited={'db.statement','db.query.text','http.url','http.target','url.full','url.query'}
        assert not prohibited.intersection(span['attributes']), span['name']
print('Selected sensitive attribute keys absent from stored span attributes')
PYTHON
```

Compare the record's request ID to the client ledger, its trace ID to Tempo, and its span ID to the server span table. The formatter reads active context; no logging SDK transport was added. Request IDs remain human-facing request correlation, while trace/span IDs encode execution relationships.

The privacy check covers the selected span-attribute keys only. It does not prove arbitrary data cannot leak through resource attributes, span names, events or logs. Do not use real credentials or sensitive request bodies in exercises.

## 9. Add Practical Logs-to-Traces Navigation

```bash
lab-notes/.tools/bin/python - <<'PYTHON'
from pathlib import Path
import yaml
root=Path('config/grafana/learning/provisioning/datasources')
found=[]
for path in root.glob('*.y*ml'):
    config=yaml.safe_load(path.read_text())
    for source in config.get('datasources',[]):
        if source.get('uid')=='loki':
            fields=source.setdefault('jsonData',{}).setdefault('derivedFields',[])
            fields[:]=[f for f in fields if f.get('name')!='TraceID']
            fields.append({'name':'TraceID','datasourceUid':'tempo',
                'matcherRegex':r'"trace_id"\s*:\s*"([0-9a-f]{32})"','url':'$${__value.raw}'})
            found.append(path)
            path.write_text(yaml.safe_dump(config,sort_keys=False))
assert len(found)==1, 'Expected exactly one active Loki datasource UID'
print(found[0])
PYTHON
dp restart grafana
wait_grafana
```

In Loki Explore, search the correlated completion record and expand it. The `TraceID` derived field should navigate to Tempo. The doubled dollar sign preserves Grafana's field expression through provisioning environment expansion. If the link fails, copy the exact ID into Tempo manually to separate datasource navigation from trace delivery.

## 10. Cleanup, Recovery, and Troubleshooting

```bash
api -fsS -X DELETE "$APP_URL/api/v1/items/$TRACE_ITEM_ID" -o /dev/null
wait_ready
pq 'up{job="fastapi"}' > "$LAB_DIR/fastapi-up-after.json"
make test
```

Only the lab-owned item is deleted. The application keeps tracing enabled for Lab 35. Existing database migrations, dependency behavior and Prometheus instrumentation are unchanged.

| Symptom | Diagnostic path |
|---|---|
| Instrumentation import fails | Check matching locked packages inside the rebuilt image; a host pip install does not change it. |
| Duplicate server or Redis spans | Remove an extra CLI/bootstrap wrapper; keep the existing code-owned provider. |
| SQL spans missing on a cold read | Verify cache key/TTL and stored trace completeness before altering instrumentation. |
| Warm read includes SQL | Check Redis degradation, key invalidation and elapsed time. |
| Completion log lacks trace ID | Confirm app environment was recreated and the logger runs inside the active server context. |
| Trace ID exists in logs but not Tempo | Sampling/export/storage can discard spans; a valid context does not guarantee retention. |
| Outgoing HTTPX client adds metrics | Ensure the no-op meter provider is passed and no metric exporter was added. |

If tracing causes a startup problem, move `compose.auto-tracing.yaml` out of the active path, recreate the app and prove readiness. Restore the saved requirement files and rebuild only when rolling back the added dependency. Do not remove unrelated application changes.

Upstream API references: [FastAPI](https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/fastapi/fastapi.html), [SQLAlchemy](https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/sqlalchemy/sqlalchemy.html), [Redis](https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/redis/redis.html), and [HTTPX](https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/httpx/httpx.html) instrumentation.

## 11. Knowledge Check

1. Why instrument sync_engine for an async SQLAlchemy engine?
2. Why should a warm GET normally lack item SQL?
3. Does summing span durations give response latency?
4. Can a trace ID in a log exist without a retained trace?

### Answer Guide

1. SQLAlchemy async operations use the underlying synchronous engine event interface.
2. Redis should return the cached representation before a database lookup.
3. No; nested and concurrent spans overlap.
4. Yes; sampling and delivery/storage failures can remove the trace.

## 12. Professional Scenario Exercise

A teammate observes a slow warm GET and proposes increasing PostgreSQL pool size. Use the actual span tree, cache result and HTTP client/server durations to decide whether that change addresses the observed delay. State what additional evidence you would require.

## 13. Lab Notebook Template

Create a notebook in the evidence directory for this run:

```bash
test -e "$LAB_DIR/notebook.md" || printf '# Lab 34 Evidence\n' > "$LAB_DIR/notebook.md"
```

Use these headings and fill them with your predictions, commands, observed results and conclusions:

```markdown
# Lab 34 Evidence

## Starting state and operational question
## Prediction before the change
## Commands and timestamps
## Observed results and source evidence
## Unexpected findings and troubleshooting
## Recovery proof and remaining limitations
```

## 14. Observable Completion Criteria

- [ ] Runtime pins and exactly one instrumentation owner were verified.
- [ ] A lab-owned item produced cold and warm trace evidence with client/framework/dependency spans.
- [ ] Actual cache behavior was explained rather than inferred from latency alone.
- [ ] A completion log correlates with the retrieved trace.
- [ ] The selected stored attributes pass the redaction check and the lab-owned item was cleaned up.

## 15. Production Implications

Automatic instrumentation provides consistent library-level boundaries, not a complete business model. Control capture, sample deliberately, watch overhead and prevent duplicate bootstrap. Rebuilding with coherent locks is part of instrumentation maintenance. Health-route exclusion and background dependency spans require separate consideration.

## 16. End State and Transition

Twelve long-running services and nine scrape jobs remain. The Items app now exports traces through the Collector; HTTPX is a locked runtime dependency and the four existing dashboards remain. [Lab 35](Lab-35.md) adds a separate internal FastAPI service and proves W3C parentage across a real service boundary.
