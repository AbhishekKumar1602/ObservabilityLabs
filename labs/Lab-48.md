# Lab 48: Security and Telemetry Hardening Review

## Purpose and Scope

> **Primary Objective:** Review exposure, credentials, privileges and sensitive telemetry, apply a concrete local hardening change, and verify redaction without hiding useful evidence.

The platform now makes failures observable. Those same endpoints, log fields, query APIs and diagnostic workloads can expose operational data or consume resources. This lab reviews the actual running composition, tests synthetic secret handling and records decisions about administrative access.

The review is confined to your local repository and its containers. It is not penetration testing, a vulnerability-free certification, an internet deployment, or an implementation of authentication for the learning API. No real credentials are placed into probes, screenshots or telemetry.

## 1. Prerequisites and Starting State

```bash
source lab-notes/session.sh
source lab-notes/evidence.sh
source lab-notes/metrics-session.sh
source lab-notes/platform-session.sh
source lab-notes/traces-session.sh
source lab-notes/profiling/session.sh
load_app_settings
start_lab 48
dp config --quiet
wait_ready
wait_backend loki:3100 /ready
wait_backend tempo:3200 /ready
wait_backend pyroscope:4040 /ready
mkdir -p lab-notes/operations
umask 077
chmod 700 "$LAB_DIR"
```

Complete [Lab 47](Lab-47.md), with all stopped services restored. Work from the repository root in the existing Bash session. The Collector and SDK settings remain unchanged. Keep the bounded demo endpoint enabled for this review and the final game day; production exposure of that endpoint needs a separate decision.

**Measurable outcomes:** inventory every published listener; distinguish runtime privileges from initialization exceptions; verify administrative API flags; protect local credential files; demonstrate positive log/trace controls with synthetic secrets absent; and write an actionable findings register.

## 2. Draw the Trust Boundaries

| Boundary | Actual access in this repository | Review question |
|---|---|---|
| Host → app | Loopback HTTP; business and demo routes have no user authentication | Who else can access this host or tunnel the port? |
| Host → Grafana | Loopback HTTP; login required for private datasources | Are credentials unique, and are anonymous access and signup disabled? |
| Host → Prometheus/Alertmanager | Local query and control surfaces | Could an untrusted process query data, silence alerts or send writes? |
| Docker daemon → Collector | Loopback-published Fluent Forward input | Is this kept local and limited to the intended logging path? |
| App → PostgreSQL/Redis | Docker DNS and credentials | Are application and bootstrap roles separated? |
| App/Collector → backends | Internal unauthenticated learning network | Can unrelated containers join that trust domain? |
| Operator → evidence files | Local filesystem | Could evidence or expanded configuration reveal credentials? |

Loopback limits network reachability, not access by other local users, browser behavior, host malware or Docker administrators. An internal Docker network is not application-level authorization. The node exporter introduced earlier may intentionally bind the Docker bridge address for host metrics; review that exception rather than declaring every listener loopback-only.

**Prediction checkpoint:** should hiding a dashboard prevent users from querying its datasource? No. Dashboard navigation and datasource authorization are different controls. Should request IDs identify authenticated actors? No; clients can choose valid request IDs.

## 3. Inventory Configuration Without Printing Secrets

```bash
cat > lab-notes/operations/security_inventory.py <<'PYTHON'
"""Read expanded Compose JSON from stdin; emit an allowlisted, secret-free inventory."""
import json,sys
config=json.load(sys.stdin)
result=[]
for name,service in sorted(config['services'].items()):
    command=service.get('command') or []
    if isinstance(command,str):command=command.split()
    flags={key:any(arg==key or arg.startswith(key+'=true') for arg in command)
           for key in ('--web.enable-admin-api','--web.enable-lifecycle','--web.enable-remote-write-receiver')}
    ports=[{key:port.get(key) for key in ('host_ip','published','target','protocol')}
           for port in service.get('ports',[])]
    mounts=[{'type':mount.get('type'),'target':mount.get('target'),'read_only':mount.get('read_only',False)}
            for mount in service.get('volumes',[])]
    result.append({'service':name,'image':service.get('image'),'user':service.get('user','image default'),
                   'privileged':service.get('privileged',False),'read_only':service.get('read_only',False),
                   'cap_add':service.get('cap_add',[]),'cap_drop':service.get('cap_drop',[]),
                   'security_opt':service.get('security_opt',[]),'pid':service.get('pid'),
                   'network_mode':service.get('network_mode'),'ports':ports,'mounts':mounts,
                   'environment_keys':sorted((service.get('environment') or {}).keys()),
                   'logging_driver':service.get('logging',{}).get('driver'), 'prometheus_flags':flags})
json.dump(result,sys.stdout,indent=2);print()
PYTHON
```

```bash
set -o pipefail
dp config --format json | python3 lab-notes/operations/security_inventory.py > "$LAB_DIR/compose-inventory.json"
jq '.[] | {service,user,ports,privileged,cap_add,read_only}' "$LAB_DIR/compose-inventory.json"
python3 - "$LAB_DIR/compose-inventory.json" <<'PYTHON'
import json,sys
rows=json.load(open(sys.argv[1]))
assert not any(row['privileged'] for row in rows),'Unexpected privileged service'
assert not any(m['target']=='/var/run/docker.sock' for row in rows for m in row['mounts']),'Docker socket exposed'
for row in rows:
    for port in row['ports']:
        address=port.get('host_ip') or '0.0.0.0'
        print(row['service'],address,port['published'],'->',port['target'])
        assert address not in ('0.0.0.0','::'),'Investigate wildcard publishing before continuing'
PYTHON
```

The expanded Compose document passes through memory to an allowlisted report. Do not save `dp config` output directly, enable shell tracing, or attach a full `docker inspect` document: environment values can contain passwords. The report intentionally records environment **names**, not values, and never prints arbitrary command arguments.

Check for unnecessary host ports on PostgreSQL, Redis, Loki, Tempo, Pyroscope and OTLP. Lab 42 removed the Pyroscope host listener because Grafana can query it internally. Collector port 8006 serves Docker's host-side logging driver; binding the container receiver to `0.0.0.0` is still necessary for forwarding through Docker.

If a required local-only UI was changed to a wildcard binding, correct its active Compose overlay to `127.0.0.1:HOST_PORT:CONTAINER_PORT`, validate and recreate only that service. Do not remove the bridge listener used by the node exporter without also revisiting its scrape path.

## 4. Verify Runtime Privileges and Document Exceptions

```bash
while IFS= read -r cid; do
  docker inspect --format '{{json .}}' "$cid" | python3 -c '
import json,sys
c=json.load(sys.stdin);h=c["HostConfig"]
print(json.dumps({"service":c["Config"]["Labels"].get("com.docker.compose.service"),
"user":c["Config"]["User"],"privileged":h["Privileged"],"read_only":h["ReadonlyRootfs"],
"cap_add":h["CapAdd"],"cap_drop":h["CapDrop"],"security_opt":h["SecurityOpt"],
"pid_mode":h["PidMode"],"image_id":c["Image"]}))'
done < <(dp ps -q) > "$LAB_DIR/runtime-inventory.jsonl"
cat "$LAB_DIR/runtime-inventory.jsonl"
```

Check effective image users as well as Compose's `user` field. The application and backend wrappers run as non-root users with dropped capabilities; writable named volumes are intentional state, not a reason to make the entire container privileged.

The one-shot `init-volumes` service runs with narrowly scoped ownership capabilities and no network to prepare volume ownership. It is not a permanently running root application. Node exporter host filesystem/process mounts are another deliberate exception: inspect their read-only flags and necessity. A read-only host mount still exposes data to the process that can read it.

Do not apply `read_only: true` to every service mechanically. SQLite, WALs, compaction, queues and temporary files require deliberate writable paths. Review capabilities, mounts, host namespace use, user identity and socket access together.

## 5. Protect Credentials and Understand Rotation

```bash
chmod 600 .env
python3 - <<'PYTHON'
from pathlib import Path
import stat
mode=stat.S_IMODE(Path('.env').stat().st_mode)
assert mode==0o600,oct(mode)
print('Local environment permissions:',oct(mode))
PYTHON
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  if git ls-files --error-unmatch .env >/dev/null 2>&1; then
    printf '%s\n' 'STOP: .env is tracked; remove it from tracking and rotate exposed credentials.'
  else
    git check-ignore .env
  fi
fi
python3 lab-notes/operations/change_event.py "$LAB_DIR/changes.jsonl" chmod .env completed --reason 'owner-only local credential file'
```

This changes file permissions, not passwords. Review secret quality privately. Never print passwords to prove that they are strong. `.env.example` contains setup guidance, not reusable production credentials. If a secret entered Git history, removing the current file does not revoke copies: rotate the affected credential and coordinate history cleanup according to repository policy.

Changing `POSTGRES_PASSWORD` in `.env` does not change the password of an existing PostgreSQL role. Likewise Grafana's initial admin environment variables do not reliably reset an existing database account. Rotation requires changing the actual server-side credential, updating consumers, verifying access, and revoking the previous value. Record who performed that governed change without storing either password.

Compose secrets can mount files under `/run/secrets`, but this application's typed settings do not automatically read a `_FILE` variable. Adopting file-based secrets requires an explicit settings implementation and tests. A local Compose secret file is not an encrypted organizational secret manager. Production secret distribution, backup encryption and access auditing remain separate work.

## 6. Review Administrative APIs Without Invoking Destructive Actions

```bash
api -fsS "$PROM_URL/api/v1/status/flags" > "$LAB_DIR/prometheus-flags.json"
jq '.data | with_entries(select(.key | test("web.enable-(admin-api|lifecycle|remote-write-receiver)")))' \
  "$LAB_DIR/prometheus-flags.json"
jq -e '.data["web.enable-admin-api"]=="false" and .data["web.enable-lifecycle"]=="false"' \
  "$LAB_DIR/prometheus-flags.json"
GRAFANA_HTTP=${GRAFANA_URL:-http://127.0.0.1:3000}
STATUS=$(curl --noproxy '*' -sS --connect-timeout 2 --max-time 10 -o "$LAB_DIR/grafana-anonymous.json" \
  -w '%{http_code}' "$GRAFANA_HTTP/api/datasources")
printf '%s\n' "$STATUS" > "$LAB_DIR/grafana-anonymous-status.txt"
test "$STATUS" = 401
```

Lab 41 intentionally enabled the Prometheus remote-write receiver for Tempo-generated metrics. It is a write surface, even though destructive admin APIs and HTTP lifecycle endpoints remain disabled. Keep it behind the learning network/loopback boundary. Use container restart for a validated configuration change rather than enabling reload solely for convenience.

Alertmanager permits operations such as silencing and alert submission within its trusted access model. Loki, Tempo, Pyroscope and Collector ingest/query endpoints also require a controlled network boundary. Do not send fake production alerts or destructive API calls as a “security check.” Review flags, access paths and documented capabilities instead.

For internet deployment, add authenticated authorization-aware ingress, TLS, rate limits and an explicit allowlist of paths. HTTPS transport and login solve different problems. The API also needs domain authorization before real multi-user data is served. No proxy or access-control deployment is added during this local review.

## 7. Inject Synthetic Secrets With Positive Controls

```bash
cat > lab-notes/operations/redaction_probe.py <<'PYTHON'
"""Use only synthetic secrets. Save IDs for positive-control telemetry checks."""
import argparse,json,time,uuid
from urllib.parse import urlencode
from urllib.request import ProxyHandler,Request,build_opener
p=argparse.ArgumentParser();p.add_argument('base_url');p.add_argument('output');a=p.parse_args()
marker='FAKE-LAB48-'+uuid.uuid4().hex
opener=build_opener(ProxyHandler({}));started=time.time_ns()
def call(path,method='GET',body=None):
    rid='redaction-'+str(uuid.uuid4())
    headers={'X-Request-ID':rid,'Authorization':'Bearer '+marker,'Cookie':'lab_secret='+marker}
    data=None if body is None else json.dumps(body).encode()
    if data is not None:headers['Content-Type']='application/json'
    with opener.open(Request(a.base_url.rstrip('/')+path,data=data,headers=headers,method=method),timeout=12) as response:
        raw=response.read();document=json.loads(raw) if raw else None
        assert response.headers['X-Request-ID']==rid
        return {'request_id':rid,'status':response.status,'response':document}
records={}
records['profile']=call('/api/v1/demo/profile-work?'+urlencode({'variant':'cpu','iterations':3000000,'secret':marker}))
records['create']=call('/api/v1/items','POST',{'name':'lab48-'+uuid.uuid4().hex,'description':marker,'price':'4.80'})
item_id=records['create']['response']['id']
try:
    assert records['profile']['status']==200 and records['create']['status']==201
    assert records['create']['response']['description']==marker
finally:
    records['delete']=call('/api/v1/items/'+item_id,'DELETE')
with open(a.output,'x') as f:
    json.dump({'synthetic_marker':marker,'start_ns':started,'end_ns':time.time_ns(),'records':records},f,indent=2)
print(json.dumps({'output':a.output,'profile_trace_id':records['profile']['response']['trace_id']}))
PYTHON
```

```bash
START_UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)
python3 lab-notes/operations/redaction_probe.py "$APP_URL" "$LAB_DIR/redaction-probe.json"
sleep 20
dp logs --since "$START_UTC" --no-color app > "$LAB_DIR/app-probe.log"
TRACE_ID=$(jq -er '.records.profile.response.trace_id' "$LAB_DIR/redaction-probe.json")
fetch_trace "$TRACE_ID" "$LAB_DIR/profile-trace.json"
python3 lab-notes/tracing/inspect_trace.py "$LAB_DIR/profile-trace.json" > "$LAB_DIR/profile-spans.json"
START_NS=$(jq -er '.start_ns' "$LAB_DIR/redaction-probe.json")
END_NS=$(python3 -c 'import time; print(time.time_ns())')
for operation in profile create delete; do
  RID=$(jq -er --arg op "$operation" '.records[$op].request_id' "$LAB_DIR/redaction-probe.json")
  QUERY="{service_name=\"$LAB_SERVICE\",deployment_environment_name=\"$LAB_ENVIRONMENT\"} | json | request_id=\"$RID\""
  backend loki:3100 /loki/api/v1/query_range query "$QUERY" start "$START_NS" end "$END_NS" limit 100 \
    > "$LAB_DIR/$operation-logs.json"
done
api -fsS "$APP_URL/metrics" > "$LAB_DIR/probe-metrics.prom"
```

The marker is fake and unique. It appears in an Authorization header, a cookie, an extra query parameter and one item description. The body is expected to contain it in the create response and persistence, because it is user data; the helper deletes its own item afterward. It should not appear in emitted logs, metric labels or retained trace attributes.

The endpoint accepting the fake Authorization header demonstrates that the learning API has no authentication enforcement. It does not demonstrate that the token was valid or checked. Do not use real secrets in query strings even when this controlled redaction test passes.

## 8. Assert Absence Only After Proving Collection Worked

```bash
python3 - "$LAB_DIR" <<'PYTHON'
import json,sys
from pathlib import Path
root=Path(sys.argv[1]);probe=json.loads((root/'redaction-probe.json').read_text())
marker=probe['synthetic_marker']
spans=json.loads((root/'profile-spans.json').read_text())
assert any(s['attributes'].get('app.operation')=='sum_squares' for s in spans),'No retained positive-control worker span'
local=(root/'app-probe.log').read_text()
for op,record in probe['records'].items():
    assert record['request_id'] in local,('Missing local positive control',op)
    document=json.loads((root/(op+'-logs.json')).read_text())
    records=[line for stream in document['data']['result'] for _,line in stream['values']]
    assert records and any(record['request_id'] in line for line in records),('Missing Loki positive control',op)
for filename in ('app-probe.log','profile-trace.json','profile-spans.json',
                 'profile-logs.json','create-logs.json','delete-logs.json','probe-metrics.prom'):
    assert marker not in (root/filename).read_text(),('Synthetic secret leaked',filename)
print('Positive controls present; marker absent from tested telemetry artifacts.')
PYTHON
```

Inspect normalized trace attributes and span events. The inherited Collector transform removes SQL statement text, complete URLs/query strings and verbose exception fields before storage. Application logging avoids sensitive fields before they enter stdout. Downstream redaction does not undo a leak already written into Docker's local cache, which is why both boundaries are tested.

An empty query cannot pass a confidentiality test. A failed positive control means the test is inconclusive until the transport or query is fixed. A passing marker test covers these inputs and this configuration; it is not a proof that every code path is free of sensitive information.

## 9. Audit-Relevant Records and Findings Register

A `created_item` or request log provides operational evidence. Without authenticated actor identity, governed action semantics, reliable delivery and protected retention it is not a trustworthy security audit trail. Keep these categories distinct:

| Record | What it establishes | Missing guarantee |
|---|---|---|
| App log with request/trace ID | Correlated execution observation | Authenticated actor and durable audit delivery |
| Operator change journal | Stated action, time and target | Independent identity verification and tamper protection |
| Deployment image ID | Exact local artifact selected | Approval, supply-chain provenance and signing |
| Synthetic marker test | Tested fields stayed out of observed telemetry | Universal absence of confidential data |

Create `findings.md` in this run's evidence directory. For each actual finding record: evidence file/line, affected boundary, realistic impact, remediation owner, priority, target date, verification command, and accepted residual risk. Do not populate it with invented vulnerabilities just to fill a table. At minimum record the deliberate unauthenticated local API/demo exposure, the retained Prometheus remote-write surface, and host-level trust assumptions as explicit deployment constraints.

**Exercise:** propose an audit event schema with authenticated actor, action, target, outcome, timestamp and correlation ID. Explain where authentication would come from and how delivery/retention would differ from the current best-effort diagnostic logging path. Do not claim that adding `actor="admin"` makes a record trustworthy.

## 10. Troubleshooting and Final Checks

| Observation | Interpretation | Follow-up |
|---|---|---|
| Inventory reports wildcard port | Effective overlay changed the binding | Correct the responsible file, recreate that service, re-inventory |
| Runtime user is blank | Image default may be root | Inspect image `Config.User`; fix the image/service deliberately |
| Grafana anonymous query returns 200 | Anonymous/auth configuration changed | Review Grafana settings and access policy before continuing |
| Marker appears only in local log | App logging leaked before Collector sanitization | Fix the emitting logger, then rerun with a new marker |
| Marker appears in trace | Unexpected attribute survives transformation | Inspect exact key, add a targeted transform, validate Collector and repeat |
| No marker and no request record | Broken observation path | Restore positive controls before claiming redaction |

If remediation changes application logging or Collector transforms, preserve request/trace IDs and low-cardinality operational fields. Re-run tests, native configuration validation and the fresh marker experiment. Avoid indiscriminate field deletion that makes incidents impossible to diagnose.

```bash
dp config --quiet
wait_ready
wait_backend loki:3100 /ready
wait_backend tempo:3200 /ready
wait_backend pyroscope:4040 /ready
python3 lab-notes/operations/change_event.py "$LAB_DIR/changes.jsonl" review telemetry completed --reason 'exposure inventory and synthetic-marker checks recorded'
```

Technical references: [Prometheus security model](https://prometheus.io/docs/operating/security/), [Compose secrets](https://docs.docker.com/compose/how-tos/use-secrets/), and [Grafana security configuration](https://grafana.com/docs/grafana/latest/setup-grafana/configure-security/).

## 11. Knowledge Check

1. Why is downstream log redaction insufficient by itself?
2. Does a request ID prove who acted?
3. Why does changing a database password variable not necessarily rotate a role?
4. Why keep remote write enabled while documenting its risk?

### Answer Guide

1. The sensitive value may already exist in stdout, Docker caches or other upstream records.
2. No. It is a correlation value, and valid client-supplied IDs are accepted.
3. Initialization environment variables do not rewrite existing role credentials; server and consumers need coordinated changes.
4. Tempo uses the inherited internal metrics path. Restrict its access boundary rather than silently breaking a required integration.

## 12. Professional Scenario Exercise

A team proposes exposing every observability port to simplify remote troubleshooting. Produce a reviewed access plan: identify the necessary human entry point, internal-only receivers, authentication and TLS needs, allowed administrative operations, and temporary-access expiry. Use the actual inventory rather than a generic checklist.

## 13. Lab Notebook Template

Create a notebook in the evidence directory for this run:

```bash
test -e "$LAB_DIR/notebook.md" || printf '# Lab 48 Evidence\n' > "$LAB_DIR/notebook.md"
```

Use these headings and fill them with your predictions, commands, observed results and conclusions:

```markdown
# Lab 48 Evidence

## Trust boundaries and predictions
## Sanitized inventory
## Credential handling changes
## Administrative API evidence
## Synthetic probe and positive controls
## Findings and owners
## Recovery and remaining deployment constraints
```

## 14. Observable Completion Criteria

- [ ] Effective host listeners and runtime privileges are recorded without secret values.
- [ ] .env has owner-only permissions and its tracking status is checked.
- [ ] Administrative API flags and Grafana anonymous datasource access are checked.
- [ ] Synthetic secret probes have positive local-log, Loki and retained-trace controls.
- [ ] Findings distinguish diagnostic records from authenticated audit events.
- [ ] No extra public ports, collectors or permanent components were introduced.

## 15. Production Implications

Production hardening requires authenticated business access, centralized secret distribution, encrypted transport, controlled administrative access, tested updates and protected audit retention. Observability data deserves an explicit sensitivity policy. This lab improves and reviews a local learning stack; it does not certify it for public deployment.

## 16. End State and Transition

All services remain healthy, fixtures are removed, and local evidence is protected. Lab 49 turns configuration checks and state protection into repeatable validation, backup, restore and release checkpoints.
