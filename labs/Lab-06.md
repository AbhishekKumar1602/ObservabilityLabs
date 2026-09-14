# Lab 06: Events, Structured Logging, Request IDs, and Evidence Capture

## Purpose and Scope

> **Primary Objective:** Distinguish occurrences from their recorded evidence; implement a safe, versioned JSON log envelope; prove request-context isolation; and preserve reproducible operational evidence.

The earlier labs established what the Items API does and how it recovers. This lab gives those observations precise names. A database commit is an occurrence. A message describing it is a record of that occurrence. A line in a container log is a serialization of a record; it is not the occurrence itself.

You will extend the existing logger, use real requests to connect application and HTTP records, test correlation and redaction boundaries, and capture a small dependency change. No log backend is needed yet.

## 1. Inherited State and Scope

Complete Labs 1–5, including Lab 2's database connection-error regression. Keep the course checkpoint item and the baseline overlay. Only `app`, `postgres` and `redis` should be running.

Use the same Linux Docker host, Bash, curl, jq, Python 3, Git, Make and ripgrep. All commands run at repository root. Keep OpenTelemetry export and Pyroscope disabled, and do not start Loki or Collector.

Kubernetes Events, span events, audit records and profiles are compared conceptually here. Their existence does not require a new event broker, Kubernetes cluster, span-event implementation or auditing subsystem in this lab.

## 2. Clean Starting-State Check

```bash
source lab-notes/session.sh
baseline_check
CHECKPOINT_ID=$(cat lab-notes/checkpoint-item-id.txt)
api -fsS "$APP_URL/api/v1/items/$CHECKPOINT_ID" | jq '{id,name,price}'
git diff -- app/app/logging_config.py
rg -n 'request_id_context|SAFE_ID|request_completed' app/app/middleware.py
```

Expected: the checkpoint item exists and exactly three long-running services are active. Review any local logger edits before replacing its file. Preserve unrelated work in your Git checkpoint.

## 3. Measurable Learning Objectives

By completion, you should be able to:

- distinguish an event from a representation or aggregate of it;
- explain when one occurrence can produce several records, or no retained record;
- separate event ID, request ID, trace ID and item ID;
- emit one parseable JSON object per application log line;
- preserve existing trace correlation while keeping trace export disabled;
- prove accepted, rejected and duplicate request-ID behavior;
- show two concurrent requests keep separate context;
- capture operational changes and a bounded dependency failure;
- identify the limits of field allowlisting and ordinary logs as audit evidence; and
- restore business readiness and retain evidence without credentials.

## 4. The Event and Evidence Model

| Term | Meaning | Example in this platform |
|---|---|---|
| Real event | Something happens or state changes | PostgreSQL commits an item insert |
| Event record | A representation describing an occurrence | A record named `item_created` |
| Log record | A logging-system object with level, message and context | Python `logging.LogRecord` |
| Log line | Encoded bytes written to a stream | One newline-terminated JSON object |
| Metric observation | An update to instrument state | Increment a request counter or observe duration |
| Metric sample | A numeric value with labels and a timestamp in the metric system | Prometheus stores a counter value at scrape time |
| Span event | A timestamped annotation within a span | A retry event attached to a request span in a later lab |
| Change event | An operational or configuration change | An operator stops Redis or deploys a new image |
| Audit event | Evidence of a governed action by an identified actor | An authenticated administrator changes permissions |
| Kubernetes Event | A Kubernetes API resource about a lifecycle/warning occurrence | A pod scheduling failure; no cluster is present here |
| Profile sample | A sampled stack/resource observation | Python CPU stack sampled by Pyroscope later |

An event does not imply exactly one log entry. One POST can create an `item_created` record and a `request_completed` record. A process can fail after commit before emitting either. A metric counter can reflect an event whose detailed log was lost.

A profile sample is not a list of business operations. A span event depends on its span being recorded and retained. A Kubernetes Event is not a generic name for every container log. Keep these distinctions when explaining evidence gaps.

## 5. Current Signal Flow

```mermaid
flowchart TD
    HTTP["HTTP request + optional request ID"] --> Context["ASGI context"]
    Context --> Handler["Item handler"]
    Handler --> DB["PostgreSQL commit"]
    Handler --> Business["Business log record"]
    Context --> Completion["HTTP completion record"]
    Business --> Formatter["JSON formatter"]
    Completion --> Formatter
    Formatter --> Output["stdout and Docker json-file"]
    Output --> Notes["Local evidence capture"]
```

The process already updates metric state, but metric interpretation starts in Lab 7. No external event backend is introduced. Docker log retention can remove old lines; local capture is an investigation aid, not guaranteed archival delivery.

## 6. Choose the Record Envelope Before Editing

| Field | Contract |
|---|---|
| `timestamp` | UTC time when the Python log record was created |
| `schema_version` | Envelope version `1` |
| `event_id` | UUID identifying this log record; retained when formatting it again |
| `event_name` | Bounded event name from known application messages, otherwise `application_log` |
| `message` | Human-readable message; application call sites use safe constants |
| `request_id` | Validated context-local request correlation, or null outside a request |
| HTTP fields | Method, normalized route, status and elapsed milliseconds when applicable |
| Error fields | Class and sanitized frame locations, not raw exception text |
| Trace fields | Included only when a valid active span context exists |

An `event_id` is not the database transaction ID or a guarantee of durable delivery. `timestamp` approximates record creation, not the exact PostgreSQL commit instant. The event ID remains a log field; never turn it into a Prometheus or Loki index label.

The formatter's allowlist prevents accidental serialization of arbitrary `extra` fields. It does **not** sanitize secrets already interpolated into `message`. Keep call sites safe and test what is actually emitted.

## 7. Implement the Structured Record Envelope

Replace the existing formatter file after reviewing the previous diff. This keeps stdout, request context, safe exception frames and optional trace fields intact while adding explicit record identity and event naming.

```bash
cat > app/app/logging_config.py <<'PYTHON'
"""Structured stdout records with safe context and stable record identifiers."""

import json
import logging
import sys
import traceback
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from opentelemetry import trace

from app.config import Settings

request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)
EVENT_NAMES = frozenset(
    {
        "item_created",
        "item_updated",
        "item_deleted",
        "request_completed",
        "database_request_failed",
        "unhandled_request_exception",
        "cache_operation_failed",
        "application_started",
        "application_stopped",
        "dependencies_ready",
        "dependencies_degraded",
        "postgres_startup_probe_failed",
        "postgres_unavailable_starting_not_ready",
    }
)


class JsonFormatter(logging.Formatter):
    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        event: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "schema_version": 1,
            "event_id": record.__dict__.setdefault("_observability_event_id", str(uuid4())),
            "event_name": message if message in EVENT_NAMES else "application_log",
            "level": record.levelname,
            "logger": record.name,
            "service": self.settings.service_name,
            "environment": self.settings.environment,
            "message": message,
            "request_id": request_id_context.get(),
        }
        context = trace.get_current_span().get_span_context()
        if context.is_valid:
            event.update(
                trace_id=f"{context.trace_id:032x}",
                span_id=f"{context.span_id:016x}",
                trace_sampled=context.trace_flags.sampled,
            )
        for key in ("http.method", "http.route", "http.status_code", "duration_ms", "operation", "error_type"):
            if key in record.__dict__:
                event[key] = record.__dict__[key]
        if record.exc_info and record.exc_info[0]:
            event["error_type"] = record.exc_info[0].__name__
            event["error_frames"] = [
                {"file": Path(f.filename).name, "line": f.lineno, "function": f.name}
                for f in traceback.extract_tb(record.exc_info[2])[-12:]
            ]
        return json.dumps(event, ensure_ascii=True, separators=(",", ":"))


def configure_logging(settings: Settings) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(settings))
    logging.getLogger().handlers = [handler]
    logging.getLogger().setLevel(settings.log_level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
PYTHON
```

The event-name set is intentionally small. Unknown framework messages remain useful human-readable logs without becoming a stream of unbounded event names. `setdefault` retains one ID on the LogRecord if multiple format operations occur.

Request context is read synchronously during formatting in this implementation. If you later introduce queued/background logging, capture the context into the record before crossing the task/thread boundary; do not assume a later consumer has the originating ContextVar.

## 8. Add Context and Logging Contract Tests

Create `app/tests/test_logging.py`. This test module is the only new application test file in this lab. It checks behavior that stdout inspection alone cannot reliably expose: concurrent context isolation, duplicate-ID handling and stable record IDs.

```bash
cat > app/tests/test_logging.py <<'PYTHON'
import asyncio
import json
import logging
import sys
from uuid import UUID

from app.logging_config import JsonFormatter, request_id_context


async def test_record_identity_context_and_field_allowlist(app):
    formatter = JsonFormatter(app.state.settings)
    record = logging.LogRecord("app.api", logging.INFO, __file__, 1, "item_created", (), None)
    record.authorization = "synthetic-secret"
    record.payload = {"description": "synthetic-secret"}
    token = request_id_context.set("lab06-request")
    try:
        first = json.loads(formatter.format(record))
        second = json.loads(formatter.format(record))
    finally:
        request_id_context.reset(token)
    assert first["event_id"] == second["event_id"]
    assert str(UUID(first["event_id"])) == first["event_id"]
    assert first["event_name"] == "item_created"
    assert first["schema_version"] == 1
    assert first["request_id"] == "lab06-request"
    assert "synthetic-secret" not in json.dumps(first)
    assert "trace_id" not in first
    assert request_id_context.get() is None


async def test_exception_message_is_not_serialized(app):
    try:
        raise RuntimeError("synthetic-password-do-not-log")
    except RuntimeError:
        record = logging.LogRecord("app.api", logging.ERROR, __file__, 1, "database_request_failed", (), sys.exc_info())
    document = json.loads(JsonFormatter(app.state.settings).format(record))
    assert document["error_type"] == "RuntimeError"
    assert document["error_frames"]
    assert "synthetic-password-do-not-log" not in json.dumps(document)


async def test_concurrent_business_logs_keep_request_context(client, app):
    documents = []

    class Capture(logging.Handler):
        def emit(self, record):
            documents.append(json.loads(self.format(record)))

    handler = Capture()
    handler.setFormatter(JsonFormatter(app.state.settings))
    logger = logging.getLogger("app.api")
    logger.addHandler(handler)
    identifiers = ["lab06-first", "lab06-second"]
    try:
        responses = await asyncio.gather(
            *[
                client.post(
                    "/api/v1/items",
                    headers={"X-Request-ID": identifier, "Authorization": "Bearer synthetic-secret"},
                    json={"name": "context subject", "price": "1.00", "description": "synthetic-secret"},
                )
                for identifier in identifiers
            ]
        )
    finally:
        logger.removeHandler(handler)
    assert all(response.status_code == 201 for response in responses)
    created = [record for record in documents if record["event_name"] == "item_created"]
    assert {record["request_id"] for record in created} == set(identifiers)
    assert len({record["event_id"] for record in created}) == 2
    assert "synthetic-secret" not in json.dumps(documents)
    assert request_id_context.get() is None


async def test_duplicate_and_invalid_request_ids_are_replaced(client):
    for headers in (
        [("X-Request-ID", "first"), ("X-Request-ID", "second")],
        [("X-Request-ID", "not a valid id")],
        [("X-Request-ID", "x" * 65)],
    ):
        response = await client.get("/api/v1/items?limit=1", headers=headers)
        generated = response.headers["X-Request-ID"]
        assert str(UUID(generated)) == generated
PYTHON
```

## 9. Run Tests and Rebuild the Application

```bash
make test
make lint
git diff --check
git diff --stat
dc up -d --build --no-deps app
baseline_check
```

Fix any failed test before continuing. The image rebuild applies the formatter change; a restart alone cannot copy new source into the container. Existing PostgreSQL rows remain in their named volume.

The concurrency test uses isolated application resources; it is not a throughput benchmark. The existing JSON request body and authorization header are deliberately synthetic canaries.

## 10. Establish the Shared Evidence Workflow

Create one helper used throughout Labs 6–15. Each run gets a private directory so a second attempt does not overwrite the first. Change records describe your operation and its outcome; they do not establish an authenticated, tamper-resistant audit trail.

```bash
cat > lab-notes/evidence.sh <<'BASH'
# Source after lab-notes/session.sh. Contains no application secrets.
start_lab() {
  local number="$1"
  [[ "$number" =~ ^[0-9]{2}$ ]] || { echo 'Use a two-digit lab number' >&2; return 1; }
  umask 077
  mkdir -p "$LAB_ROOT/lab-notes/lab-$number"
  LAB_DIR=$(mktemp -d "$LAB_ROOT/lab-notes/lab-$number/run-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX") || return 1
  export LAB_DIR
  date -u +'%Y-%m-%dT%H:%M:%SZ' > "$LAB_DIR/started-at.txt"
  git -C "$LAB_ROOT" rev-parse HEAD > "$LAB_DIR/commit.txt" 2>/dev/null || true
  printf 'Evidence directory: %s\n' "$LAB_DIR"
}

record_change() {
  : "${LAB_DIR:?Call start_lab first}"
  python3 - "$LAB_DIR/changes.jsonl" "$1" "$2" <<'PYTHON'
import json, os, sys
from datetime import datetime, timezone
from uuid import uuid4
path, action, outcome = sys.argv[1:]
if outcome not in {"planned", "completed", "failed"}:
    raise SystemExit("Outcome must be planned, completed, or failed")
record = {
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "event_id": str(uuid4()), "event_name": "operator_change",
    "actor": f"local-uid:{os.getuid()}", "action": action, "outcome": outcome,
}
with open(path, "a", encoding="utf-8") as output:
    output.write(json.dumps(record, separators=(",", ":")) + "\n")
PYTHON
}

capture_app_logs() {
  : "${LAB_DIR:?Call start_lab first}"
  dc logs --since "$(cat "$LAB_DIR/started-at.txt")" --no-color --no-log-prefix app \
    > "$LAB_DIR/docker-app-output.txt" 2>&1 || return 1
  python3 - "$LAB_DIR" <<'PYTHON'
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
records, unparsed = [], []
for line in (root / "docker-app-output.txt").read_text().splitlines():
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        unparsed.append(line)
        continue
    if isinstance(value, dict) and "timestamp" in value and "message" in value:
        records.append(value)
    else:
        unparsed.append(line)
(root / "app.jsonl").write_text("".join(json.dumps(value) + "\n" for value in records))
(root / "unparsed-lines.txt").write_text("\n".join(unparsed) + ("\n" if unparsed else ""))
print(f"parsed_records={len(records)} unparsed_lines={len(unparsed)}")
PYTHON
}
BASH
```

```bash
source lab-notes/evidence.sh
start_lab 06
record_change "install_lab06_logging_envelope" completed
dc ps -a > "$LAB_DIR/containers.txt"
```

`capture_app_logs` preserves non-JSON lines separately instead of silently treating them as valid records. It can include process startup or CLI diagnostics; inspect `unparsed-lines.txt`. `umask 077` keeps new evidence private.

`lab-notes/` should already be ignored by Git from setup. Verify with `git check-ignore lab-notes/evidence.sh`. Never capture an unfiltered Compose model or full environment inspection into this directory.

## 11. Predict the Records for One Create Request

Write your prediction before sending the request:

1. Which business event name should appear?
2. Which HTTP completion record should share its request ID?
3. Should the two records share an event ID?
4. Will item name, description, authorization or cookie values appear?
5. Should a valid trace ID appear while tracing is disabled?

The request ID joins records for one request. Record IDs distinguish the records themselves. A client can reuse an accepted request ID; it is correlation input, not authenticated identity or an idempotency key.

## 12. Send a Correlated Request and Preserve the Result

```bash
RID="lab06-$(new_uuid)"
api -fsS -D "$LAB_DIR/create-headers.txt" \
  -H "X-Request-ID: $RID" \
  -H 'Authorization: Bearer SYNTHETIC_DO_NOT_LOG_LAB06' \
  -H 'Cookie: example=SYNTHETIC_DO_NOT_LOG_LAB06' \
  -H 'Content-Type: application/json' \
  -d '{"name":"Lab 06 event subject","description":"SYNTHETIC_DO_NOT_LOG_LAB06","price":"6.00"}' \
  "$APP_URL/api/v1/items" -o "$LAB_DIR/item.json"
ITEM_ID=$(jq -er '.id' "$LAB_DIR/item.json")
printf '%s\n' "$RID" > "$LAB_DIR/request-id.txt"
capture_app_logs
jq -c --arg rid "$RID" 'select(.request_id == $rid) | {event_id,event_name,request_id,message,"http.status_code":.["http.status_code"]}' \
  "$LAB_DIR/app.jsonl"
```

Expected: HTTP 201 and a response `X-Request-ID` equal to your value. `item_created` and `request_completed` share that request ID but have different event IDs. The completion record reports 201. Additional records should be explained rather than automatically counted as duplicates.

Preserve HTTP response headers and body separately: the body intentionally contains your synthetic description, while the log should not.

## 13. Verify Correlation and Safe Fields Structurally

```bash
python3 - "$LAB_DIR" <<'PYTHON'
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
rid = (root / "request-id.txt").read_text().strip()
records = [json.loads(line) for line in (root / "app.jsonl").read_text().splitlines()]
matched = [record for record in records if record.get("request_id") == rid]
assert {"item_created", "request_completed"} <= {record["event_name"] for record in matched}
assert len({record["event_id"] for record in matched}) == len(matched)
assert all(record["schema_version"] == 1 for record in matched)
assert "SYNTHETIC_DO_NOT_LOG_LAB06" not in json.dumps(records)
assert all("trace_id" not in record for record in matched)
print("Correlation, record identity and synthetic-secret checks passed")
PYTHON
dbsql -v item_id="$ITEM_ID" <<'SQL'
SELECT id, name, price FROM items WHERE id = :'item_id'::uuid;
SQL
```

A database row and two logs support different claims. SQL proves visible committed state now. The business record says the application reached its post-commit logging point. The completion record says the server completed that request path; it does not prove the client consumed every response byte.

The absence of trace fields is expected for this baseline. Request IDs provide useful correlation before distributed tracing is introduced.

## 14. Validate Request-ID Admission Rules

```bash
for candidate in 'valid.lab06_id-1' 'contains a space'; do
  api -fsS -D - -o /dev/null -H "X-Request-ID: $candidate" \
    "$APP_URL/api/v1/items?limit=1"
done
api -fsS -D - -o /dev/null \
  -H 'X-Request-ID: first' -H 'X-Request-ID: second' \
  "$APP_URL/api/v1/items?limit=1"
```

The valid header is accepted. Whitespace, more than 64 characters, missing values, or multiple request-ID headers cause a generated UUID. Allowed incoming characters are letters, digits, dot, underscore and hyphen, with the first character alphanumeric.

Do not demonstrate header injection by pasting raw newlines into HTTP headers. The isolated regression tests cover invalid input without relying on how curl or a proxy sanitizes control bytes.

## 15. Inspect Context Propagation and Its Boundaries

```bash
sed -n '28,115p' app/app/middleware.py
rg -n 'logger.info|logger.warning|logger.error' app/app/api.py app/app/cache.py app/app/main.py
```

The middleware sets a ContextVar token and request state before entering the handler. It adds the response header and resets the token in `finally`. Awaited calls within the request keep the context; the tests verify concurrent requests do not overwrite each other.

Background dependency probes normally have no request ID because they are not caused by a specific HTTP request. Do not manufacture one. A copied request context in a background task is also not proof that the task belongs to the same completed response lifecycle.

The API has no authenticated users. An accepted `X-Request-ID` must never be reported as an actor ID in an audit record.

## 16. Record and Observe a Bounded Operational Change

Predict which evidence will show **intent**, which will show a failed cache operation, and which will show current dependency state. A change record alone cannot establish that its intended effect occurred.

```bash
(
  set -euo pipefail
  trap 'dc start redis >/dev/null' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  record_change "stop_redis_for_lab06" planned
  dc stop redis
  record_change "stop_redis_for_lab06" completed
  api -fsS -H "X-Request-ID: lab06-cache-$(new_uuid)" \
    "$APP_URL/api/v1/items/$ITEM_ID" -o "$LAB_DIR/fallback.json"
  api -fsS "$APP_URL/health/ready" | tee "$LAB_DIR/degraded.json" | jq .
  jq -e '.status == "degraded" and .dependencies.postgres == "up"' "$LAB_DIR/degraded.json" >/dev/null
)
wait_ready
record_change "restore_redis_after_lab06" completed
capture_app_logs
jq -c 'select(.event_name == "cache_operation_failed") | {timestamp,event_name,request_id,operation,error_type}' \
  "$LAB_DIR/app.jsonl"
```

Expected: the individual read succeeds through PostgreSQL, readiness stays HTTP 200/degraded, and a sanitized cache failure record appears. Some probe failures have null request IDs; a request-scoped cache failure should carry its request context.

Dependency transition logs are emitted by a periodic loop. A sufficiently short outage might fall entirely between two probes, so the absence of a transition log does not negate the request failure record and captured readiness response.

## 17. Restore and Prove the End State

```bash
api -fsS "$APP_URL/health/ready" | tee "$LAB_DIR/recovered.json" | jq .
api -fsS -X DELETE "$APP_URL/api/v1/items/$ITEM_ID" -o /dev/null
baseline_check
api -fsS "$APP_URL/api/v1/items/$CHECKPOINT_ID" | jq '{id,name,price}'
capture_app_logs
```

Keep only the course checkpoint item. Stopping Redis for this drill did not require editing the application's readiness policy, deleting volumes, or enabling a second log transport.

## 18. Evidence Integrity and Honest Conclusions

```bash
python3 - "$LAB_DIR" <<'PYTHON'
import hashlib, json, sys
from pathlib import Path
root = Path(sys.argv[1])
manifest = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.iterdir()) if p.is_file() and p.name != "sha256.json"}
(root / "sha256.json").write_text(json.dumps(manifest, indent=2) + "\n")
print("Recorded hashes for", len(manifest), "evidence files")
PYTHON
```

A hash helps detect later changes relative to this manifest; an operator able to edit both files and manifest can rewrite both. This is not signed, immutable audit storage. Update the manifest deliberately if you later edit your notebook.

Record the clock source, time window and capture method. Events can be lost through crashes, rotation, buffering or sampling. Do not infer that an event never occurred solely because no record survived.

## 19. Troubleshooting Runbook

| Symptom | Investigation and correction |
|---|---|
| New envelope fields are absent | Confirm the app image was rebuilt and recreated; inspect a new request, not an old line |
| JSON parsing fails | Inspect `unparsed-lines.txt`; preserve CLI/startup lines separately and remove Compose prefixes with the supplied flags |
| Request IDs are null everywhere | Send a business request, inspect middleware ordering, and verify you are not only reading background probe logs |
| Two requests seem mixed | Compare response headers and the concurrency regression; ensure the client did not deliberately reuse the same ID |
| No trace fields | Expected while OTel is disabled; do not add fake trace IDs |
| A secret appears in a log | Inspect interpolation at the logging call site; an allowlist cannot repair a secret already in `message` |
| No dependency transition line | Check probe interval and outage duration; compare direct health and request evidence |
| Redis remains stopped after interruption | Run `dc start redis`, then `wait_ready`; capture the manual recovery action |
| Old and new JSON coexist | Logs span multiple application versions; filter by `schema_version` and the run start time |

Before changing code, identify whether the problem is event generation, context, formatting, capture, timing or an incorrect expectation. Do not solve every evidence gap by enabling DEBUG or printing payloads.

## 20. Knowledge Check

1. Can a real event occur without a retained log?
2. Why do two records for one POST have different event IDs?
3. Does a request counter store the request IDs that caused it?
4. Is an event ID a valid bounded metric label?
5. Why are background probe records allowed to have null request IDs?
6. Does field allowlisting redact a password interpolated into the message?
7. What distinguishes a span event from a log record?
8. Can a CPU profile enumerate every item creation?
9. Does a local change record identify an authenticated business actor?
10. Are Kubernetes Events required for this Compose experiment?

### Answer Guide

1. Yes; emission, flushing, transport or retention can fail.
2. They describe different recorded observations while sharing request context.
3. No; it stores accumulated numeric state.
4. No; its values grow with records.
5. No particular HTTP request caused the background observation.
6. No; safe call sites are still required.
7. A span event belongs to a span and shares its tracing/retention lifecycle.
8. No; profiling samples stacks/resources over time.
9. No; this app has no authentication/audit subsystem, and the local file is editable.
10. No; they are a separate Kubernetes API concept introduced only for comparison.

## 21. Professional Scenario Exercise

An operator claims that an absent `item_created` log proves a customer write never committed. Write a response that separates transaction state, log emission, response delivery and retention. Propose the smallest safe verification sequence and explain why blindly retrying POST could create a second item.

## 22. Lab Notebook Template

Create a notebook in the evidence directory for this run:

```bash
test -e "$LAB_DIR/notebook.md" || printf '# Lab 06 Evidence\n' > "$LAB_DIR/notebook.md"
```

Use these headings and fill them with your predictions, commands, observed results and conclusions:

```markdown
# Lab 06 Evidence

## Objective and inherited state
## Event/evidence terminology in my own words
## Envelope contract and code diff
## Test results
## One POST: predicted and observed records
## Request IDs versus record IDs
## Synthetic-secret check
## Redis change timeline and dependency evidence
## Recovery proof
## What is observed, inferred, or unknown
## Knowledge-check answers
## Professional scenario response
```

## 23. Observable Completion Criteria

- [ ] The new event envelope is emitted and tests pass.
- [ ] A create is correlated across HTTP, business log and direct SQL.
- [ ] Distinct records have distinct event IDs, while request IDs correlate related work.
- [ ] Invalid and duplicate incoming IDs are replaced.
- [ ] Concurrent request contexts remain isolated.
- [ ] Synthetic credentials/body values do not appear in captured logs.
- [ ] The Redis change has intent, observed impact and recovery evidence.
- [ ] The notebook distinguishes events from log records, metric observations, span events and samples.
- [ ] Only the three baseline services run; the checkpoint item survives.

## 24. Production Implications

Structured logs improve investigation when the event semantics are clear. Ordinary stdout logging is neither exactly-once event delivery nor a compliance audit system. Request IDs are untrusted correlation input; record IDs are high-cardinality fields. Stronger audit guarantees need authenticated actors, governed retention, integrity controls and independent storage. Those responsibilities cannot be proven by a JSON formatter on one host.

## 25. End State and Transition

Leave the three-service baseline running with the new formatter and tests. Keep `lab-notes/evidence.sh`; source it after `session.sh` in later labs. Preserve the course checkpoint item.

Next: [Lab 07 — Raw OpenMetrics Before Prometheus](Lab-7.md). You will compare individual event records with numeric instrument state, add actual format negotiation, and read the wire representation before introducing a scraper.
