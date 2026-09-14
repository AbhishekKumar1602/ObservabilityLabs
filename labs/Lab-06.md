# Lab 06: Events, Structured Logging, Request IDs, and Evidence Capture

## 1. Purpose and Learning Outcomes

You will make application logs easier to connect to the request that produced them. First define consistent JSON fields, then test that overlapping requests keep their IDs separate. Finally, record a small operational change with its responses and logs so another person can follow what you intended, what you did, and what actually happened.

> **Primary Objective:** Separate real events from records describing them, implement a safe JSON log format with a version number, test that request context stays with the correct request, and save evidence that another person can review and reproduce.

Earlier labs showed what the Items API does and how it recovers. Now name its evidence carefully. A database commit is something that happens. A log record describes that occurrence. A container log line is the record converted into text. The line and the real database action are related, but they are not the same thing.

Extend the existing logger, connect HTTP responses to application records, test request correlation and safe fields, and capture a short dependency change. You will inspect local output before adding a log backend.

## 2. Starting Point and Lab Map

Open the complete application repository before using this guide. Unless a step says otherwise, run commands in Bash from the repository's top-level folder. Follow the starting checks in order because later commands depend on the helper functions and running services they prepare.

Read each explanation before running its commands. Run one complete code block, check the result, and then continue. In your notebook, save the commands, what happened, why you think it happened, and anything that differed from the expected result.

### Key Terms

| **Term**     | **Explanation**                                                                            |
| ------------ | ------------------------------------------------------------------------------------------ |
| Event        | Something that happened. A log describes the event but is not the event itself.            |
| Request ID   | A value used to find and connect records associated with a request.                        |
| Log Envelope | The shared set of fields on log records, including time, level, event name, and IDs.       |

### Lab Map

The arrows show how the parts connect and where a request can take different paths. Use this map to understand the design. It is not a recording of a request that actually ran.

```mermaid
flowchart TD
    HTTP["HTTP Request + Optional Request ID"] --> Context["ASGI Context"]
    Context --> Handler["Item Handler"]
    Handler --> DB["PostgreSQL Commit"]
    Handler --> Business["Business Log Record"]
    Context --> Completion["HTTP Completion Record"]
    Business --> Formatter["JSON Formatter"]
    Completion --> Formatter
    Formatter --> Output["stdout and Docker json-file"]
    Output --> Notes["Local Evidence Capture"]
```

## 3. Guided Walkthrough

### Step 01. Inherited State and Scope

**What You Are Doing:** Continue with the recovered baseline and previous code changes. Add records to the request paths you already understand, while keeping log backends stopped.

**Practical Walkthrough:** Use Lab 05's working app, the database-error fix, and the checkpoint. They give you known behavior to compare with the new logs. Keep export disabled so you first prove what the app writes locally, before adding log transport and search services.

Check the checkpoint request before describing it through logs. This lab follows only local standard output and Docker's capture of it. That keeps a missing app record separate from later problems sending or indexing logs elsewhere.

Complete Labs 1–5, including Lab 2's database connection-error regression. Keep the course checkpoint item and the baseline overlay. Only `app`, `postgres` and `redis` should be running.

Use the same Linux Docker host, Bash, curl, jq, Python 3, Git, Make and ripgrep. All commands run at repository root. Keep OpenTelemetry export and Pyroscope disabled, and do not start Loki or Collector.

Kubernetes Events, span events, audit records, and profiles appear here as concepts to compare. You do not need to build a Kubernetes cluster, event broker, tracing feature, or audit subsystem for this lab.

**Understanding the Result:** Only the app and its two data dependencies should run. Extra telemetry services would complicate the evidence without testing the local log format itself.

### Step 02. Clean Starting-State Check

**What You Are Doing:** Check the known item and review existing logger edits before replacing code. Both the deployed app and the local source need to be understood.

**Practical Walkthrough:** Load the helpers, run the baseline check, and read the saved item. Then inspect logger and middleware code and any local diff. A working container may still contain an older image, so it cannot tell you whether unbuilt source edits are waiting on the host.

Review the source the replacement would overwrite. A successful request checks the running image; the diff shows what the next build will contain. Resolve differences first so later log changes can be attributed to the new formatter accurately.

```bash
source lab-notes/session.sh
baseline_check
CHECKPOINT_ID=$(cat lab-notes/checkpoint-item-id.txt)
api -fsS "$APP_URL/api/v1/items/$CHECKPOINT_ID" | jq '{id,name,price}'
git diff -- app/app/logging_config.py
rg -n 'request_id_context|SAFE_ID|request_completed' app/app/middleware.py
```

**Expected Result:** the checkpoint exists and exactly three services are running. Review local logger edits before replacing the file and save unrelated work in a Git checkpoint.

**Understanding the Result:** Continue once the request works and you understand the source changes. Preserve unrelated edits before applying the replacement.

### Step 03. Measurable Learning Objectives

**What You Are Doing:** Separate record identity, request context, and record contents. Each helps answer a different question when investigating a completed request.

**Practical Walkthrough:** Request IDs connect related work. Event IDs distinguish separate records. Context tells the logger which request a record belongs to. Plan a check for each property; one JSON line that looks correct cannot prove all of them.

Test record identity, request correlation, and concurrent isolation separately. Two records may share a request ID but should have different event IDs. Match each assertion to the property it checks instead of assuming valid JSON also proves correct ownership.

By completion, you should be able to:

- distinguish a real event from a record or numeric total describing it;
- explain when one occurrence can produce several records, or no retained record;
- separate event ID, request ID, trace ID and item ID;
- emit one parseable JSON object per application log line;
- preserve existing trace correlation while keeping trace export disabled;
- prove accepted, rejected and duplicate request-ID behavior;
- show two concurrent requests keep separate context;
- capture operational changes and a bounded dependency failure;
- explain what an allowed-field list protects and why ordinary logs are not a complete audit trail; and
- restore business readiness and retain evidence without credentials.

**Understanding the Result:** Keep example records and test results. Examples show what happened in one run; tests deliberately arrange edge cases that a normal run may not expose.

### Step 04. The Event and Evidence Model

**What You Are Doing:** Separate an occurrence from the ways you record it. A single request may create several logs and metric updates, so their counts need not match.

**Practical Walkthrough:** Follow a create request through receipt, commit, and response completion. Then identify the JSON records describing those stages. A counter can combine many completions into one number, while several logs can describe one request. Each representation keeps some details and leaves others out.

Name the real occurrence first, then the evidence describing it. The create record and HTTP completion record refer to different stages of the same request. Compare their fields and times without assuming each line represents an entire transaction or that every event left a retained line.

| **Term**           | **Meaning**                                                            | **Example in This Platform**                            |
| ------------------ | ---------------------------------------------------------------------- | ------------------------------------------------------- |
| Real Event         | Something actually happens or changes                                  | PostgreSQL commits an item insert                       |
| Event Record       | Data describing something that happened                                | A record named `item_created`                           |
| Log Record         | The logging object holding a level, message, and context               | Python `logging.LogRecord`                              |
| Log Line           | The record encoded as bytes in an output stream                        | One JSON object followed by a newline                   |
| Metric Observation | An action that updates a measurement instrument                        | Incrementing a request counter or recording a duration  |
| Metric Sample      | A numeric value identified by labels and a time in the metrics system  | The counter value Prometheus stores when it scrapes     |
| Span Event         | A named observation with a timestamp attached to a trace span          | A retry noted inside a request span in a later lab      |
| Change Event       | An operational or configuration action                                 | Stopping Redis or deploying an image                    |
| Audit Event        | Evidence of a controlled action attributed to an identified actor      | An authenticated administrator changing permissions     |
| Kubernetes Event   | A Kubernetes API resource describing a lifecycle occurrence or warning | A pod scheduling failure; this lab has no cluster       |
| Profile Sample     | A sampled view of code stacks or resource use                          | A Python CPU stack sampled by Pyroscope later           |

One real event does not guarantee one log entry. A POST may produce both `item_created` and `request_completed`. The process could also crash after commit before writing either record. A metric counter may include a completion even if its detailed log was later lost.

A profile samples execution; it does not list every business action. A span event survives only if its tracing data is recorded and retained. A Kubernetes Event is a specific API object, not a general name for container logs. These differences matter when explaining missing evidence.

**Understanding the Result:** Say which event type you are counting. The total number of log lines is not automatically the number of requests or commits.

### Step 05. Current Signal Flow

**What You Are Doing:** Follow records from the app to Docker's local storage. This identifies what your capture command can retrieve and what may already have been removed.

**Practical Walkthrough:** The app chooses fields, the formatter converts the record into JSON, and stdout carries the line to Docker. Docker keeps output according to its logging settings. Your capture reads what is still retained, not a permanent complete history of every occurrence.

Find where formatting ends and Docker storage begins. When a record is missing, check whether it was emitted, formatted correctly, inside your capture window, and still retained. Missing output alone does not prove the business event never happened.

The lab map in Section 2 shows this relationship.

The app already updates metrics, but Lab 7 begins interpreting them. No external event backend is needed here. Docker log rotation can remove older lines, so local captures help investigations without guaranteeing permanent delivery or storage.

**Understanding the Result:** A missing record may involve generation, formatting, capture timing, or retention. Identify which part you checked before drawing a conclusion.

### Step 06. Choose the Record Envelope Before Editing

**What You Are Doing:** Define shared log fields before changing the formatter. Consistent keys and types let later scripts inspect records reliably.

**Practical Walkthrough:** Classify the fields as record IDs, request-correlation values, or event details. Keep their names and types stable because assertions use exact JSON keys. The allowlist restricts extra fields; it does not automatically remove a secret already placed in message text.

Read each field's purpose before writing code. Separate useful IDs from arbitrary request payloads and check which extras are allowed. Changing a key name changes the logging contract, even if the line still looks readable to a person.

| **Field**        | **Contract**                                                                    |
| ---------------- | ------------------------------------------------------------------------------- |
| `timestamp`      | UTC time when the Python log record was created                                 |
| `schema_version` | Envelope version `1`                                                            |
| `event_id`       | UUID for this log record; formatting the same record again keeps the same ID    |
| `event_name`     | One of the known event names, or `application_log` for other messages           |
| `message`        | Readable message text; application logging calls use safe constant messages     |
| `request_id`     | The validated ID from this request's context, or null outside a request         |
| HTTP Fields      | Method, route template, status, and elapsed milliseconds where relevant         |
| Error Fields     | Error class and safe source-frame locations, without raw exception text         |
| Trace Fields     | Added only when a valid active trace-span context exists                        |

`event_id` identifies a log record, not a database transaction or guaranteed delivery. `timestamp` describes when the log record was created, not the exact commit instant. Keep the event ID as a log field; using every unique value as a Prometheus or Loki index label would create unbounded label growth.

The formatter's allowed-field list prevents arbitrary `extra` data from being copied into JSON. It does **not** remove a secret already inserted into `message`. Keep logging call sites safe and test their actual output.

**Understanding the Result:** Valid JSON is only the first requirement. The record must also use predictable fields, correct IDs, and the intended safety rules.

### Step 07. Implement the Structured Record Envelope

**What You Are Doing:** Replace the formatter while preserving request context and safe exception handling. Test stable record IDs and a limited set of event names, not just JSON syntax.

**Practical Walkthrough:** Write the complete formatter file. It collects approved values into one object and emits one line. Read how it handles missing request context and exceptions. Startup and background records must still work when no HTTP request is active.

Copy through the final heredoc marker, then review context lookup and exception handling. The formatter must work inside and outside requests. An allowed key can still contain unsafe text, so field allowlisting and message safety remain separate responsibilities.

After reviewing the existing diff, replace the formatter file. It retains stdout, request context, safe exception frames, and optional trace fields while adding a stable record ID and explicit event name.

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

**Command Note:** `<<'PYTHON'` writes the following code literally until the closing `PYTHON` line. Quoting prevents Bash from expanding `$variables`. Creating the file does not execute the Python yet.

The set of event names is deliberately small. Other framework messages remain readable without each unique message becoming a new event name. `setdefault` stores the ID on the LogRecord so repeated formatting of that same record keeps it.

This formatter reads request context immediately in the same execution flow. If logging is later queued to another task or thread, copy the context into the record before that handoff. A later consumer may not have the originating request's ContextVar values.

**Understanding the Result:** Event IDs distinguish records; request IDs connect related records. Neither should be confused with the ID of the database item.

### Step 08. Add Context and Logging Contract Tests

**What You Are Doing:** Test behavior that a few visible log lines cannot prove, especially overlapping requests. Incorrect shared context can create records that look plausible but belong to the wrong request.

**Practical Walkthrough:** Add the tests before rebuilding. The concurrency case holds requests open at the same time so a wrongly shared variable can be detected. Other cases check envelope fields and accepted IDs. Sequential requests might never expose that context leak.

Find where the test overlaps and releases requests. Then read the assertions mapping each record to its request ID. Counting lines alone would not prove that the records belong to the right requests.

Create `app/tests/test_logging.py`, the only new application test file in this lab. It checks concurrent request isolation, duplicate-ID handling, and stable record IDs—behaviors that visual stdout inspection cannot reliably prove.

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

**Understanding the Result:** Passing tests establish these particular cases. They do not guarantee that every message added in the future will be free of sensitive data.

### Step 09. Run Tests and Rebuild the Application

**What You Are Doing:** Test and rebuild the app so the new formatter is active. Wait for readiness before gathering evidence from that version.

**Practical Walkthrough:** Fix test failures before building. The build packages the source and recreation starts it. After readiness, begin a fresh capture window so your records come from the new formatter rather than an older image or incomplete startup.

Treat tests and lint as prerequisites for deployment. After the rebuild, wait for readiness and record a new start time for capture. Mixing old and new records can otherwise make valid changes look like inconsistent fields.

```bash
make test
make lint
git diff --check
git diff --stat
dc up -d --build --no-deps app
baseline_check
```

Fix any failed test before continuing. The rebuild installs the changed source; a restart alone does not copy new files into an existing image. PostgreSQL rows remain on their named volume.

The concurrent test uses isolated resources and checks correctness, not throughput. Its fake authorization header and request-body values are canaries: recognizable test values used to detect accidental logging.

**Understanding the Result:** Inspect newly emitted records after deployment. Retained history can still contain lines written by the earlier format.

### Step 10. Establish the Shared Evidence Workflow

**What You Are Doing:** Give each run its own evidence folder and load reusable capture helpers. This keeps retries separate and puts requests, changes, and logs within a known time window.

**Practical Walkthrough:** Start a run directory and load the helper functions in this shell. Keep commands, responses, and selected logs together under that run. Use its time boundaries for capture. Preserve a failed attempt instead of overwriting it with the successful retry.

Read what `start_lab` initializes and where the helpers write. Keep one `LAB_DIR` for a complete experiment. Start a separate run when you need separate retry evidence. Reload the helpers when opening another terminal because functions belong to that shell session.

Create the helper used through Labs 6–15. Each run gets a private folder, so a later attempt does not overwrite an earlier one. Change records describe your action and result, but editable local files are not an authenticated or tamper-resistant audit trail.

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

`capture_app_logs` saves non-JSON lines separately in `unparsed-lines.txt`. Inspect them; they may be startup or command diagnostics, and should not be silently treated as structured app records. `umask 077` makes newly created evidence private.

Setup should already ignore `lab-notes/` in Git. Check with `git check-ignore lab-notes/evidence.sh`. Do not save the unfiltered Compose model or full container environment because those can contain secrets.

**Understanding the Result:** Another reader should be able to find the action, time, and observed result in the run folder. Do not silently merge evidence from different attempts.

### Step 11. Predict the Records for One Create Request

**What You Are Doing:** Predict the item-created and HTTP-completed records for one POST. They should share request context but remain two distinct records.

**Practical Walkthrough:** Write down the expected records before sending the request. Item creation and request completion describe different stages. Predict the body and response header too, so you can compare the actual HTTP result with the logs rather than treating logs as the only truth.

Identify which correlation fields should match and which event fields should differ. Also predict the returned item UUID. This compares the response with the records without assuming that logging alone proves either committed data or full client receipt.

Write your prediction before sending the request:

1. Which business event name should appear?
2. Which HTTP completion record should share its request ID?
3. Should the two records share an event ID?
4. Will item name, description, authorization or cookie values appear?
5. Should a valid trace ID appear while tracing is disabled?

The request ID groups related work; event IDs distinguish individual records. Clients can reuse an accepted request ID, so it is neither an authenticated identity nor a key that prevents duplicate writes.

**Understanding the Result:** Shared request context with different event IDs is correct. Several records can describe one request when they record different events.

### Step 12. Send a Correlated Request and Preserve the Result

**What You Are Doing:** Send a known valid request ID and save the full HTTP result. Use the accepted response ID to locate the related log records.

**Practical Walkthrough:** Put your chosen ID in the header and capture response headers and body. Save the new item ID separately. The request ID helps find records for this operation; the item ID identifies the stored resource.

Keep `RID` and `ITEM_ID` separate. Check the response's request-ID header before searching logs. If the input ID was invalid and the app replaced it, use the returned accepted ID for correlation.

```bash
RID="lab06-$(new_uuid)"
api -fsS -D "$LAB_DIR/create-headers.txt" \
  -H "X-Request-ID: $RID" \
  -H 'Authorization: Bearer SYNTHETIC_DO_NOT_LOG_LAB06' \
  -H 'Cookie: example=SYNTHETIC_DO_NOT_LOG_LAB06' \
  -H 'Content-Type: application/json' \
  -d '{"name":"Lab 06 Event Subject","description":"SYNTHETIC_DO_NOT_LOG_LAB06","price":"6.00"}' \
  "$APP_URL/api/v1/items" -o "$LAB_DIR/item.json"
ITEM_ID=$(jq -er '.id' "$LAB_DIR/item.json")
printf '%s\n' "$RID" > "$LAB_DIR/request-id.txt"
capture_app_logs
jq -c --arg rid "$RID" 'select(.request_id == $rid) | {event_id,event_name,request_id,message,"http.status_code":.["http.status_code"]}' \
  "$LAB_DIR/app.jsonl"
```

**Command Note:** `jq --arg` passes a shell value as a JSON-query string variable without inserting it into the query source. Where used, `-e` makes a false or null final result fail the check.

**Expected Result:** HTTP 201 with `X-Request-ID` matching your accepted value. `item_created` and `request_completed` share that request ID but have different event IDs. The completion record reports 201. Explain any additional records by event type before calling them duplicates.

Save headers and body separately. The response body is allowed to contain the test description you submitted; the log should not copy it.

**Understanding the Result:** You have observed a successful HTTP request. The next checks separately verify that its records use the intended fields and correlation.

### Step 13. Verify Correlation and Safe Fields Structurally

**What You Are Doing:** Parse JSON and assert exact fields instead of judging logs by appearance. Keep logging checks separate from proof of stored database state.

**Practical Walkthrough:** Select records from this run using the exact structured request-ID field. Check event types, IDs, required fields, and allowed extras. This avoids confusing unrelated free text with a field match in a long log stream.

Read what each assertion checks. If one fails, inspect the selected records before weakening it. Finding the ID somewhere in a text line does not prove that it belongs in the correct structured field or is attached to the right request.

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

The row and two logs answer different questions. SQL shows committed data visible now. The business record shows the app reached its post-commit logging point. The completion record shows that the server completed that request path, but cannot prove the client consumed every response byte.

Trace fields should be absent while tracing is disabled. Request IDs still provide useful links between local records before distributed tracing is added.

**Understanding the Result:** Keep assertion results with the HTTP response. Matching records support correlation, while direct SQL supplies separate evidence of the committed row.

### Step 14. Validate Request-ID Admission Rules

**What You Are Doing:** Try valid, invalid, missing, and duplicate request-ID headers. Check which input the app accepts and when it generates a replacement.

**Practical Walkthrough:** Send each header case separately and compare input with the returned ID. Invalid or absent values should be replaced according to the app's rules. Multiple headers are another case to test. Search resulting logs by the actual response ID, not by rejected input.

Save each case's returned ID. Compare an accepted valid value with generated replacements, then examine duplicate headers separately. Replacement can be correct admission behavior rather than a failure to correlate the request.

```bash
for candidate in 'valid.lab06_id-1' 'contains a space'; do
  api -fsS -D - -o /dev/null -H "X-Request-ID: $candidate" \
    "$APP_URL/api/v1/items?limit=1"
done
api -fsS -D - -o /dev/null \
  -H 'X-Request-ID: first' -H 'X-Request-ID: second' \
  "$APP_URL/api/v1/items?limit=1"
```

A valid single header is accepted. Whitespace, a value longer than 64 characters, a missing value, or multiple request-ID headers causes a generated UUID. Accepted incoming values use letters, digits, dot, underscore, and hyphen, and must begin with a letter or digit.

Do not paste raw newlines into HTTP headers to demonstrate injection. The isolated tests cover invalid inputs without depending on how curl or a proxy handles control characters.

**Understanding the Result:** The server's admitted ID is the value used for correlation. The app does not blindly copy every client-supplied string into log context.

### Step 15. Inspect Context Propagation and Its Boundaries

**What You Are Doing:** Trace where request context is set and cleared. Cleanup keeps later or concurrent work from being assigned the wrong request ID.

**Practical Walkthrough:** Read context setup around the handler and reset afterward. Awaited work should keep the intended context. Detached or later tasks have different lifetimes, so do not assume all background work belongs to the most recent request. Use the tests to check concurrent isolation.

Find where middleware installs the context and where cleanup restores the previous value. Follow the awaited calls inside that scope and compare them with separately scheduled work. Tests show isolation for their controlled overlap; source review explains the cleanup mechanism.

```bash
sed -n '28,115p' app/app/middleware.py
rg -n 'logger.info|logger.warning|logger.error' app/app/api.py app/app/cache.py app/app/main.py
```

Before the handler runs, middleware sets request state and a ContextVar token. It adds the response header and resets the token in `finally`. Awaited calls retain the context, and the tests check that concurrent requests do not overwrite one another's values.

Background dependency probes normally have no request ID because no particular HTTP request caused them. Leave it null rather than inventing one. A background task may inherit context, but that alone does not prove its lifetime belongs to the same completed HTTP response.

This API has no authenticated users. An accepted `X-Request-ID` is client-supplied correlation data, not a verified actor ID for auditing.

**Understanding the Result:** Context is scoped to execution, not a single global “current request” variable. Proper reset prevents later records from being attributed to the wrong work.

### Step 16. Record and Observe a Bounded Operational Change

**What You Are Doing:** Record the planned Redis action, carry it out, and inspect its effects independently. A note saying you intended to stop Redis does not prove Redis actually stopped.

**Practical Walkthrough:** Write the change record before the action. Then check service state, requests, and logs to see the result. Keep intent separate from observation and retain the complete time-limited outage and recovery block so the experiment has a clear start and finish.

Save the planned change first, then measure the actual consequences. Keep the recovery-protected block intact. Put plan and result on the same timeline without rewriting the original prediction to match what later happened.

Predict which evidence records **intent**, which shows a failed cache operation, and which reports current dependency state. Each answers a different question; the change note cannot establish its own success.

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

**Command Note:** `trap ... EXIT` schedules cleanup on shell exit. Keep it with the fault and use the explicit later checks to prove that recovery succeeded.

**Expected Result:** the individual GET succeeds through PostgreSQL, readiness returns HTTP 200/degraded, and a safe cache-failure record appears. Background probe records may have null request IDs. A failure inside the request should carry that request's context.

Dependency-transition logs come from a periodic check. A short outage may begin and end between checks, producing no transition record. That absence does not cancel the captured readiness response or request-scoped cache-error evidence.

**Understanding the Result:** Compare your predicted observations with the actual records. Differences are findings to investigate, not reasons to change the saved prediction afterward.

### Step 17. Restore and Prove the End State

**What You Are Doing:** Restore Redis and repeat useful app work. Keep failure and recovery evidence together in the same run.

**Practical Walkthrough:** After starting Redis, repeat the normal business check and save the recovery result. A successful start command is only an action; readiness and requests show whether it worked. If the request still fails, investigate before continuing.

Check the readiness body and a useful request, then delete only this lab's item. Capture the recovered interval before finishing the evidence folder. If work still fails, retain that result instead of reporting recovery solely because the start command returned zero.

```bash
api -fsS "$APP_URL/health/ready" | tee "$LAB_DIR/recovered.json" | jq .
api -fsS -X DELETE "$APP_URL/api/v1/items/$ITEM_ID" -o /dev/null
baseline_check
api -fsS "$APP_URL/api/v1/items/$CHECKPOINT_ID" | jq '{id,name,price}'
capture_app_logs
```

Keep the original course checkpoint. This Redis outage does not require changing readiness policy, deleting volumes, or adding another log transport.

**Understanding the Result:** A new successful request proves recovery for that path. Keep earlier failures as part of the evidence rather than removing them from the run.

### Step 18. Evidence Integrity and Honest Conclusions

**What You Are Doing:** Hash the captured files and explain what those hashes can prove. They help detect later byte changes relative to the saved manifest, but do not create an authenticated audit system.

**Practical Walkthrough:** Generate the manifest after capture is complete. A hash is a compact value calculated from a file's bytes; comparing it later can reveal a changed file. However, someone able to edit both the file and manifest can replace both, so this does not independently prove author or time.

Write all intended evidence before making the manifest. Later edits normally change the hash, so record a new version if you revise a file. Describe the manifest as a comparison against saved bytes, not as independent proof of who recorded the event or when it happened.

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

Hashes help detect changes against this saved manifest. An operator who can edit both evidence and manifest can rewrite both, so this is not signed or immutable audit storage. Update the manifest deliberately when you later revise your notebook.

Record the clock source, time window, and capture method. Crashes, log rotation, buffering, or sampling can leave gaps. No surviving record does not necessarily mean no event occurred.

**Understanding the Result:** This is local integrity checking. It does not establish authenticity, completeness, or tamper-resistant long-term audit storage.

## 4. Troubleshooting and Diagnosis

Begin with the first result that differed from your prediction. Save the exact error or symptom and identify which service or command produced it. Use the checks below, make the needed fix, and repeat the original check to confirm the problem is resolved.

### Troubleshooting Runbook

| **Symptom**                              | **Investigation and Correction**                                                                                        |
| ---------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| New envelope fields are absent           | Check that the edited image was built and the app recreated; inspect a newly generated request record                   |
| JSON parsing fails                       | Read `unparsed-lines.txt`; keep startup/CLI lines separate and use the supplied flags to remove Compose prefixes        |
| Request IDs are null everywhere          | Send an item request, check middleware order, and make sure you are not inspecting only background probes               |
| Two requests seem mixed                  | Compare returned headers and the concurrent test; check whether the client reused the same ID                           |
| No trace fields                          | Expected with OTel disabled; do not invent trace IDs                                                                    |
| A secret appears in a log                | Inspect the logging call; an allowed-field list cannot remove a secret already inserted into `message`                  |
| No dependency transition line            | Compare the outage duration with the probe interval, then use direct health and request evidence                        |
| Redis remains stopped after interruption | Run `dc start redis` and `wait_ready`, then record the manual recovery                                                  |
| Old and new JSON coexist                 | The capture contains multiple app versions; filter by `schema_version` and the run start time                           |

First locate the problem: event generation, context, formatting, capture, timing, or an incorrect expectation. Enabling DEBUG everywhere or logging full payloads is not a suitable response to every missing record.

## 5. Review and Practice

Answer the questions in your own words before reading the answers. For the scenario, say what you would inspect and exactly what each result would tell you.

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

1. Yes. A record may never be emitted, may remain buffered, or may be lost during transport or retention.
2. They record different observations within the same request, so they share request context but not record identity.
3. No. The counter stores a numeric total, not the IDs of individual requests.
4. No. New record IDs keep appearing, so using them as labels creates continually growing distinct values.
5. Those background checks were not caused by a specific HTTP request.
6. No. The logging call must avoid placing secrets in message text in the first place.
7. A span event is attached to a trace span and depends on that span being recorded and retained.
8. No. A CPU profile samples execution stacks and resource use; it is not a complete list of item creates.
9. No. This app has no authenticated audit actor, and the local change file is editable.
10. No. They are Kubernetes API objects mentioned for comparison; this lab uses Compose.

### Professional Scenario Exercise

An operator says that no `item_created` log means the customer's write never committed. Explain why database state, log emission, response delivery, and log retention must be checked separately. Propose a small safe verification sequence and explain how retrying POST without checking could create another item.

## 6. Completion Checklist

Tick an item only when you can show its result or saved evidence. Finish the cleanup instructions before starting the next lab.

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

Structured logs help when their event names and fields have clear meanings. Ordinary stdout does not guarantee that every event is delivered exactly once or provide a complete compliance audit system. Request IDs come from untrusted correlation input, and record IDs create many unique values. Stronger auditing needs verified actors, controlled retention, integrity protection, and independent storage; a JSON formatter on one host does not supply those guarantees.

### End State and Transition

Keep the three-service baseline running with the new formatter and passing tests. Retain `lab-notes/evidence.sh` and load it after `session.sh` in later labs. Preserve the checkpoint item.

Next: [Lab 07 — Raw OpenMetrics Before Prometheus](Lab-07.md). You will compare event records with numeric metric state, support actual format negotiation, and inspect the endpoint's text before adding a scraper.