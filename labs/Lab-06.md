# Lab 06: Events, Structured Logging, Request IDs, and Evidence Capture

## 1. Purpose and Learning Outcomes

**In Plain Language:** You will turn application output into records that can be connected to a specific request. First define a consistent JSON structure, then test how request IDs move through concurrent work. Finally, capture a small operational change with its responses and logs so another person can reconstruct what you did and what actually happened.

> **Primary Objective:** Distinguish occurrences from their recorded evidence; implement a safe, versioned JSON log envelope; prove request-context isolation; and preserve reproducible operational evidence.

The earlier labs established what the Items API does and how it recovers. This lab gives those observations precise names. A database commit is an occurrence. A message describing it is a record of that occurrence. A line in a container log is a serialization of a record; it is not the occurrence itself.

You will extend the existing logger, use real requests to connect application and HTTP records, test correlation and redaction boundaries, and capture a small dependency change. No log backend is needed yet.

## 2. Starting Point and Lab Map

Use this guide inside the complete application repository. Run commands from the repository root in Bash unless a step says otherwise. Work through the starting checks in order; they load the helpers and state this lab needs.

Read the explanation before each step, run one complete code block at a time, and compare the actual result with the stated expectation. Save commands, observations, explanations, and unexpected results in your notebook.

### Key Terms

| **Term**     | **Plain-Language Meaning**                                                                 |
| ------------ | ------------------------------------------------------------------------------------------ |
| Event        | Something that happened; a log is a record describing it.                                  |
| Request ID   | An identifier used to connect records belonging to one request.                            |
| Log envelope | The common fields around each record, such as time, severity, event name, and identifiers. |

### Lab Map

The arrows show the flow or dependency being studied. Branches identify paths to compare; this is a conceptual map, not a captured runtime trace.

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

## 3. Guided Walkthrough

### Step 01. Inherited State and Scope

**What You Are Doing:** Carry forward the recovered baseline and earlier code changes. This lab adds evidence to the request paths you already understand without starting a log backend.

**Practical Walkthrough:** Start from the working application left by Lab 05, including the database error fix and saved checkpoint item. These are prerequisites because this lab compares records of known behavior. Keep export disabled: you will first prove what the application emits locally before introducing transport or a searchable logging service.

Confirm the checkpoint still represents successful business work before using logs to describe it. Keep local stdout capture as the only log-delivery path under test. This lets you distinguish a missing application record from a future shipping or indexing failure without adding another component to the experiment.

Complete Labs 1–5, including Lab 2's database connection-error regression. Keep the course checkpoint item and the baseline overlay. Only `app`, `postgres` and `redis` should be running.

Use the same Linux Docker host, Bash, curl, jq, Python 3, Git, Make and ripgrep. All commands run at repository root. Keep OpenTelemetry export and Pyroscope disabled, and do not start Loki or Collector.

Kubernetes Events, span events, audit records and profiles are compared conceptually here. Their existence does not require a new event broker, Kubernetes cluster, span-event implementation or auditing subsystem in this lab.

**Understanding the Result:** The expected environment contains only the application and its two dependencies. Extra telemetry services would add observations without proving the local logging contract.

### Step 02. Clean Starting-State Check

**What You Are Doing:** Check the known item and inspect local logger changes before replacing code. A clean comparison requires both working business behavior and an understood source baseline.

**Practical Walkthrough:** Load the session functions into this shell, run the baseline check, and read the retained item through the API. Then inspect the logger and middleware source. The source checks reveal whether earlier edits need preserving before the replacement block runs; a working container alone does not reveal unbuilt local changes.

Read the current source before the replacement command, especially any local diff that has not been built into the running image. A successful request checks the deployed app, while the file review checks what the next build will contain. Resolve any mismatch before attributing later behavior to the new formatter.

```bash
source lab-notes/session.sh
baseline_check
CHECKPOINT_ID=$(cat lab-notes/checkpoint-item-id.txt)
api -fsS "$APP_URL/api/v1/items/$CHECKPOINT_ID" | jq '{id,name,price}'
git diff -- app/app/logging_config.py
rg -n 'request_id_context|SAFE_ID|request_completed' app/app/middleware.py
```

**Expected Result:** the checkpoint item exists and exactly three long-running services are active. Review any local logger edits before replacing its file. Preserve unrelated work in your Git checkpoint.

**Understanding the Result:** Proceed when the business check passes and you understand the local diff. Save unrelated edits before applying the supplied replacement.

### Step 03. Measurable Learning Objectives

**What You Are Doing:** Use these outcomes to distinguish identity, context, and record content. Each solves a different part of explaining a request after it has finished.

**Practical Walkthrough:** Read the objectives as separate checks you will perform, rather than as names to memorize. Request identity connects work, event identity distinguishes individual records, and context determines which request owns a record. Keep an evidence note for each behavior so that a successful-looking example does not substitute for testing the other cases.

Prepare separate checks for record identity, request correlation, and concurrent isolation. Two records can correctly share one request ID while needing different event IDs. During review, explain which assertion checks each property so a well-formed JSON line is not mistaken for proof of correct context ownership.

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

**Understanding the Result:** You should eventually be able to point to both example records and tests. Examples show a particular run; tests cover deliberately arranged edge cases.

### Step 04. The Event and Evidence Model

**What You Are Doing:** Separate a real occurrence from its representations. One request can produce several records and several metric updates, so those counts need not be interchangeable.

**Practical Walkthrough:** Choose one create request and follow its occurrences: receipt, database commit, and response completion. Now distinguish those occurrences from the JSON lines that describe them. A count may aggregate several completions, while two different log records may describe different parts of the same request. Neither representation preserves every detail automatically.

Name the real occurrence first, then identify the representation that observes it. A create event and a completion event can describe different boundaries in the same request. Compare their fields and timestamps without assuming one line equals one whole transaction or that every occurrence necessarily produced a retained record.

| **Term**           | **Meaning**                                                      | **Example in This Platform**                            |
| ------------------ | ---------------------------------------------------------------- | ------------------------------------------------------- |
| Real event         | Something happens or state changes                               | PostgreSQL commits an item insert                       |
| Event record       | A representation describing an occurrence                        | A record named `item_created`                           |
| Log record         | A logging-system object with level, message and context          | Python `logging.LogRecord`                              |
| Log line           | Encoded bytes written to a stream                                | One newline-terminated JSON object                      |
| Metric observation | An update to instrument state                                    | Increment a request counter or observe duration         |
| Metric sample      | A numeric value with labels and a timestamp in the metric system | Prometheus stores a counter value at scrape time        |
| Span event         | A timestamped annotation within a span                           | A retry event attached to a request span in a later lab |
| Change event       | An operational or configuration change                           | An operator stops Redis or deploys a new image          |
| Audit event        | Evidence of a governed action by an identified actor             | An authenticated administrator changes permissions      |
| Kubernetes Event   | A Kubernetes API resource about a lifecycle/warning occurrence   | A pod scheduling failure; no cluster is present here    |
| Profile sample     | A sampled stack/resource observation                             | Python CPU stack sampled by Pyroscope later             |

An event does not imply exactly one log entry. One POST can create an `item_created` record and a `request_completed` record. A process can fail after commit before emitting either. A metric counter can reflect an event whose detailed log was lost.

A profile sample is not a list of business operations. A span event depends on its span being recorded and retained. A Kubernetes Event is not a generic name for every container log. Keep these distinctions when explaining evidence gaps.

**Understanding the Result:** Count records only after specifying their event type. The number of log lines is not inherently the number of requests or committed changes.

### Step 05. Current Signal Flow

**What You Are Doing:** Trace where the application creates records and where Docker retains them. This identifies the path your local evidence capture observes and the retention limits that still apply.

**Practical Walkthrough:** Follow the diagram from application code to stdout and then Docker's local log storage. Each boundary has a different owner: the application chooses fields, the formatter serializes them, and Docker retains output according to its logging settings. Your capture command reads this retained representation, not an immutable history of everything that ever happened.

Locate where serialization ends and Docker retention begins. The capture command can only retrieve records still available from that logging path. When a record is missing, check emission, formatting, capture interval, and retention separately rather than assuming the business event never occurred.

The lab map in Section 2 shows this relationship.

The process already updates metric state, but metric interpretation starts in Lab 7. No external event backend is introduced. Docker log retention can remove old lines; local capture is an investigation aid, not guaranteed archival delivery.

**Understanding the Result:** Missing local evidence can involve emission, capture timing, or retention. State which part of this path your observation actually checked.

### Step 06. Choose the Record Envelope Before Editing

**What You Are Doing:** Agree on the record fields before implementing the formatter. Consistent names and types let later scripts filter records without guessing how each line is structured.

**Practical Walkthrough:** Review the envelope fields and decide which identify the record, which correlate a request, and which describe the event. Keep types and names consistent because later assertions will address exact JSON keys. The allowlist limits structured extras; it does not make arbitrary message text or every possible exception safe automatically.

Read every field's type and purpose before implementing the formatter. Distinguish high-value identifiers from arbitrary payload data and note which extras are permitted. The later tests address these exact keys, so renaming a field casually would change the logging contract even if the output still looks readable.

| **Field**        | **Contract**                                                                    |
| ---------------- | ------------------------------------------------------------------------------- |
| `timestamp`      | UTC time when the Python log record was created                                 |
| `schema_version` | Envelope version `1`                                                            |
| `event_id`       | UUID identifying this log record; retained when formatting it again             |
| `event_name`     | Bounded event name from known application messages, otherwise `application_log` |
| `message`        | Human-readable message; application call sites use safe constants               |
| `request_id`     | Validated context-local request correlation, or null outside a request          |
| HTTP fields      | Method, normalized route, status and elapsed milliseconds when applicable       |
| Error fields     | Class and sanitized frame locations, not raw exception text                     |
| Trace fields     | Included only when a valid active span context exists                           |

An `event_id` is not the database transaction ID or a guarantee of durable delivery. `timestamp` approximates record creation, not the exact PostgreSQL commit instant. The event ID remains a log field; never turn it into a Prometheus or Loki index label.

The formatter's allowlist prevents accidental serialization of arbitrary `extra` fields. It does **not** sanitize secrets already interpolated into `message`. Keep call sites safe and test what is actually emitted.

**Understanding the Result:** The contract is a predictable object shape with explicit safety boundaries. A line merely parsing as JSON is only the first check.

### Step 07. Implement the Structured Record Envelope

**What You Are Doing:** Replace the formatter with the defined envelope while keeping the existing safe context and exception handling. The important behavior is stable record identity and bounded event naming, not merely JSON-looking output.

**Practical Walkthrough:** Apply the formatter replacement as a complete file, preserving the existing context integration called out in the instructions. The formatter gathers approved information into one object and serializes it as one line. Read the handling of optional context and exceptions carefully: ordinary records must still work when there is no active HTTP request.

Copy the complete Python file through the closing heredoc marker, then inspect the context lookup and exception handling. The formatter must produce a valid record both inside and outside a request. Keep message safety separate from the extra-field allowlist: admitting only selected keys does not sanitize arbitrary text placed inside them.

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

**Command Note:** `<<'PYTHON'` writes the following block literally until `PYTHON`. Quoting the delimiter prevents Bash from expanding `$variables` inside the generated file; creation and execution are separate steps.

The event-name set is intentionally small. Unknown framework messages remain useful human-readable logs without becoming a stream of unbounded event names. `setdefault` retains one ID on the LogRecord if multiple format operations occur.

Request context is read synchronously during formatting in this implementation. If you later introduce queued/background logging, capture the context into the record before crossing the task/thread boundary; do not assume a later consumer has the originating ContextVar.

**Understanding the Result:** Check that event identifiers belong to individual records while request identifiers can be shared. Avoid treating either as the database item's identity.

### Step 08. Add Context and Logging Contract Tests

**What You Are Doing:** Test behaviors that a few visible log lines cannot establish, especially isolation between concurrent requests. A mixed-up request context could produce plausible but misleading evidence.

**Practical Walkthrough:** Add the provided tests before rebuilding. The concurrent-request case deliberately overlaps work so that an incorrect shared variable would attach the wrong identity to a record. Other cases exercise envelope fields and admitted values. This is stronger than making two sequential requests, which might never expose a context leak.

Identify where the concurrency test holds requests open together and where it releases them. That overlap creates the condition under which shared mutable context could leak. Read the final associations between records and request IDs, not merely the total number of log lines, when deciding whether isolation is correct.

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

**Understanding the Result:** Passing tests establish the specified behaviors under those arrangements. They do not certify that all future messages are free of sensitive content.

### Step 09. Run Tests and Rebuild the Application

**What You Are Doing:** Run the tests and rebuild so the deployed app uses the new formatter. Then verify readiness before collecting records from that version.

**Practical Walkthrough:** Run the test block and resolve any assertion failure before building the application image. Rebuilding packages your edited source; recreating the service makes that image active. Wait for readiness afterward so the following evidence belongs to a running version with the new formatter, not to an older process or a startup failure.

Treat tests and lint as gates before deploying the formatter. After the rebuild, wait for readiness and start a fresh capture interval. This avoids mixing earlier startup or request records with output from the new process, which would make an apparent field mismatch difficult to attribute.

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

**Understanding the Result:** Use fresh records after deployment. Existing container history can still contain lines emitted under the previous logging format.

### Step 10. Establish the Shared Evidence Workflow

**What You Are Doing:** Create a separate evidence directory for each run and reusable capture functions. This preserves earlier attempts and gives requests, changes, and logs a common time window.

**Practical Walkthrough:** Create a new run directory and load the capture helpers in the current shell. They keep command evidence, responses, and relevant logs together under one run identity. Use the same run's time boundaries for later extraction, and retain failed attempts in their own directories instead of overwriting them with a successful retry.

Read what `start_lab` sets and where subsequent helpers write before using it. Keep the same `LAB_DIR` through a complete experiment and open a new run for a retry that needs separate evidence. The helper definitions are loaded into this shell; a new terminal needs the same sourcing sequence.

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

**Understanding the Result:** A useful evidence folder lets another reader identify the action, its timing, and its observed result. Files from different attempts should not silently be combined.

### Step 11. Predict the Records for One Create Request

**What You Are Doing:** Predict the business and HTTP records for one successful create. They describe different events, so they should share request context without becoming the same record.

**Practical Walkthrough:** Before sending the create request, write down which records should appear and why. A committed item creation and HTTP completion are distinct events even though they share request context. Also predict the response header and body so you can compare application behavior with its recorded representation instead of using logs as the only source of truth.

Predict the shared correlation fields and the fields that should differ between creation and completion records. Also predict the resource ID returned to the client. This creates a comparison across response and logs rather than assuming the logging layer alone proves successful persistence or response delivery.

Write your prediction before sending the request:

1. Which business event name should appear?
2. Which HTTP completion record should share its request ID?
3. Should the two records share an event ID?
4. Will item name, description, authorization or cookie values appear?
5. Should a valid trace ID appear while tracing is disabled?

The request ID joins records for one request. Record IDs distinguish the records themselves. A client can reuse an accepted request ID; it is correlation input, not authenticated identity or an idempotency key.

**Understanding the Result:** Expect correlation without identical event IDs. More than one record for this request is legitimate when each has a different stated purpose.

### Step 12. Send a Correlated Request and Preserve the Result

**What You Are Doing:** Send a request with a known valid ID and retain the complete HTTP result. That ID is your starting point for finding the related records afterward.

**Practical Walkthrough:** Use the chosen valid request ID in the request header and save the response headers and body through the capture workflow. Keep the returned item ID for later checks. The known request ID makes the log lookup selective; the item ID identifies the created resource, and these two values should remain conceptually separate.

Keep `RID` distinct from `ITEM_ID`: one identifies request context, the other identifies the created resource. Inspect the returned request-ID header before filtering logs. If the application replaces an invalid incoming identifier, the admitted response value is the correct key for finding the resulting records.

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

**Command Note:** `jq --arg` passes a shell value as a JSON-query string variable without inserting it into the query text. Where used, `-e` turns a false or null final result into a failing exit status.

**Expected Result:** HTTP 201 and a response `X-Request-ID` equal to your value. `item_created` and `request_completed` share that request ID but have different event IDs. The completion record reports 201. Additional records should be explained rather than automatically counted as duplicates.

Preserve HTTP response headers and body separately: the body intentionally contains your synthetic description, while the log should not.

**Understanding the Result:** A successful HTTP response proves the observed request succeeded. The next checks establish whether its associated records also satisfy the logging contract.

### Step 13. Verify Correlation and Safe Fields Structurally

**What You Are Doing:** Parse the records and assert their fields instead of relying on visual similarity. Compare correlation and safe content while keeping database state and logging evidence as separate claims.

**Practical Walkthrough:** Run the structural checks over parsed JSON objects from this run. Match the exact request ID, then inspect event types, identifiers, required fields, and allowed extras. Parsing and assertions avoid mistakes caused by visually scanning a long mixed log stream or matching an identifier that merely appears inside unrelated free text.

Read each assertion in the Python check and identify the envelope property it enforces. Parse only the captured JSON records, then select exact structured fields. If an assertion fails, inspect the matching records before loosening the check; a text search hit does not establish correct field placement or correlation.

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

**Understanding the Result:** Retain the assertion results alongside the response. Agreement supports correlation; it does not make a business record a substitute for checking database state.

### Step 14. Validate Request-ID Admission Rules

**What You Are Doing:** Try valid, invalid, missing, and duplicate request-ID inputs. This checks that client-provided context is admitted only under the application's explicit rules.

**Practical Walkthrough:** Send each documented header variation separately and compare the response's admitted identifier with the input. Invalid or absent inputs should follow the application's replacement policy; duplicate headers exercise a separate ambiguity. Keep the resulting records associated with their actual response IDs so that rejected input is not used as the lookup key accidentally.

Treat the candidate headers as separate test cases and retain the response ID for each. Compare a valid admitted value with replaced invalid or missing values, then inspect duplicate-header behavior independently. These cases test admission policy, so a generated replacement can be the correct outcome rather than a correlation failure.

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

**Understanding the Result:** The server's accepted identifier is the correlation value. Do not assume every client-supplied string is copied into the log context.

### Step 15. Inspect Context Propagation and Its Boundaries

**What You Are Doing:** Follow how request context is set, used, and reset. Cleanup matters because later or concurrent work must not inherit another request's identity.

**Practical Walkthrough:** Read the context setup and cleanup around request execution. Context must follow the intended asynchronous work and then be reset when that request ends. Identify the boundaries where work may no longer share the original context, and use the tests to demonstrate isolation instead of assuming all background activity belongs to the last request.

Locate the point where request context is installed and the cleanup that restores the prior context. Follow awaited work through that scope and compare it with detached or later work. The concurrency tests establish isolation under their controlled scheduling; source inspection explains why the cleanup boundary matters beyond those examples.

```bash
sed -n '28,115p' app/app/middleware.py
rg -n 'logger.info|logger.warning|logger.error' app/app/api.py app/app/cache.py app/app/main.py
```

The middleware sets a ContextVar token and request state before entering the handler. It adds the response header and resets the token in `finally`. Awaited calls within the request keep the context; the tests verify concurrent requests do not overwrite each other.

Background dependency probes normally have no request ID because they are not caused by a specific HTTP request. Do not manufacture one. A copied request context in a background task is also not proof that the task belongs to the same completed response lifecycle.

The API has no authenticated users. An accepted `X-Request-ID` must never be reported as an actor ID in an audit record.

**Understanding the Result:** Context propagation is a scoped behavior, not a global variable containing the current user request. Cleanup prevents misleading attribution later.

### Step 16. Record and Observe a Bounded Operational Change

**What You Are Doing:** Record the intended Redis change, run it, and inspect independent observations of its effect. An operator's change record proves intent was recorded, not that the intended failure or recovery occurred.

**Practical Walkthrough:** Record the planned Redis change before performing it, then collect its actual effects through dependency state, requests, and logs. Keep the operational record separate from the observation: writing 'stopping Redis' cannot prove that Redis stopped. Use the bounded experiment and recovery handling exactly as supplied so the comparison has a clear beginning and end.

Write the change record before the stop action, then use runtime state and responses to establish what actually happened. Keep the full recovery-protected block together. A planned change and a measured consequence belong on the same timeline, but neither should be rewritten afterward to match the other.

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

**Command Note:** `trap ... EXIT` schedules cleanup when that shell exits. Keep it in the same block as the fault; the explicit recovery checks afterward confirm that restoration actually succeeded.

**Expected Result:** the individual read succeeds through PostgreSQL, readiness stays HTTP 200/degraded, and a sanitized cache failure record appears. Some probe failures have null request IDs; a request-scoped cache failure should carry its request context.

Dependency transition logs are emitted by a periodic loop. A sufficiently short outage might fall entirely between two probes, so the absence of a transition log does not negate the request failure record and captured readiness response.

**Understanding the Result:** Compare the predicted observer results with the actual ones. Differences are findings to investigate, not reasons to rewrite the original prediction.

### Step 17. Restore and Prove the End State

**What You Are Doing:** Restore the dependency and confirm the original business path succeeds. Keep both the failure observations and recovery evidence in the same run record.

**Practical Walkthrough:** Restore Redis and repeat the normal business check used before the failure. Capture recovery in the same run folder so the evidence shows both the interruption and its end. If the request still fails, inspect readiness and dependency state before continuing; merely issuing a start command does not establish successful service recovery.

Check the readiness body and a useful request after Redis restoration, then remove only this lab's fixture. Capture the recovered interval before finalizing the evidence set. If normal work still fails, preserve that result and diagnose it rather than recording recovery solely because the start command returned successfully.

```bash
api -fsS "$APP_URL/health/ready" | tee "$LAB_DIR/recovered.json" | jq .
api -fsS -X DELETE "$APP_URL/api/v1/items/$ITEM_ID" -o /dev/null
baseline_check
api -fsS "$APP_URL/api/v1/items/$CHECKPOINT_ID" | jq '{id,name,price}'
capture_app_logs
```

Keep only the course checkpoint item. Stopping Redis for this drill did not require editing the application's readiness policy, deleting volumes, or enabling a second log transport.

**Understanding the Result:** A fresh successful request is recovery evidence for its tested path. Preserve earlier failure records rather than removing them from the report.

### Step 18. Evidence Integrity and Honest Conclusions

**What You Are Doing:** Create integrity hashes for the captured files and document their limits. They help detect changes relative to the saved manifest without turning editable local evidence into an authenticated audit system.

**Practical Walkthrough:** Generate the manifest after finishing the capture and retain it with the evidence files. A hash summarizes a file's bytes, allowing a later comparison to detect changes against that saved value. If both a file and its manifest can be edited together, however, this arrangement cannot independently establish who created the evidence or when.

Create the manifest after all intended evidence files have been written. A later byte change should produce a different hash, so avoid editing captured files after manifest generation without recording a new version. Describe the manifest as a consistency check against saved bytes, not independent proof of authorship or event time.

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

**Understanding the Result:** Describe the result as local integrity checking. Authenticity, completeness, and tamper-resistant audit retention require guarantees this exercise has not implemented.

## 4. Troubleshooting and Diagnosis

Start with the first unexpected result. Record the exact symptom, identify which component produced it, and use the checks below before repeating the experiment. After a fix, repeat the original check and confirm recovery.

### Troubleshooting Runbook

| **Symptom**                              | **Investigation and Correction**                                                                                        |
| ---------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| New envelope fields are absent           | Confirm the app image was rebuilt and recreated; inspect a new request, not an old line                                 |
| JSON parsing fails                       | Inspect `unparsed-lines.txt`; preserve CLI/startup lines separately and remove Compose prefixes with the supplied flags |
| Request IDs are null everywhere          | Send a business request, inspect middleware ordering, and verify you are not only reading background probe logs         |
| Two requests seem mixed                  | Compare response headers and the concurrency regression; ensure the client did not deliberately reuse the same ID       |
| No trace fields                          | Expected while OTel is disabled; do not add fake trace IDs                                                              |
| A secret appears in a log                | Inspect interpolation at the logging call site; an allowlist cannot repair a secret already in `message`                |
| No dependency transition line            | Check probe interval and outage duration; compare direct health and request evidence                                    |
| Redis remains stopped after interruption | Run `dc start redis`, then `wait_ready`; capture the manual recovery action                                             |
| Old and new JSON coexist                 | Logs span multiple application versions; filter by `schema_version` and the run start time                              |

Before changing code, identify whether the problem is event generation, context, formatting, capture, timing or an incorrect expectation. Do not solve every evidence gap by enabling DEBUG or printing payloads.

## 5. Review and Practice

Answer the knowledge questions in your own words before reading the answer guide. For the scenario, name the evidence you would collect and the conclusion each observation would support.

### Knowledge Check

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

#### Answer Guide

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

### Professional Scenario Exercise

An operator claims that an absent `item_created` log proves a customer write never committed. Write a response that separates transaction state, log emission, response delivery and retention. Propose the smallest safe verification sequence and explain why blindly retrying POST could create a second item.

## 6. Completion Checklist

Mark each item only after you can point to its result or evidence file. Complete any cleanup shown here before moving on.

### Observable Completion Criteria

- [ ] The new event envelope is emitted and tests pass.
- [ ] A create is correlated across HTTP, business log and direct SQL.
- [ ] Distinct records have distinct event IDs, while request IDs correlate related work.
- [ ] Invalid and duplicate incoming IDs are replaced.
- [ ] Concurrent request contexts remain isolated.
- [ ] Synthetic credentials/body values do not appear in captured logs.
- [ ] The Redis change has intent, observed impact and recovery evidence.
- [ ] The notebook distinguishes events from log records, metric observations, span events and samples.
- [ ] Only the three baseline services run; the checkpoint item survives.

## 7. Production Context and Next Lab

### Production Implications

Structured logs improve investigation when the event semantics are clear. Ordinary stdout logging is neither exactly-once event delivery nor a compliance audit system. Request IDs are untrusted correlation input; record IDs are high-cardinality fields. Stronger audit guarantees need authenticated actors, governed retention, integrity controls and independent storage. Those responsibilities cannot be proven by a JSON formatter on one host.

### End State and Transition

Leave the three-service baseline running with the new formatter and tests. Keep `lab-notes/evidence.sh`; source it after `session.sh` in later labs. Preserve the course checkpoint item.

Next: [Lab 07 — Raw OpenMetrics Before Prometheus](Lab-07.md). You will compare individual event records with numeric instrument state, add actual format negotiation, and read the wire representation before introducing a scraper.