# Lab 04: Liveness, Readiness, and Dependency Health

## 1. Purpose and Learning Outcomes

You will separate three questions often called “health”: can the app process answer, can it perform the work required for normal traffic, and can each dependency respond to its check? You will briefly stop Redis, PostgreSQL, and then both. Comparing the results explains why health indicators can disagree and why recovery needs a real item request too.

> **Primary Objective:** Define and test separate rules for process liveness, application readiness, and individual dependency checks. Show why PostgreSQL is required here and why Redis can fail while the app continues serving useful work.

A green container indicator, a successful cached GET, and a ready application do not prove the same thing. This lab makes their meanings clear and tests every combination of dependency availability without adding a monitoring backend.

This lab follows the database and cache labs because you must understand what the application actually needs before deciding what its health checks should report.

## 2. Starting Point and Lab Map

Open the complete application repository before using this guide. Unless a step says otherwise, run commands in Bash from the repository's top-level folder. Follow the starting checks in order because later commands depend on the helper functions and running services they prepare.

Read each explanation before running its commands. Run one complete code block, check the result, and then continue. In your notebook, save the commands, what happened, why you think it happened, and anything that differed from the expected result.

### Key Terms

| **Term**  | **Explanation**                                                                           |
| --------- | ----------------------------------------------------------------------------------------- |
| Liveness  | Whether this application process can answer its liveness HTTP request.                    |
| Readiness | Whether the required dependency checks meet the app's rules for accepting normal traffic. |
| Degraded  | An optional dependency is unavailable, but the app can still perform its required work.   |

### Lab Map

The arrows show how the parts connect and where a request can take different paths. Use this map to understand the design. It is not a recording of a request that actually ran.

```mermaid
flowchart TD
    Caller["HTTP caller"] --> Live["Liveness Route"]
    Caller --> Ready["Readiness Route"]
    Live --> Alive["Process Response"]
    Ready --> PG["PostgreSQL and item-relation Probe"]
    Ready --> Redis["Redis PING"]
    PG --> Policy["Required Dependency Policy"]
    Redis --> Policy
    Policy --> Result["200 ready/degraded or 503 not_ready"]
```

## 3. Guided Walkthrough

### Step 01. Inherited State

**What You Are Doing:** Start with the database and cache recovered from earlier labs. An old unresolved fault would make it harder to connect this lab's health results to the outage you deliberately create.

**Practical Walkthrough:** Confirm that the cached data is correct and both dependencies are available. Save this healthy starting point before creating failures. Keep the app code and helpers unchanged during the experiments so dependency availability is the change you are testing.

Check current PostgreSQL access and recovered caching before recording the baseline. This separates a new planned outage from a leftover fault. Changing the implementation during these tests would introduce another possible reason for a different health response.

Continue from Lab 3 with:

- app, postgres and redis running;
- the original course checkpoint preserved;
- the connection-failure translation from Lab 2;
- no unfinished cache corruption/staleness experiment;
- no Collector, Prometheus, Grafana, Loki, Tempo, Pyroscope or Alertmanager running; and
- the shared Bash helpers from Lab 1.

Earlier labs showed how the dependencies behave. Now compare how the API endpoints, dependency probes, and Docker report that behavior.

**Understanding the Result:** If the app starts degraded for an unexplained reason, later output cannot be attributed only to your planned fault. Resolve that starting problem first.

### Step 02. Scope and Exclusions

**What You Are Doing:** Learn exactly what each health signal means before using it for an alert, dashboard, or restart decision.

**Practical Walkthrough:** A health endpoint is a small program answering a particular question. Read which checks it performs and how it chooses a status. Docker, a traffic router, or an alert system may act on that status later, but those actions are separate from what the endpoint measures.

For each endpoint, identify its checks, response body, and HTTP status rule. Then ask which tool consumes that result. Reporting a condition and reacting to it are different steps. This helps explain why failed readiness does not automatically restart the process.

You will inspect the existing health code, fill in a status table, compare startup rules with runtime checks, show a cached GET working during a database outage, and verify useful recovery.

Do not add Kubernetes probes, a restart watcher, a blackbox exporter, alert rules, or a reverse proxy. This lab tests the health signals themselves. Tools that act on those signals come later.

**Understanding the Result:** First establish what a signal proves. Then a separate policy can decide whether that condition should cause an alert, stop traffic, or restart a process.

### Step 03. Clean Starting State

**What You Are Doing:** Confirm full readiness before stopping anything. This healthy result is your reference for each later dependency-failure comparison.

**Practical Walkthrough:** Load the helpers, check the known item, and save the health response bodies. Read the individual dependency fields as well as the overall status. You will compare the same fields with Redis down, PostgreSQL down, and both down.

Save and inspect each healthy response before introducing a fault. The overall readiness decision and each dependency result are related but different details. If your baseline differs from the example, investigate now; the later table assumes both dependencies started working.

```bash
source lab-notes/session.sh
baseline_check
mkdir -p lab-notes/lab-04
api -fsS "$APP_URL/health/live" | jq .
api -fsS "$APP_URL/health/ready" | jq .
```

**Expected Result:**

```json
{"status":"alive"}
```

```json
{"status":"ready","dependencies":{"postgres":"up","redis":"up"}}
```

If readiness is already degraded, repair that condition first. An experiment with an unexplained starting failure cannot clearly show the effect of the new fault.

**Understanding the Result:** Both dependencies should start up. Keep the full body because HTTP 200 alone does not distinguish full readiness from the allowed degraded state.

### Step 04. Measurable Learning Objectives

**What You Are Doing:** Explain each status using the check and policy that produced it. Understanding why two indicators disagree is more useful than simply looking for a green label.

**Practical Walkthrough:** Turn each observation into a precise statement. “Liveness returned 200” means the process answered that request. It does not mean an item could be created. Practice predicting which checks can pass while others fail and why those results can all be correct.

Replace “the app works” with the specific request, status, and dependency involved. Use that wording throughout the failure table. A liveness response should never be presented as evidence that a new database write committed.

You must be able to:

- state precisely what liveness does and does not prove;
- explain why PostgreSQL is required but Redis is optional;
- predict status codes and dependency details in all four dependency combinations;
- demonstrate a live process that is not ready for normal business traffic;
- show a cached read succeeding while required persistence is unavailable;
- prove the container health check calls liveness, not readiness;
- distinguish container `running`, Docker `healthy`, application `ready`, and a successful user operation;
- explain why fresh startup and an established process behave differently;
- identify probe timeouts, checks that run together, and how recently a reported check ran; and
- demonstrate recovery using both health checks and CRUD.

**Understanding the Result:** The skill is explaining each indicator, including apparent disagreements. One broad healthy/unhealthy label would hide the differences this lab is testing.

### Step 05. Three Health Questions

**What You Are Doing:** Separate the question behind each signal. A responding process does not prove database work succeeds, and one successful dependency check does not test every business operation.

**Practical Walkthrough:** Read each table row as a separate question, then match it with the command or request that answers it. A dependency PING, a liveness response, and a committed item write exercise different amounts of the system.

Choose a check that fits your question. An app can answer a local route without writing data. A database listener can respond without proving all application queries work. A committed item tests more of the write path. State only what the chosen check actually reached.

| **Signal**       | **Question It Answers**                                                | **What It Does Not Prove**                                                           |
| ---------------- | ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| `/health/live`   | Can this app process answer the liveness request?                      | Working database access, current item data, successful writes, or telemetry delivery |
| `/health/ready`  | Do the current checks meet this app's rules for serving required work? | That every future request will succeed or every downstream system is fault-free      |
| Dependency Probe | Did this specific check of the dependency succeed?                     | That every query, identity, operation, or future connection will work                |

Liveness intentionally avoids PostgreSQL, Redis, and observability services. Readiness checks the required database path. When PostgreSQL works but Redis does not, readiness reports `degraded` while keeping an HTTP success status.

**Understanding the Result:** Match your conclusion to the check's scope. A narrow successful probe cannot prove a broader operation that it never attempted.

### Step 06. Health Architecture

**What You Are Doing:** Follow liveness and readiness separately in the map. Readiness checks PostgreSQL and Redis, then applies the rule that PostgreSQL is required and Redis is optional.

**Practical Walkthrough:** Start with the liveness branch, which does not query the dependencies. Then follow readiness as it collects dependency results and chooses a status. Connections in a diagram do not tell you which dependency is required; that rule comes from the application code.

Inspect the dependency results before explaining the overall decision. Redis can be down in the body while required PostgreSQL-backed work remains possible. Use this app's policy to explain the status instead of assuming every failed dependency must make every application not ready.

The lab map in Section 2 shows this relationship.

Readiness checks PostgreSQL and Redis concurrently, meaning it starts their checks so they can run at the same time. It does not check Prometheus, Loki, Tempo, Pyroscope, Grafana, or Collector.

**Understanding the Result:** Optional-dependency failure may leave readiness at HTTP 200. Read the body to see which capability is degraded.

### Step 07. Read the Actual Contract in Code

**What You Are Doing:** Find the actual probe functions and Docker health command. Use their code to predict exactly which database and cache failures each check can detect.

**Practical Walkthrough:** Open the functions returned by the searches. Verify that liveness avoids dependency calls and readiness uses the app's configured database connection plus Redis. Then check Docker's health command; it may call a different endpoint from your manual readiness request.

Read complete functions, including time limits and exception handling. Compare the endpoint Docker uses with the one you requested manually. Different checks or different observation times can explain different results without either observer being broken.

```bash
rg -n 'async def live|async def ready|asyncio.gather|status_code=200 if postgres' app/app/main.py
rg -n 'async def check|asyncio.timeout|select\(Item.id\)' app/app/database.py
rg -n 'async def check|ping|socket_timeout' app/app/cache.py
rg -n 'HEALTHCHECK' app/Dockerfile
```

Read the surrounding functions in your editor. Verify:

- `live` returns a static status without calling dependencies;
- `ready` waits for both probe results, then bases HTTP readiness on PostgreSQL;
- `Database.check` performs `SELECT 1` and an item-relation/ID-column query;
- `Cache.check` performs an authenticated Redis PING;
- Docker probes `/health/live`.

The database probe can detect a missing items relation. It does not check every possible schema mismatch or confirm that Alembic is at its latest revision. Incompatible column changes still need proper migrations and application tests.

**Understanding the Result:** A function name is not a complete specification. Read its body to learn exactly what it checks and what it leaves untested.

### Step 08. Write the Expected Matrix Before Injecting Failure

**What You Are Doing:** Predict all four combinations before stopping services. This makes the required-versus-optional dependency policy something you can test against your own expectations.

**Practical Walkthrough:** Fill your table before reading the expected one. For each combination, ask whether the process can answer and whether PostgreSQL-backed work can succeed. Save your predictions unchanged so a surprising result leads to investigation rather than a rewritten expectation.

Predict liveness, readiness, and business work separately for each combination. After the fault, compare both status and body with that prediction. If they differ, check the policy, timing, and whether the intended service actually stopped.

Fill the right-hand columns yourself, then compare after each experiment:

| **PostgreSQL** | **Redis** | **Live HTTP** | **Ready HTTP/Body** | **Required Business Behavior** |
| -------------- | --------- | ------------- | ------------------- | ------------------------------ |
| Up             | Up        | Prediction    | Prediction          | Prediction                     |
| Up             | Down      | Prediction    | Prediction          | Prediction                     |
| Down           | Up        | Prediction    | Prediction          | Prediction                     |
| Down           | Down      | Prediction    | Prediction          | Prediction                     |

Expected contract after you have made a prediction:

| **PostgreSQL** | **Redis** | **Live HTTP** | **Ready HTTP/Body** | **Business Impact**                                                               |
| -------------- | --------- | ------------- | ------------------- | --------------------------------------------------------------------------------- |
| Up             | Up        | 200           | 200 / `ready`       | Normal CRUD                                                                       |
| Up             | Down      | 200           | 200 / `degraded`    | Item operations continue through PostgreSQL; Redis caching is unavailable         |
| Down           | Up        | 200           | 503 / `not_ready`   | Writes, lists, and cache misses fail; some already cached reads can still succeed |
| Down           | Down      | 200           | 503 / `not_ready`   | Required database work fails, and Redis cannot supply a cached answer             |

If the app process itself cannot answer, you receive no HTTP response. That is a separate case from the live app returning a deliberate 503.

**Understanding the Result:** The table tests this app's policy, not whether one technology matters more in every system. An application that requires Redis for correct operation would need different readiness rules.

### Step 09. Define a Repeatable Health Capture

**What You Are Doing:** Create one helper that saves health statuses and bodies consistently. An expected 503 is useful evidence, so the capture must retain it.

**Practical Walkthrough:** The helper queries the same endpoints each time and saves their bodies and status codes. Expected 503 responses remain available for comparison. A connection failure is still treated separately. Using one method keeps the four experiments comparable.

Read the helper's label argument and output names. The label identifies the fault phase. If curl cannot connect, record that separately; an empty file or status `000` is not a readiness decision returned by the app.

```bash
capture_health() {
  local label="$1"
  local endpoint code
  for endpoint in live ready; do
    code=$(api -sS -o "lab-notes/lab-04/${label}-${endpoint}.json" \
      -w '%{http_code}' "$APP_URL/health/$endpoint") || return 1
    printf '%s %s HTTP %s\n' "$label" "$endpoint" "$code" \
      | tee -a lab-notes/lab-04/statuses.txt
    jq . "lab-notes/lab-04/${label}-${endpoint}.json" || return 1
  done
}
capture_health all-up
```

The helper omits `--fail` so it can keep an expected 503 response. Network failures still make curl fail. `HTTP 000` means no HTTP status was received; it is not a status generated by the application.

**Understanding the Result:** A real HTTP status came from a server response. If no status was received, investigate the connection or process rather than reading it as an application readiness policy.

### Step 10. Record the Container's Independent View

**What You Are Doing:** Save Docker's health view and the app's identity before the faults. Later, compare them to check whether the app actually restarted during dependency failures.

**Practical Walkthrough:** Record the container ID, start information, restart count, and health history. Select only those fields. Docker records its own periodic probe, whereas your manual readiness requests have their own timestamps and response files. Keep both observations so you can compare their scope and timing.

Capture the identity and lifecycle fields before the fault. Compare Docker's probe timestamps with your manual requests because the checks run on different schedules. Avoid saving a full inspect dump; it includes environment values and other details unnecessary for this experiment.

```bash
APP_CONTAINER=$(dc ps -q app)
docker inspect --format \
  'id={{.Id}} status={{.State.Status}} started={{.State.StartedAt}} restarts={{.RestartCount}}' \
  "$APP_CONTAINER" | tee lab-notes/lab-04/app-before.txt
docker inspect --format '{{json .Config.Healthcheck}}' "$APP_CONTAINER" | jq .
docker inspect --format '{{json .State.Health}}' "$APP_CONTAINER" | jq .
```

Docker's health history contains its own probe results, not every readiness request you send. Because its probe checks liveness, the container can remain running and healthy while PostgreSQL is unavailable.

Do not put the full inspect output into shared evidence. Its environment section contains secrets; save only the fields needed here.

**Understanding the Result:** Docker and readiness can disagree because they test different capabilities. The saved IDs and start values let you separately check whether a restart occurred.

### Step 11. Optional Dependency Experiment: Redis Down

**What You Are Doing:** Stop Redis only and compare health responses with an uncached request. This tests whether loss of the optional cache leaves required database-backed work available.

**Practical Walkthrough:** Run the complete block with recovery included. Keep PostgreSQL available while you capture liveness, readiness, and a list request. The question is whether useful app work continues without Redis, not whether the stopped Redis server passes its own health check.

Use the list route because it must read PostgreSQL and cannot use a warmed individual-item copy. Read both the degraded readiness body and the successful list response. After the block restarts Redis, verify full readiness before moving to the next combination.

Predict first, then run the controlled subshell:

```bash
(
  set -euo pipefail
  trap 'dc start redis >/dev/null' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  dc stop redis
  capture_health redis-down
  jq -e '.status == "degraded" and .dependencies.postgres == "up" and .dependencies.redis == "down"' \
    lab-notes/lab-04/redis-down-ready.json >/dev/null
  api -fsS "$APP_URL/api/v1/items?limit=1" \
    -o lab-notes/lab-04/redis-down-list.json
  docker inspect --format '{{json .State.Health}}' "$APP_CONTAINER" \
    > lab-notes/lab-04/redis-down-container-health.json
)
wait_ready
capture_health redis-restored
```

**Command Note:** `trap ... EXIT` runs the cleanup when this shell exits. Keep it in the same block as the fault and use the later checks to confirm recovery succeeded.

**Expected Result:** liveness and readiness both return HTTP 200. The readiness body reports degraded caching, the uncached list succeeds, and Docker does not restart the app.

Redis failure can still have a cost. Lab 3 showed fallback; later labs measure added request latency and database load. Success here does not mean the outage is free of impact.

**Understanding the Result:** A degraded readiness body with HTTP 200 is expected. Use the business request as evidence of what still works when deciding how traffic should be handled.

### Step 12. Why Optional Redis Must Not Fail Readiness Here

**What You Are Doing:** Connect readiness to traffic decisions. Removing instances that can still do useful work could reduce available capacity during a cache outage.

**Practical Walkthrough:** Imagine a router that sends traffic only to ready instances. If every instance reported not-ready solely because Redis failed, the router could remove all of them even though they can still use PostgreSQL. Base the readiness decision on the fallback behavior you have tested.

Explain both sides of the fallback: useful work remains possible, but PostgreSQL may receive more work. This is the reason for this application's readiness policy. It does not mean Redis is optional in every application.

Readiness helps decide whether normal traffic should reach an instance. Here, PostgreSQL-backed work can continue while Redis is down.

Returning readiness 503 for optional-cache loss could remove working capacity and widen the outage. Another application may rely on Redis for correctness or coordination, in which case it should use different rules. The workload determines the policy, not the dependency's name.

**Understanding the Result:** Define required dependencies from the operations the app must support. Do not automatically require every connected service to be healthy.

### Step 13. Prepare a Fresh Cache-Mask Subject

**What You Are Doing:** Prepare one valid cached item for the short database outage. It will demonstrate how one request can succeed while the app's overall readiness fails.

**Practical Walkthrough:** Create an item, read it to warm the cache, and extend only its key long enough for the test. Check that the key really contains the item. If a previous invalidation failure left bypass active, the GET might succeed without filling Redis, so verify that first.

Compare the warm response and cached document before extending the TTL. They must belong to this item and the expected version. If bypass prevents filling, finish normal recovery first. Without a valid cached copy, the planned comparison cannot show a cache-backed success during the outage.

```bash
api -fsS -H 'Content-Type: application/json' \
  -d '{"name":"Lab 04 health subject","description":"Disposable readiness exercise","price":"40.00","is_active":true}' \
  "$APP_URL/api/v1/items" -o lab-notes/lab-04/item.json
ITEM_ID=$(jq -er '.id' lab-notes/lab-04/item.json)
KEY=$(cache_key "$ITEM_ID")
api -fsS "$APP_URL/api/v1/items/$ITEM_ID" >/dev/null
rcli EXPIRE "$KEY" 120
rcli TTL "$KEY"
```

Only this test key gets a two-minute lifetime so it can outlast the brief database outage. The app's default TTL and other keys stay unchanged. Remove the temporary key after recovery.

If a previous bypass interval is still active, the key may remain absent. Finish Lab 3's cache-recovery check before continuing.

**Understanding the Result:** The longer lifetime is temporary and belongs only to this exercise item. Remove it during cleanup so you do not leave behind a different cache policy.

### Step 14. Required Dependency Experiment: PostgreSQL Down

**What You Are Doing:** Stop PostgreSQL and compare an already cached GET with a list request. Their different results show that different routes need different dependencies.

**Practical Walkthrough:** Keep the app and prepared Redis copy running while PostgreSQL is stopped. Request the cached item and the uncached list, then compare their statuses with liveness and readiness. One cached response can succeed while the database capability needed for normal traffic remains unavailable.

Capture both requests during the actual outage. Explain which store each needs. Save the readiness body and keep the full recovery block. A successful warm read proves that limited path worked; it does not make the required database available.

```bash
(
  set -euo pipefail
  trap 'dc start postgres >/dev/null' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  dc stop postgres
  capture_health postgres-down
  jq -e '.status == "not_ready" and .dependencies.postgres == "down" and .dependencies.redis == "up"' \
    lab-notes/lab-04/postgres-down-ready.json >/dev/null

  cached_status=$(api -sS -o lab-notes/lab-04/cached-during-db-outage.json \
    -w '%{http_code}' "$APP_URL/api/v1/items/$ITEM_ID")
  list_status=$(api -sS -o lab-notes/lab-04/list-during-db-outage.json \
    -w '%{http_code}' "$APP_URL/api/v1/items?limit=1")
  printf 'cached_get=%s list=%s\n' "$cached_status" "$list_status" \
    | tee lab-notes/lab-04/cache-mask.txt
  test "$cached_status" = 200
  test "$list_status" = 503
  docker inspect --format '{{json .State.Health}}' "$APP_CONTAINER" \
    > lab-notes/lab-04/postgres-down-container-health.json
)
wait_ready
rcli DEL "$KEY"
api -fsS "$APP_URL/api/v1/items/$ITEM_ID" >/dev/null
capture_health postgres-restored
```

Expect liveness 200 and readiness 503. The same process can return 200 for the valid cached item and 503 for a list that needs PostgreSQL.

If the individual GET returns 503, check whether its key expired or was never filled. That changes which limited read path was available; it does not show that the readiness policy is wrong.

**Understanding the Result:** The requests exercise different paths. A cached success is fully compatible with failed readiness when PostgreSQL is required for normal business work.

### Step 15. State the Cache-Mask Conclusion Precisely

**What You Are Doing:** Describe the limited availability accurately. One cached success does not restore database operations, but it also means that not every possible request failed.

**Practical Walkthrough:** Include all three facts in your conclusion: the process answered, required database work failed, and one known cached item remained readable. This explains the actual available capability instead of calling the whole system simply healthy or broken.

Mention that the successful item was deliberately cached before the outage. Without that condition, someone might assume the result also applies to uncached items, lists, or writes. The evidence supports the prepared read path only.

A correct statement is:

> PostgreSQL was unavailable to the application. The process remained live, but normal business readiness failed. One cached item could still be returned; operations needing persistence failed.

The results do not support either “the whole API is healthy” or “no request can succeed.” Some paths remain available while required capabilities are missing.

Readiness applies a policy for normal traffic. It does not mean every endpoint uses exactly the same dependencies.

**Understanding the Result:** Describe the specific operations in an incident report. That tells others what can still work and what must be tested again after recovery.

### Step 16. Both Dependencies Down

**What You Are Doing:** Stop both dependencies to complete the table, then restore them. Check whether liveness still answers independently of database and cache availability.

**Practical Walkthrough:** Run the complete block with both services stopped and cleanup included. Liveness should still answer without checking either dependency. Data requests now have neither PostgreSQL nor Redis available to supply their answer. Compare this with the earlier single-dependency cases.

Confirm both services stopped, then capture the same health requests as before. Keep the recovery block intact. Restore both services and verify the baseline afterward, so an unfinished outage cannot confuse the next lifecycle comparison.

Now test the final combination. The trap restores both dependencies on exit:

```bash
(
  set -euo pipefail
  trap 'dc start postgres redis >/dev/null' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  dc stop postgres redis
  capture_health both-down
  jq -e '.status == "not_ready" and .dependencies.postgres == "down" and .dependencies.redis == "down"' \
    lab-notes/lab-04/both-down-ready.json >/dev/null
  status=$(api -sS -o lab-notes/lab-04/both-down-get.json -w '%{http_code}' \
    "$APP_URL/api/v1/items/$ITEM_ID")
  test "$status" = 503
)
wait_ready
capture_health both-restored
```

Liveness should still avoid both dependencies. If it also fails, check whether the process stopped or became unresponsive. That is different from a live process deciding it is not ready.

**Understanding the Result:** If liveness disappears, inspect the process and HTTP handling first. Do not assume the endpoint suddenly started depending on PostgreSQL or Redis.

### Step 17. Prove Dependencies Did Not Restart the App

**What You Are Doing:** Compare the app's container ID, start time, and restart count with the saved baseline. These fields check actual restart behavior separately from a change in health status.

**Practical Walkthrough:** Read the same lifecycle fields you saved before the faults. They tell you whether the process or container changed. Readiness is a reported condition; a different readiness response is not proof that the app restarted.

Compare every recorded field. A process can restart within the same container ID, so the start time and restart count matter too. If a field changed, look for a process exit or an explicit restart before blaming readiness.

```bash
docker inspect --format \
  'id={{.Id}} status={{.State.Status}} started={{.State.StartedAt}} restarts={{.RestartCount}}' \
  "$APP_CONTAINER" | tee lab-notes/lab-04/app-after.txt
diff -u lab-notes/lab-04/app-before.txt lab-notes/lab-04/app-after.txt
```

The container identity, start time, and restart count should remain unchanged in these dependency-only tests. If they changed, investigate an exit, an out-of-memory (OOM) kill, or another person's action. Do not attribute a restart to readiness without evidence.

Docker Compose does not restart a container just because it becomes unhealthy. Restart policies respond to the process or container exiting. Health status is a separate signal. Lab 5 tests this distinction directly.

**Understanding the Result:** Unchanged lifecycle values support that the process kept running. Changed values need an explanation, such as a process exit or a separate restart command.

### Step 18. Inspect Probe Latency and Failure Budgets

**What You Are Doing:** Measure how long the probe takes and inspect its time limits. Health checks perform real work, so waiting on a failed dependency affects when you receive the result.

**Practical Walkthrough:** Measure the request duration and read the configured dependency timeouts. Readiness checks real services, and failures can make it slower. The checks run concurrently, so do not assume their full waiting times are deliberately added one after another.

Read the duration in seconds and compare it with the timeout settings. Include pool waits, scheduling, and request overhead in your explanation. One measured request describes that observation; it does not establish an exact maximum for every possible outage.

```bash
api -sS -o /dev/null -w 'ready_time_seconds=%{time_total}\n' "$APP_URL/health/ready"
dc exec -T app python - <<'PYTHON'
from app.config import Settings
s = Settings()
print({
    "db_timeout_seconds": s.db_timeout_seconds,
    "redis_timeout_seconds": s.redis_timeout_seconds,
    "dependency_probe_interval_seconds": s.dependency_probe_interval_seconds,
    "startup_attempts": s.startup_attempts,
})
PYTHON
```

**Command Note:** `exec -T` runs the diagnostic program in the existing container without an interactive terminal. The heredoc supplies the Python program through standard input, using the image's installed dependencies.

Readiness starts both dependency checks concurrently instead of deliberately waiting for one to finish before starting the other. PostgreSQL has an overall async time limit slightly above its configured database timeout. Redis has short connection and socket limits with no configured automatic retries.

A healthy timing sample is not a service-level objective (SLO) or a worst-case guarantee. A practical time budget must also consider task scheduling, waiting for a pooled connection, name resolution, and network behavior.

**Understanding the Result:** A fast healthy check does not guarantee fast failure detection. Consider connection-pool waits, scheduling, and network delays when choosing probe limits.

### Step 19. Understand Periodic Checks versus Request-Time Checks

**What You Are Doing:** Separate periodic background checks from a readiness request made now. Who made the observation and when it happened can explain different results.

**Practical Walkthrough:** The background task checks on a schedule. A readiness request performs fresh checks when called. Therefore, a business request may fail before the background task logs the change. Repeated readiness calls also add dependency work; they do not merely read an old status label.

Put the background checks, manual probes, and business failures on one timeline. Each may detect the fault at a different time. Record how often you polled readiness because those extra calls can also explain additional database or Redis activity.

A background task normally checks dependencies every 15 seconds and logs changes in state. `/health/ready` runs new checks for each request instead of simply returning that task's last result.

This distinction matters:

- the last background result becomes older until the next check;
- a readiness request creates dependency work itself;
- repeatedly polling at high frequency can add unnecessary load;
- later metrics based on these observations must be interpreted with their age in mind.

Avoid a tight endless readiness loop. It creates extra dependency requests just to watch a status change.

**Understanding the Result:** Include the observation time and the check that produced each health statement. Results from different moments can disagree without either check being wrong.

### Step 20. Why a Healthy Database Container Is Not Sufficient

**What You Are Doing:** Compare PostgreSQL accepting connections with the app successfully using its own connection and required table. These are separate levels of proof.

**Practical Walkthrough:** The database container's listener check asks a narrower question than application readiness. The app also needs the correct network route, credentials, database, and schema. PostgreSQL can accept connections while the app's role is rejected or the items table is missing.

Run the listener check and readiness request separately. `pg_isready` does not perform the full Items query using the app's identity. If the results differ, inspect the app's network path, login details, selected database, and migrations before relying on the container's healthy label.

Compare:

```bash
dc exec postgres pg_isready -h 127.0.0.1 -U postgres -d postgres
api -fsS "$APP_URL/health/ready" | jq .
```

The first command checks server acceptance through TCP loopback inside the PostgreSQL container. The second uses the app's configured database role and network path and runs its minimal items-schema check.

A wrong app password or missing table can fail readiness while the listener still accepts connections. A slow administrator command also does not automatically mean every business request is failing. Compare the actual paths being tested.

**Understanding the Result:** A working listener is necessary but does not prove the app can use its data. The same limitation applies to an administrator connection with different credentials and access.

### Step 21. Startup Ordering Is a Separate Contract

**What You Are Doing:** Read the initial startup requirements separately from runtime health behavior. A dependency blocking app creation is different from a dependency failing after the app is already serving.

**Practical Walkthrough:** Follow the startup chain in the final Compose configuration. Storage ownership, database acceptance, and migrations must succeed before a fresh app starts. Once the app is running, a later dependency outage follows the runtime behavior you just tested.

Read the combined `depends_on` settings in order and identify what each condition waits for. These startup rules do not rerun migrations or restart the serving app every time PostgreSQL changes state. The next experiment restarts an existing container, which is a different path from creating a fresh stack.

Inspect only dependency fields:

```bash
dc config --format json | jq '{
  postgres: .services.postgres.depends_on,
  migrate: .services.migrate.depends_on,
  app: .services.app.depends_on
}'
```

At fresh startup:

1. volume ownership must complete;
2. PostgreSQL must accept TCP connections;
3. the migration job must complete successfully;
4. the app starts and performs bounded dependency validation.

These checks explain why a new app container may not exist yet when database initialization fails. An existing app surviving a later database outage is a different situation.

`depends_on` does not continuously supervise dependency health or provide database failover. [Docker describes startup dependency conditions](https://docs.docker.com/compose/how-tos/startup-order/).

**Understanding the Result:** Keep fresh-start evidence separate from runtime-failure evidence. A process that never started cannot answer the same liveness request as one that was already running when PostgreSQL stopped.

### Step 22. Controlled Existing-Container Startup During DB Failure

**What You Are Doing:** Restart the existing app while PostgreSQL is down. Observe the app's own startup checks and their limits, separately from a new deployment waiting for migrations.

**Practical Walkthrough:** This uses an existing container, not a new stack. The app still performs its own startup probes, which may take several attempts before HTTP becomes available. After that bounded startup period, compare liveness with not-ready behavior.

Allow the configured startup attempts to finish. A refused HTTP connection while startup is still in progress differs from a later readiness 503. Keep the full recovery block and verify useful work after PostgreSQL returns before concluding how startup handled the outage.

This experiment separates the app's limited startup checks from Compose's first-time migration requirement. It restarts an **existing** app container while PostgreSQL is down; it does not create a fresh stack.

```bash
(
  set -euo pipefail
  trap 'dc start postgres >/dev/null' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  dc stop postgres
  dc restart app
  wait_live
  status=$(api -sS -o lab-notes/lab-04/restarted-not-ready.json -w '%{http_code}' \
    "$APP_URL/health/ready")
  test "$status" = 503
  jq . lab-notes/lab-04/restarted-not-ready.json
)
wait_ready
```

Several probe and backoff intervals may pass before the app serves HTTP. Its startup code eventually logs that PostgreSQL is unavailable and begins serving in a not-ready state. Once serving starts, the liveness route itself performs no dependency check.

This deliberate app restart changes its start time. The earlier tests stopped only dependencies. Keep these two sets of evidence separate when comparing restart behavior.

**Understanding the Result:** Here you intentionally restarted the app process. Its changed start information does not contradict the earlier observation that dependency failures alone did not restart it.

### Step 23. Prove Useful Recovery, Not Merely Probe Recovery

**What You Are Doing:** After restoring dependencies, create and read a new item and verify SQL. This checks real committed work as well as healthy probe responses.

**Practical Walkthrough:** Save a fresh create response, read that item through the API, and query its row independently. Keep the same UUID across these checks. A real new operation gives stronger recovery evidence than simply seeing the probes turn green.

Use the newly returned ID in both GET and SQL. This connects the API success to a committed row. Reading an old cached item could still succeed with a broken database path, so it would not provide the same recovery proof.

```bash
api -fsS -H 'Content-Type: application/json' \
  -d '{"name":"Lab 04 recovery write","price":"4.00"}' \
  "$APP_URL/api/v1/items" -o lab-notes/lab-04/recovery-item.json
RECOVERY_ID=$(jq -er '.id' lab-notes/lab-04/recovery-item.json)
api -fsS "$APP_URL/api/v1/items/$RECOVERY_ID" | jq '{id,name,price}'
dbsql -v item_id="$RECOVERY_ID" <<'SQL'
SELECT id, name FROM items WHERE id = :'item_id'::uuid;
SQL
baseline_check
```

Record the three recovery results separately: dependency checks pass, the API creates and reads a new item, and another database connection can see its committed row.

**Understanding the Result:** Recovery includes both passing probes and successful useful work. Either one alone leaves part of the failed path unchecked.

### Step 24. Validate the Health Contract Tests

**What You Are Doing:** Read and run the health tests. Fake dependencies let them test the status policy repeatably, while the live outages test the deployed services.

**Practical Walkthrough:** Read the assertions first. See how fake dependency results produce ready, degraded, and not-ready responses. Check that liveness never awaits dependency probes. Then compare these repeatable code tests with the real container-stop observations.

Identify the fake result behind each expected status, then run the focused suite and check its exit result. The tests protect the decision rules. The live faults show whether actual service failures reach those rules correctly. Keep both types of evidence.

Read the existing tests:

```bash
cat app/tests/test_health.py
```

Run the focused health suite in a disposable image:

```bash
docker build --target test -t observability-test:local ./app
docker run --rm --network none --read-only --tmpfs /tmp \
  observability-test:local pytest -p no:cacheprovider tests/test_health.py
```

The tests cover full readiness, Redis degradation, PostgreSQL failure, and dependency-independent liveness. The liveness test specifically checks that no dependency probe is awaited.

Fake-based tests check the policy in code. Docker outage tests check the deployed path with real services. Describe them as complementary evidence with different coverage.

**Understanding the Result:** Tests help catch later code changes that break the policy. Runtime experiments check that the current deployment follows it with real dependencies.

### Step 25. Design Review Exercise: Classify Dependencies

**What You Are Doing:** Classify dependencies by the work this app requires. Use the request paths as evidence instead of treating every connected service as essential.

**Practical Walkthrough:** For each component, ask which operations would stop if it disappeared. Consider liveness and readiness separately. Losing telemetry visibility can matter operationally while item operations remain available, so describe that loss without confusing it with a database outage.

Trace a specific user operation for every proposed dependency. Identify where it would block if the component were absent. Then classify the impact on liveness, readiness, and observability separately using the behavior you have tested.

Complete this table without changing code:

| **Component**        | **Required for Liveness?** | **Required for Readiness Here?** | **Failure Implication** |
| -------------------- | -------------------------- | -------------------------------- | ----------------------- |
| PostgreSQL           | Your answer                | Your answer                      | Your answer             |
| Redis                | Your answer                | Your answer                      | Your answer             |
| Collector            | Your answer                | Your answer                      | Your answer             |
| Prometheus           | Your answer                | Your answer                      | Your answer             |
| Loki/Tempo/Pyroscope | Your answer                | Your answer                      | Your answer             |
| Grafana              | Your answer                | Your answer                      | Your answer             |

Explain why an observability outage needs its own detection without automatically making this business app fail readiness.

The lab already supplies useful evidence: item operations worked while all those observability backends were stopped.

**Understanding the Result:** A component becomes required because of the application's behavior and promises, not simply because it appears in the deployment file.

## 4. Troubleshooting and Diagnosis

Begin with the first result that differed from your prediction. Save the exact error or symptom and identify which service or command produced it. Use the checks below, make the needed fix, and repeat the original check to confirm the problem is resolved.

### Troubleshooting Runbook

#### A. Readiness 503 Is Reported as a Curl Error

When expecting an HTTP error, use `-sS -o body.json -w '%{http_code}'` without `-f`. This saves the body and status. Keep an HTTP failure response separate from a network failure that produces no response.

#### B. Redis Failure Returns Readiness 503

Check the code in the running image. With healthy PostgreSQL, the current policy returns 200/degraded for Redis failure. An optional-cache outage should not be reported as loss of the required database path.

#### C. PostgreSQL Failure Returns a 500 on Business Requests

Apply Lab 2's database-operation connection-error change and rebuild the app. Readiness uses its own probe error handling, which is separate from the business-route fix.

#### D. Docker Says Healthy While Readiness Is not_ready

Inspect `.Config.Healthcheck` and confirm it calls `/health/live`. Docker and readiness intentionally ask different questions. Adding database checks to liveness just to make their indicators agree would change that contract.

#### E. The Cache-Mask GET Fails

While Redis is available, check the exact key, value, and TTL. Expiry, invalidation, or active bypass may remove the prepared cache path. Restore PostgreSQL and warm the test key before repeating the outage.

#### F. The App Takes Time to Answer After Restart

Read the startup logs and probe-attempt settings. HTTP may not be served until lifespan startup finishes. After the bounded attempts, an existing app can become live while still reporting not-ready.

#### G. A New App Never Starts with PostgreSQL Down

Inspect `migrate` and `depends_on`. A fresh app may be blocked before its process exists, so there is no liveness route to call yet. This does not prove that liveness itself runs SQL.

#### H. All Probes Pass but a Real Request Fails

Readiness checks a small set of current conditions. Inspect the failing request's input, route, query, and transaction. A passing probe does not exercise every code path or guarantee the next operation.

### Health Diagnostic Sequence

1. Can you receive any HTTP response?
2. Does liveness succeed?
3. What does readiness say about each dependency?
4. What does the container runtime report, and which endpoint does it probe?
5. Which dependencies does the failing user operation actually need?
6. Is a cache masking or serving only part of the workload?
7. Does the failure concern initial startup, runtime behavior or recovery?
8. After repair, do both probes and the original business operation succeed?

## 5. Review and Practice

Answer the questions in your own words before reading the answers. For the scenario, say what you would inspect and exactly what each result would tell you.

### Knowledge Check

1. Can liveness be 200 while readiness is 503?
2. Why does Redis down return readiness 200 here?
3. Why can one cached GET succeed when readiness is not_ready?
4. What does a running container fail to prove?
5. Does Docker health automatically restart a container?
6. Does a manual readiness request appear in Docker health history?
7. Why test the app role separately from `pg_isready`?
8. Does the schema probe prove every column matches every migration?
9. Does `depends_on` supervise dependency health forever?
10. Why can an existing app restart into not-ready state while a fresh stack is blocked by migrations?
11. Does a readiness check guarantee the next write will commit?
12. What additional evidence proves business recovery?

#### Answer Key

1. Yes. The process can answer HTTP while its required database is unavailable.
2. PostgreSQL still supports required work, while Redis is an optional way to speed up reads.
3. That route can return a valid Redis copy without querying PostgreSQL.
4. Running alone does not prove health, readiness, correct values, or successful business work.
5. No. Health status is separate from restart policy, which responds to process exit.
6. No. Docker records the health checks it runs itself.
7. The app uses its own identity and network path and needs access to the expected schema. A listener check does not prove those all work.
8. No. It checks only a minimal required table and column path.
9. No. It sets startup conditions, with only the explicit restart behavior supported and configured for dependencies.
10. Restarting an existing container does not repeat all fresh-creation gates. Its own startup probes have bounded attempts before it can serve not-ready.
11. No. Conditions may change, and the next write may require work the probe did not test.
12. Complete a representative write and read, then independently check the stored values.

### Professional Scenario Exercise

An incident message says:

> “Docker reports healthy, so the database team's claim of an outage must be wrong.”

Write a response using the actual probe target and saved readiness body. Explain the difference between a responding process and working database-dependent operations. If a cached GET succeeded, explain that limited path without dismissing the failed writes.

## 6. Completion Checklist

Tick an item only when you can show its result or saved evidence. Finish the cleanup instructions before starting the next lab.

### Cleanup and Final Baseline

**What You Are Doing:** Remove only this lab's temporary items and restore the baseline. Make sure both dependencies run and the test key with its extended lifetime is gone.

```bash
api -fsS -X DELETE "$APP_URL/api/v1/items/$ITEM_ID" -o /dev/null
api -fsS -X DELETE "$APP_URL/api/v1/items/$RECOVERY_ID" -o /dev/null
baseline_check
CHECKPOINT_ID=$(cat lab-notes/checkpoint-item-id.txt)
api -fsS "$APP_URL/api/v1/items/$CHECKPOINT_ID" | jq '{id,name,price}'
```

Neither dependency should remain stopped. Deleting the test item also removes its artificially extended cache key. Keep the original checkpoint and your evidence files.

### Completion Criteria

- [ ] All four dependency combinations were predicted and observed.
- [ ] Liveness stayed dependency-independent during established-process outages.
- [ ] Redis failure produced 200/degraded readiness and working required functionality.
- [ ] PostgreSQL failure produced 503/not_ready.
- [ ] A cached read was distinguished from a persistence-dependent request.
- [ ] Docker's actual health target was inspected.
- [ ] Pure dependency failure did not itself restart the app.
- [ ] You distinguished fresh startup gating from bounded app startup probes.
- [ ] Health-contract tests pass.
- [ ] Recovery includes a committed write, a read and direct row verification.
- [ ] Temporary items were deleted and the course checkpoint survives.

## 7. Production Context and Next Lab

### Production Implications

Other systems use health checks to make operational decisions. Keep those checks inexpensive, time-limited, safe, and aligned with traffic requirements. An optional cache or telemetry failure alone should not cause needless restarts of otherwise responsive workers in this design.

Readiness should test what the app needs for its promised work and report optional failures clearly. One endpoint cannot replace representative user journeys, deeper dependency checks, or capacity measurements. Those answer additional questions introduced in later labs.

### End State and Transition to Lab 05

Next: [Lab 05: Docker Compose Networking, Storage, and Restart Behavior](Lab-05.md).

You now know what this app means by live and ready. Lab 5 examines its Docker environment: service-name lookup, ports, stored volumes, container replacement, process exits, and restart policies.