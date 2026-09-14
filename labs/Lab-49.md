# Lab 49: Configuration Validation, Backup, Restore, Upgrade, and Rollback

## Purpose and Scope

> **Primary Objective:** Automate configuration gates, restore protected state into isolated targets, rehearse a pinned application release and prove rollback with preserved change evidence.

A backup file and a successful container start are incomplete recovery evidence. This lab validates the active configuration, restores a PostgreSQL dump into a separate database, checks a cold Grafana backup, and promotes then rolls back a small application release.

All restore targets are new and lab-owned. The source database and live volumes are never overwritten. The upgrade changes application behavior and version metadata while keeping the database schema and dependency versions unchanged. Major database migrations, backend storage-format upgrades, unattended production deployment and a full disaster-recovery site are outside this exercise.

## 1. Inherited State, Tools and Safety Boundaries

```bash
source lab-notes/session.sh
source lab-notes/evidence.sh
source lab-notes/metrics-session.sh
source lab-notes/platform-session.sh
source lab-notes/traces-session.sh
source lab-notes/profiling/session.sh
load_app_settings
start_lab 49
umask 077
chmod 700 "$LAB_DIR"
wait_ready
wait_backend loki:3100 /ready
wait_backend tempo:3200 /ready
wait_backend pyroscope:4040 /ready
mkdir -p lab-notes/operations
TOKEN=$(python3 -c 'import uuid; print(uuid.uuid4().hex[:12])')
BACKUP_DIR="$LAB_ROOT/lab-notes/backups/lab49-backup-$TOKEN"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
printf '%s\n' "$BACKUP_DIR" > "$LAB_DIR/backup-location.txt"
```

Complete [Lab 48](Lab-48.md). Stop unrelated load generators and external database writers. Use the current `dp` overlays, Python 3, jq, Docker and the existing PyYAML environment. `pg_dump`, `pg_restore`, `createdb` and `psql` run inside the pinned PostgreSQL 17 container; no host PostgreSQL installation is required.

Backups contain business data and potentially credentials in Grafana state. Owner-only permissions are a local safeguard, not encryption or off-host protection. Keep these artifacts outside Git, restrict access, and use approved encrypted storage for real recovery copies. Never attach a dump or `.env` to a public issue. Check `docker system df` and `df -h .` first: image archives, candidate images and database copies require additional free disk space.

## 2. Define Checkpoints Before Taking Action

| Checkpoint | Gate | Evidence |
|---|---|---|
| Configuration | Active Compose and native validators pass | Tool output and sanitized inventory |
| Database backup | Dump completes; quiesced fingerprints agree | Dump, revision, row count and content hash |
| Database restore | Isolated restored state matches; API can read it | Restore log and isolated readiness/CRUD read |
| Grafana backup | Service stopped before state copy | Cold archive and restored SQLite integrity |
| Candidate release | Exact image ID, tests, readiness and behavior pass | Version/header, known row, change record |
| Rollback | Recorded baseline image and version restored | Old behavior, same durable row, fresh signals |

**Prediction checkpoint:** does `docker compose down -v` belong in a rollback? No: it destroys persistent state. Does rolling back an image undo a schema migration? No. Does a hash prove that a backup can be restored? No; both integrity checking and an actual restore are needed.

RPO describes acceptable data loss and RTO describes acceptable restoration time. Measure this rehearsal's elapsed times, but do not present them as production objectives without workload, size and failure-domain assumptions.

## 3. Automate the Active Configuration Gates

```bash
cat > lab-notes/operations/validate-platform.sh <<'BASH'
#!/usr/bin/env bash
set -euo pipefail
source lab-notes/session.sh
source lab-notes/evidence.sh
source lab-notes/metrics-session.sh
source lab-notes/platform-session.sh
source lab-notes/traces-session.sh
source lab-notes/profiling/session.sh
dp config --quiet
config_path() {
  local cid
  cid=$(dp ps -q "$1")
  test -n "$cid"
  docker inspect --format '{{json .Config.Cmd}}' "$cid" | python3 -c '
import json,sys
args=json.load(sys.stdin);flag=sys.argv[1];matches=[]
for n,arg in enumerate(args):
    if arg==flag:matches.append(args[n+1])
    elif arg.startswith(flag+"="):matches.append(arg.split("=",1)[1])
assert len(matches)==1 and matches[0].startswith("/"),(flag,"expected one absolute config path")
print(matches[0])' "$2"
}
prom_path=$(config_path prometheus --config.file)
am_path=$(config_path alertmanager --config.file)
otel_path=$(config_path otel-collector --config)
loki_path=$(config_path loki -config.file)
dp run --rm -T --no-deps --entrypoint promtool prometheus check config "$prom_path"
dp run --rm -T --no-deps --entrypoint amtool alertmanager check-config "$am_path"
dp run --rm -T --no-deps otel-collector validate "--config=$otel_path"
dp run --rm -T --no-deps loki "-config.file=$loki_path" -verify-config=true
lab-notes/.tools/bin/python - <<'PYTHON'
import json
from pathlib import Path
import yaml
count=0
for root in (Path('config'),Path('lab-notes/prometheus'),Path('lab-notes/tracing')):
    if not root.exists():continue
    for path in sorted(root.rglob('*')):
        if path.is_file() and path.suffix in ('.yaml','.yml','.json'):
            assert not path.is_symlink(),f'Review configuration symlink: {path}'
            if path.suffix=='.json':json.loads(path.read_text())
            else:list(yaml.safe_load_all(path.read_text()))
            count+=1
print(f'Parsed {count} YAML/JSON documents; runtime smoke tests remain required.')
PYTHON
BASH
```

```bash
chmod +x lab-notes/operations/validate-platform.sh
set -o pipefail
bash lab-notes/operations/validate-platform.sh 2>&1 | tee "$LAB_DIR/config-validation.log"
make test 2>&1 | tee "$LAB_DIR/application-tests.log"
dp config --format json | python3 lab-notes/operations/security_inventory.py > "$LAB_DIR/config-inventory.json"
```

The validator discovers the running service's configuration path and uses the same service image and mounts. This avoids accidentally checking the original Prometheus file while the learning overlay uses another file. The command stops on the first failure.

The Collector, Prometheus, Alertmanager and Loki have the native checks shown. Generic YAML parsing for Tempo/Pyroscope and JSON parsing for Grafana check syntax only. Do not invent a universal `--validate` flag or claim that parsing proves every backend setting is supported. Exact-version startup checks, readiness and signal canaries complete those gates.

An expanded Compose file can contain secrets. The validation transcript uses quiet configuration checking and the allowlisted inventory from Lab 48, not a full environment dump.

## 4. Archive Source, Configuration and Release Identity

```bash
cat > lab-notes/operations/config_archive.py <<'PYTHON'
"""Archive reviewed source/configuration, excluding credentials and run artifacts."""
import argparse,hashlib,json,tarfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('output');a=p.parse_args()
root=Path.cwd();out=Path(a.output).resolve();assert not out.exists()
roots=['app','config','postgres','scripts','docs','Makefile','docker-compose.yml','.gitignore','.env.example','README.md','SECURITY.md']
files=set()
for name in roots:
    path=root/name
    if path.is_file():files.add(path)
    elif path.exists():files.update(p for p in path.rglob('*') if p.is_file())
notes=root/'lab-notes'
if notes.exists():
    files.update(p for p in notes.rglob('*') if p.is_file() and p.suffix in ('.py','.sh','.yaml','.yml','.json','.txt'))
exclude={'__pycache__','.pytest_cache','.ruff_cache','.venv','venv','.tools','evidence','backups','release','.git'}
selected=[]
for path in sorted(files):
    rel=path.relative_to(root)
    if any(part in exclude or part.startswith('lab49-backup-') for part in rel.parts):continue
    if path.name=='.env' or (path.name.startswith('.env.') and path.name!='.env.example'):continue
    # Only configuration/scripts in lab-notes, never dated evidence or notebooks.
    if rel.parts[0]=='lab-notes' and any(part.startswith('lab-') or part[:4].isdigit() for part in rel.parts[1:-1]):continue
    assert not path.is_symlink(),f'Review symlink before backup: {rel}'
    selected.append((path,rel))
with tarfile.open(out,'x:gz') as archive:
    for path,rel in selected:archive.add(path,arcname=str(rel),recursive=False)
manifest={str(rel):hashlib.sha256(path.read_bytes()).hexdigest() for path,rel in selected}
Path(str(out)+'.manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps({'files':len(selected),'archive':str(out),'secrets_file_included':False}))
PYTHON
```

```bash
python3 lab-notes/operations/config_archive.py "$BACKUP_DIR/configuration.tar.gz"
BASE_IMAGE_ID=$(docker inspect --format '{{.Image}}' "$(dp ps -q app)")
BASE_VERSION=$(api -fsS "$APP_URL/openapi.json" | jq -er '.info.version')
BASE_TAG="lab49-app:baseline-$TOKEN"
docker image tag "$BASE_IMAGE_ID" "$BASE_TAG"
jq -n --arg image "$BASE_IMAGE_ID" --arg tag "$BASE_TAG" --arg version "$BASE_VERSION" \
  '{image_id:$image,local_tag:$tag,app_version:$version}' > "$BACKUP_DIR/baseline-release.json"
docker image save "$BASE_TAG" > "$BACKUP_DIR/baseline-image.tar"
python3 lab-notes/operations/change_event.py "$LAB_DIR/changes.jsonl" checkpoint app completed --reason "$BASE_IMAGE_ID"
```

Review the archive manifest before copying it elsewhere. It excludes `.env`, tool environments, run evidence, backups and the candidate release workspace. It preserves source, migrations, provisioning, scripts and learning configuration. Custom files can still embed secrets despite an innocent filename; classification requires review.

Record secret-manager references and the recovery procedure separately. This archive intentionally cannot recreate credentials by itself. Preserve PostgreSQL role/owner bootstrap information and Grafana's configured encryption key through the secret-management process. Grafana datasource secrets encrypted with a custom `GF_SECURITY_SECRET_KEY` require that same key during restoration.

A local tag is a convenient name, not immutable identity. Record the content-addressed image ID and verify it before deployment. The image archive permits local recovery if its tag disappears; `docker image load -i` restores the saved artifact. Dependency lock files, base-image pins and release notes belong to the same checkpoint.

## 5. Make a Logical PostgreSQL Backup and Manifest

```bash
PAYLOAD=$(jq -n --arg name "lab49-checkpoint-$TOKEN" '{name:$name,price:"49.00"}')
python3 lab-notes/operations/request_probe.py "$APP_URL" /api/v1/items "$LAB_DIR/checkpoint-item.json" \
  --method POST --body "$PAYLOAD" --expect 201
ITEM_ID=$(jq -er '.response.id' "$LAB_DIR/checkpoint-item.json")
cat > lab-notes/operations/database-fingerprint.sql <<'SQL'
SELECT jsonb_build_object(
  'row_count', count(*),
  'rows_md5', md5(COALESCE(jsonb_agg(to_jsonb(i) ORDER BY id), '[]'::jsonb)::text),
  'alembic_revision', (SELECT version_num FROM alembic_version)
) FROM items AS i;
SQL
(
  set -euo pipefail
  trap 'dp start app >/dev/null' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  dp stop -t 30 app
  dbsql -At < lab-notes/operations/database-fingerprint.sql > "$BACKUP_DIR/database-before.json"
  dp exec -T postgres sh -ec 'exec pg_dump -U postgres -d "$APP_DB_NAME" --format=custom --no-owner --no-acl' \
    > "$BACKUP_DIR/items.dump.partial"
  dbsql -At < lab-notes/operations/database-fingerprint.sql > "$BACKUP_DIR/database-after.json"
  diff -u "$BACKUP_DIR/database-before.json" "$BACKUP_DIR/database-after.json"
  mv "$BACKUP_DIR/items.dump.partial" "$BACKUP_DIR/items.dump"
)
wait_ready
python3 lab-notes/operations/change_event.py "$LAB_DIR/changes.jsonl" backup postgres completed --reason 'logical dump and quiesced state fingerprint'
```

`pg_dump` takes a consistent logical snapshot even with concurrent writers. The brief app stop is used here so a separate before/after fingerprint describes the same state as the dump. It is not a requirement of `pg_dump`, and it does not stop other clients you may have introduced. Any fingerprint mismatch invalidates this checkpoint until writers are controlled and the backup is repeated in a new directory.

The dump contains database objects and data, not cluster-wide role credentials. `--no-owner --no-acl` supports restoration under the known application role. The fingerprint's MD5 is a compact equality check for this lab, not a cryptographic authenticity claim. The archive hashes generated later use SHA-256.

## 6. Restore to a New Database and Verify Content

```bash
RESTORE_DB="lab49_restore_$TOKEN"
dp exec -T -e RESTORE_DB="$RESTORE_DB" postgres sh -ec \
  'exec createdb -U postgres -O "$APP_DB_USER" "$RESTORE_DB"'
dp exec -T -e RESTORE_DB="$RESTORE_DB" postgres sh -ec \
  'exec pg_restore -U postgres -d "$RESTORE_DB" --role="$APP_DB_USER" --no-owner --no-acl --exit-on-error --single-transaction' \
  < "$BACKUP_DIR/items.dump" > "$LAB_DIR/restore.log" 2>&1
dp exec -T -e RESTORE_DB="$RESTORE_DB" postgres sh -ec \
  'exec psql -X -v ON_ERROR_STOP=1 -At -U postgres -d "$RESTORE_DB"' \
  < lab-notes/operations/database-fingerprint.sql > "$LAB_DIR/restored-database.json"
diff -u "$BACKUP_DIR/database-before.json" "$LAB_DIR/restored-database.json"
CLONE_NAME="lab49-restore-app-$TOKEN"
dp run -d --no-deps --name "$CLONE_NAME" \
  -e POSTGRES_DB="$RESTORE_DB" -e SERVICE_NAME=lab49-restore \
  -e OTEL_ENABLED=false -e PYROSCOPE_ENABLED=false app > "$LAB_DIR/restore-app-id.txt"
RESTORE_OK=0
for attempt in {1..45}; do
  if docker exec "$CLONE_NAME" python -c \
    'import json,urllib.request; r=json.load(urllib.request.urlopen("http://127.0.0.1:8000/health/ready",timeout=3)); assert r["status"]=="ready"' \
    > /dev/null 2>&1; then RESTORE_OK=1; break; fi
  sleep 1
done
test "$RESTORE_OK" -eq 1
docker exec -e ITEM_ID="$ITEM_ID" "$CLONE_NAME" python -c \
  'import json,os,urllib.request; r=json.load(urllib.request.urlopen("http://127.0.0.1:8000/api/v1/items/"+os.environ["ITEM_ID"],timeout=5)); assert r["price"]=="49.00"; print(json.dumps(r))' \
  > "$LAB_DIR/restored-api-item.json"
docker rm -f "$CLONE_NAME"
case "$RESTORE_DB" in lab49_restore_*) ;; *) printf '%s\n' 'Unexpected restore target'; false;; esac
dp exec -T -e RESTORE_DB="$RESTORE_DB" postgres sh -ec 'exec dropdb -U postgres "$RESTORE_DB"'
```

The restored application's unique service name gives it a separate Redis key namespace, so a cache hit from the original app cannot fake database recovery. It publishes no host ports. Readiness, migration revision, every row's content hash and an actual API read provide different checks.

If a step fails, preserve its transcript, remove only the uniquely named temporary app, and investigate the isolated database. Do not drop the source database to “start fresh.” `--single-transaction` makes a restore failure atomic within the target, but it cannot repair an incompatible schema or missing required extension.

## 7. Back Up Grafana State Cold and Test the Archive

```bash
GRAFANA_VOLUME=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/var/lib/grafana"}}{{.Name}}{{end}}{{end}}' "$(dp ps -q grafana)")
test -n "$GRAFANA_VOLUME"
HELPER_IMAGE=busybox:1.37.0-musl
docker image inspect "$HELPER_IMAGE" >/dev/null 2>&1 || docker pull "$HELPER_IMAGE"
(
  set -euo pipefail
  trap 'dp start grafana >/dev/null' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  dp stop -t 30 grafana
  docker run --rm --network none --read-only --user 10001:10001 --cap-drop ALL \
    --security-opt no-new-privileges:true -v "$GRAFANA_VOLUME:/source:ro" "$HELPER_IMAGE" \
    tar -C /source -czf - . > "$BACKUP_DIR/grafana-data.tar.gz.partial"
  mv "$BACKUP_DIR/grafana-data.tar.gz.partial" "$BACKUP_DIR/grafana-data.tar.gz"
)
wait_grafana
RESTORE_VOLUME="lab49-grafana-restore-$TOKEN"
docker volume create --label learning.lab=49 --label "learning.run=$TOKEN" "$RESTORE_VOLUME" > /dev/null
docker run --rm --network none --read-only --user 0:0 --cap-drop ALL --cap-add CHOWN \
  --security-opt no-new-privileges:true -v "$RESTORE_VOLUME:/restore" "$HELPER_IMAGE" chown 10001:10001 /restore
docker run --rm -i --network none --read-only --user 10001:10001 --cap-drop ALL \
  --security-opt no-new-privileges:true -v "$RESTORE_VOLUME:/restore" "$HELPER_IMAGE" \
  tar -C /restore -xzf - < "$BACKUP_DIR/grafana-data.tar.gz"
docker run --rm --network none --read-only --user 10001:10001 --cap-drop ALL \
  --security-opt no-new-privileges:true -v "$RESTORE_VOLUME:/restore" --entrypoint python "$BASE_IMAGE_ID" -c \
  'import sqlite3; c=sqlite3.connect("/restore/grafana.db"); assert c.execute("PRAGMA integrity_check").fetchone()[0]=="ok"; print({"dashboards":c.execute("SELECT count(*) FROM dashboard").fetchone()[0]})' \
  > "$LAB_DIR/grafana-restore-check.txt"
docker volume rm "$RESTORE_VOLUME"
```

Grafana is stopped before copying its SQLite database and related state. The helper is a short-lived, pinned utility container, not another platform service. It runs without a network or host directory mounts; the brief root operation only prepares the new restore volume's ownership.

The SQLite check opens only the writable restore copy, allowing SQLite to process any copied WAL safely. It verifies structural integrity and the dashboard table. It does not validate every encrypted datasource credential or Grafana migration. A full Grafana upgrade drill also starts the target version against a separate restored copy with the correct encryption key. Never run two Grafana versions simultaneously against the same SQLite volume.

| State | Recovery approach for this stack |
|---|---|
| PostgreSQL | Tested logical dump plus role/secret bootstrap; larger systems may need physical backup/PITR |
| Grafana | Provisioning plus stopped-service state backup and encryption-key custody |
| Redis | Rebuild cache from PostgreSQL; avoid restoring stale cached rows as authoritative data |
| Prometheus | Supported snapshot or clean shutdown copy with exact-version compatibility checks |
| Loki, Tempo, Pyroscope | Coordinated clean shutdown/cold volume copy or documented backend backup procedure |
| Alertmanager | Configuration plus silences/notification state according to operational needs |
| Collector | Config and deliberate queue recovery policy; queue copy is not complete telemetry history |

Do not copy live database directories with a generic tar command and call them consistent backups. Recovery of several backends at a single global instant is a different coordination problem.

## 8. Build a Small Pinned Application Upgrade

```bash
RELEASE_ROOT="$LAB_ROOT/lab-notes/operations/release/$TOKEN"
mkdir -p "$RELEASE_ROOT"
python3 - "$RELEASE_ROOT" <<'PYTHON'
import shutil,sys
from pathlib import Path
out=Path(sys.argv[1])/'app'
shutil.copytree('app',out,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache','.ruff_cache','.venv','venv','.env'))
PYTHON
```

```bash
cat > "$RELEASE_ROOT/app/app/release_header.py" <<'PYTHON'
"""Small, observable application release change; no database schema change."""
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class ReleaseHeaderMiddleware:
    def __init__(self, app: ASGIApp, version: str) -> None:
        self.app = app
        self.version = version.encode('ascii')

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        async def send_with_version(message: Message) -> None:
            if message['type'] == 'http.response.start':
                message = dict(message)
                headers = [(key, value) for key, value in message.get('headers', [])
                           if key.lower() != b'x-app-version']
                message['headers'] = [*headers, (b'x-app-version', self.version)]
            await send(message)

        await self.app(scope, receive, send_with_version)
PYTHON
```

```bash
python3 - "$RELEASE_ROOT/app/app/main.py" <<'PYTHON'
from pathlib import Path
import sys
p=Path(sys.argv[1]);text=p.read_text()
needle='    telemetry.instrument(app, database, cache)'
assert text.count(needle)==1,'Expected the inherited app instrumentation point'
text=text.replace(needle,'    from app.release_header import ReleaseHeaderMiddleware\n    app.add_middleware(ReleaseHeaderMiddleware, version=settings.app_version)\n'+needle)
p.write_text(text)
PYTHON
CANDIDATE_TAG="lab49-app:candidate-$TOKEN"
CANDIDATE_VERSION=1.0.1
test "$BASE_VERSION" != "$CANDIDATE_VERSION"
docker build --target test -t "lab49-tests:$TOKEN" "$RELEASE_ROOT/app"
docker run --rm --network none "lab49-tests:$TOKEN" > "$LAB_DIR/candidate-tests.log" 2>&1
docker build --target runtime -t "$CANDIDATE_TAG" "$RELEASE_ROOT/app"
CANDIDATE_ID=$(docker image inspect --format '{{.Id}}' "$CANDIDATE_TAG")
jq -n --arg image "$CANDIDATE_ID" --arg tag "$CANDIDATE_TAG" --arg version "$CANDIDATE_VERSION" \
  '{image_id:$image,local_tag:$tag,app_version:$version}' > "$BACKUP_DIR/candidate-release.json"
```

The candidate adds `X-App-Version` through a small ASGI middleware and sets the release version through typed configuration. The original application source and baseline image stay intact. The new header is observable behavior; this is more than relabeling the same image. The runtime packages, Python base and schema remain pinned to the inherited versions.

The candidate test image exercises the copied source. Review its test transcript before promotion. If your inherited baseline already uses version 1.0.1, choose a different explicit local patch version and record that decision; do not silently reuse the baseline version.

## 9. Promote, Verify and Roll Back by Recorded Identity

```bash
cat > lab-notes/operations/compose.release.yaml <<'YAML'
services:
  app:
    image: ${LAB_RELEASE_IMAGE:?Select the recorded release image}
    build: !reset null
    environment:
      APP_VERSION: ${LAB_RELEASE_VERSION:?Select the release version}
YAML
drelease() { dp -f "$LAB_ROOT/lab-notes/operations/compose.release.yaml" "$@"; }
export LAB_RELEASE_IMAGE="$CANDIDATE_TAG" LAB_RELEASE_VERSION="$CANDIDATE_VERSION"
test "$(docker image inspect --format '{{.Id}}' "$LAB_RELEASE_IMAGE")" = "$CANDIDATE_ID"
drelease config --quiet
python3 lab-notes/operations/change_event.py "$LAB_DIR/changes.jsonl" promote app intended --reason "$CANDIDATE_ID"
drelease up -d --no-deps --no-build --pull never app
wait_ready
test "$(docker inspect --format '{{.Image}}' "$(dp ps -q app)")" = "$CANDIDATE_ID"
api -fsS -D "$LAB_DIR/candidate-headers.txt" "$APP_URL/health/live" > "$LAB_DIR/candidate-live.json"
python3 - "$LAB_DIR/candidate-headers.txt" "$CANDIDATE_VERSION" <<'PYTHON'
from pathlib import Path
import sys
headers=Path(sys.argv[1]).read_text().lower()
assert ('x-app-version: '+sys.argv[2]) in headers
PYTHON
api -fsS "$APP_URL/openapi.json" | jq -e --arg version "$CANDIDATE_VERSION" '.info.version==$version'
rcli DEL "$(cache_key "$ITEM_ID")" > /dev/null
api -fsS "$APP_URL/api/v1/items/$ITEM_ID" > "$LAB_DIR/candidate-item.json"
jq -e '.price=="49.00"' "$LAB_DIR/candidate-item.json"
python3 lab-notes/operations/change_event.py "$LAB_DIR/changes.jsonl" promote app verified --reason 'version header, readiness and durable item checked'
export LAB_RELEASE_IMAGE="$BASE_TAG" LAB_RELEASE_VERSION="$BASE_VERSION"
test "$(docker image inspect --format '{{.Id}}' "$LAB_RELEASE_IMAGE")" = "$BASE_IMAGE_ID"
drelease config --quiet
drelease up -d --no-deps --no-build --pull never app
wait_ready
test "$(docker inspect --format '{{.Image}}' "$(dp ps -q app)")" = "$BASE_IMAGE_ID"
api -fsS -D "$LAB_DIR/rollback-headers.txt" "$APP_URL/health/live" > "$LAB_DIR/rollback-live.json"
python3 - "$LAB_DIR/rollback-headers.txt" <<'PYTHON'
from pathlib import Path
import sys
assert 'x-app-version:' not in Path(sys.argv[1]).read_text().lower()
PYTHON
api -fsS "$APP_URL/openapi.json" | jq -e --arg version "$BASE_VERSION" '.info.version==$version'
rcli DEL "$(cache_key "$ITEM_ID")" > /dev/null
api -fsS "$APP_URL/api/v1/items/$ITEM_ID" > "$LAB_DIR/rollback-item.json"
jq -e '.price=="49.00"' "$LAB_DIR/rollback-item.json"
python3 lab-notes/operations/change_event.py "$LAB_DIR/changes.jsonl" rollback app verified --reason "$BASE_IMAGE_ID"
unset LAB_RELEASE_IMAGE LAB_RELEASE_VERSION
unset -f drelease
```

If a candidate gate fails, run the baseline export, identity check and `drelease up` block immediately, then capture the failure evidence. Do not proceed as if promotion succeeded. The override is deliberately not added to the persistent `dp` overlay list. After rollback the standard composition and original source remain the default for later labs.

Process counters reset when the app is replaced; rate functions handle resets, but record the deployment window. `service.version` distinguishes releases in traces, while business IDs persist in PostgreSQL. No Alembic downgrade is involved because this release did not migrate the schema.

For a future backend or database upgrade, first read exact-version release notes, verify storage and migration compatibility, restore a backup into an isolated target, and test forward and backward behavior. An old image may be unable to read storage rewritten by a new version. In that case rollback requires a compatible restore and an explicit data-loss decision; an image switch alone is unsafe.

## 10. Complete the Recovery Proof and Protect the Checkpoint

```bash
python3 lab-notes/profiling/profile_load.py "$APP_URL" "$LAB_DIR/rollback-canary.jsonl" \
  --variant cpu --count 12 --iterations 3000000
sleep 20
TRACE_ID=$(jq -rs '.[0].response.trace_id' "$LAB_DIR/rollback-canary.jsonl")
fetch_trace "$TRACE_ID" "$LAB_DIR/rollback-trace.json"
python3 lab-notes/operations/request_probe.py "$APP_URL" "/api/v1/items/$ITEM_ID" "$LAB_DIR/checkpoint-delete.json" \
  --method DELETE --expect 204
bash lab-notes/operations/validate-platform.sh > "$LAB_DIR/final-validation.log" 2>&1
wait_ready
wait_grafana
pq 'up' > "$LAB_DIR/final-targets.json"
python3 - "$BACKUP_DIR" <<'PYTHON'
import hashlib,json,sys
from pathlib import Path
root=Path(sys.argv[1]);hashes={}
for path in sorted(root.iterdir()):
    if path.is_file() and path.name!='sha256.json':
        h=hashlib.sha256()
        with path.open('rb') as f:
            for chunk in iter(lambda:f.read(1048576),b''):h.update(chunk)
        hashes[path.name]=h.hexdigest()
(root/'sha256.json').write_text(json.dumps(hashes,indent=2)+'\n')
print('Checkpoint hashes written; keep the trusted manifest separately for tamper detection.')
PYTHON
```

Use Lab 47's known-ID Loki query and fresh profile query on the rollback canary. Confirm that all four signals resumed, not just that Tempo returned a trace. Keep the backup and evidence; delete temporary restore containers/databases/volumes only after their checks. Do not prune images or volumes as an automatic final step.

| Failure | Investigation |
|---|---|
| Validator checks a missing file | Compare running command, overlay mount and native binary version |
| Dump is empty or partial | Check exit status and disk space; never rename an unsuccessful partial dump |
| Restore permission error | Verify database owner and existing application role; preserve original ACL policy separately |
| Fingerprints differ | Look for concurrent writers, timezone differences or a different source database |
| Grafana SQLite check fails | Verify clean shutdown and a complete archive; do not repair the live database blindly |
| Candidate fails readiness | Roll back recorded image/version, inspect safe errors, then diagnose offline |
| Rollback cannot read data | Stop; reassess schema/storage compatibility and restore strategy |

References: [PostgreSQL logical backups](https://www.postgresql.org/docs/17/backup-dump.html), [pg_restore](https://www.postgresql.org/docs/17/app-pgrestore.html), [Grafana backup](https://grafana.com/docs/grafana/latest/administration/back-up-grafana/), and [Compose merge/reset rules](https://docs.docker.com/reference/compose-file/merge/).

## 11. Knowledge Check

1. Why stop writers if pg_dump already takes a consistent snapshot?
2. Why is the clone service name different?
3. Why preserve a Grafana encryption key separately?
4. When is image rollback insufficient?

### Answer Guide

1. This drill compares separately collected fingerprints with the dump; quiescence makes those measurements comparable.
2. It prevents original-service cache entries from falsely proving the restored database works.
3. Encrypted datasource secrets may be unreadable without the key used to encrypt them.
4. When a schema/storage change is incompatible, data was transformed, or the old version cannot safely read the new state.

## 12. Professional Scenario Exercise

A patch deploys successfully but breaks an important read path. Write a five-step decision record that names the baseline image, the schema compatibility gate, the restoration checkpoint, the user-impact window and the recovery evidence. Explain what would change if the release had performed an irreversible migration.

## 13. Lab Notebook Template

Create a notebook in the evidence directory for this run:

```bash
test -e "$LAB_DIR/notebook.md" || printf '# Lab 49 Evidence\n' > "$LAB_DIR/notebook.md"
```

Use these headings and fill them with your predictions, commands, observed results and conclusions:

```markdown
# Lab 49 Evidence

## Validation gates
## Backup scope and secret custody
## Database fingerprint and restored API
## Grafana restore evidence
## Release image identities
## Promotion and rollback timeline
## Fresh signal checks
## Measured recovery time and limitations
```

## 14. Observable Completion Criteria

- [ ] Active native configuration checks and application tests pass.
- [ ] Configuration/image identity and protected backups are preserved.
- [ ] A separate PostgreSQL restore matches the full fingerprint and serves an API read.
- [ ] A cold Grafana copy restores into a new volume with SQLite integrity checked.
- [ ] A real candidate image changes version/header behavior and passes gates.
- [ ] Rollback restores the recorded baseline image and preserves durable data.
- [ ] Temporary restore targets are removed and fresh telemetry is verified.

## 15. Production Implications

Production backup programs need scheduled tested restores, retention, encryption, off-host copies, access control and measured recovery objectives. Release safety also needs provenance, approvals and schema compatibility strategy. This single-node rehearsal establishes repeatable evidence; it does not provide redundant storage or automatic failover.

## 16. End State and Transition

The original application image/version is running, schema and data remain intact, restore targets are removed and protected checkpoints are retained. Lab 50 uses these operating habits in a multi-fault game day and evidence-based postmortem.
