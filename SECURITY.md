# Security policy and deployment boundary

This repository is a local learning environment. It does not provide application authentication, authorization, tenant isolation or HA infrastructure. Do not publish it directly to the internet or put real customer data into its API or telemetry.

## Secrets and identity

`.env.example` contains public demonstration credentials, never production secrets. `scripts/bootstrap.sh` generates independent random application DB, DB administrator, Redis and Grafana passwords when `.env` is absent; it never overwrites existing credentials. Restrict `.env` to mode 0600, keep it out of backups without encryption, and never commit it. `.gitignore` and the Docker build context exclusion reduce accidental inclusion but cannot revoke a leaked secret. Revoke/rotate exposed credentials and remove them from repository history where applicable.

Environment variables are convenient locally and visible to Docker administrators and diagnostic tooling. Redis's password is also an argument to its server process. Use a real secret manager or supported mounted secret files in a production deployment; implement `_FILE` handling deliberately instead of assuming all images and the app share that convention. Do not print expanded `docker compose config` output into public tickets because it contains credentials.

PostgreSQL bootstrap creates a non-superuser application role and database. That role owns the schema for local Alembic use; production should separate an offline migration role from a least-privilege runtime role. Host authentication uses SCRAM. PostgreSQL and Redis have no published host ports. The PostgreSQL administrator password is not sent to the app.

Credentials in initializers apply only to a new data volume. Changing `.env` does not rotate a stored PostgreSQL role or existing Grafana administrator account. Rotate the account with its supported administrative interface, update the consuming secret, restart/recreate the consumer, verify access and revoke the old credential. Redis credentials must change in both the Redis service and the app together. Take care to preserve data rather than deleting volumes to rotate passwords.

Grafana disables anonymous access and sign-up. Use individual users/SSO with least privilege in production. An authenticated Grafana user may gain access to sensitive telemetry through provisioned datasources; datasource proxying is not a substitute for telemetry access control.

## Containers and host

All long-running services use non-root users and dropped Linux capabilities. A network-less, one-shot root container changes ownership only on the nine named volume roots. PostgreSQL needs its normal writable runtime filesystem; other service roots are read-only with narrow writable mounts/tmpfs. Do not replace these controls with privileged mode to work around permissions.

The stack neither mounts `/var/run/docker.sock` nor host container log directories. Docker's built-in fluentd log driver sends only the app's stdout to the Collector; the Collector runs non-root. Docker access itself is effectively administrative access to the host. Keep Docker Engine, Compose, kernel and host packages patched. Review bind mount labels on SELinux hosts.

Exact image version tags are pinned; tags can be rebuilt upstream. A stronger production build records immutable image digests, provenance and an SBOM and scans both images and lock files. Python installs require pinned hashes and binary wheels; development tools are excluded from the runtime stage. Scan results are time-specific and are not implied by this repository's tests.

## Network and telemetry exposure

Published human endpoints bind to 127.0.0.1 only: API 8000, Grafana 3000, Prometheus 9090, Alertmanager 9093 and Pyroscope 4040. Collector Fluent Forward port 8006 is loopback-bound for the Docker daemon. The remaining endpoints are reachable inside the project Docker network. Do not assume a shared Docker bridge provides tenant isolation.

The API and most observability endpoints have no authentication/TLS. Keep them local, use SSH tunnels for a remote VM and avoid public security-group/firewall rules. For production, place human endpoints behind a maintained TLS reverse proxy and identity provider, restrict metrics/OTLP endpoints to trusted senders, and use authenticated/encrypted service communication where appropriate. Explicitly configure trusted proxy addresses before enabling forwarded-header processing; Uvicorn disables it here.

Collector resource processors assign one configured service/environment to incoming app logs. This is suitable for the single trusted application; it does not authenticate log origin. Parent-based trace sampling trusts upstream context. At a public edge, review context propagation and sampling policy to prevent untrusted callers from influencing telemetry cost.

## Data minimization and API controls

Never log authorization headers, cookies, passwords, connection strings, raw request bodies or arbitrary URLs/query parameters. The formatter includes selected fields and sanitized exception frames; the Collector removes statement text, raw URL attributes and exception details from traces. Correlation IDs are validated, bounded and untrusted; they grant no access. Item fields should contain synthetic learning data only.

Future instrumentation can introduce new attributes or native SDK output. Review telemetry after every instrumentation update and set appropriate retention/access policies. Trace IDs and request IDs are metadata/body fields, not high-cardinality Loki/Prometheus index labels. Retention is best effort and asynchronous, not proof of regulated deletion or a hard storage quota.

Application limits bound pagination and the diagnostic CPU workload. The demo is disabled outside local/test configuration. This is not a complete DoS defense: add request body limits, authentication, authorization, rate limits, quotas, tenant boundaries and ingress timeouts before serving untrusted traffic. Redis data is eventually consistent; do not cache authorization decisions or financial correctness using this example unchanged.

## Updates and production deployment

Review upstream security advisories for Python, application libraries, Docker images and Grafana plugins. Update a coherent version set, regenerate both hashed requirement locks in Linux Python 3.12, rebuild without stale layers, run tests/native validators and verify all signals on a disposable Compose deployment. Test migrations and rollback/forward recovery against restored PostgreSQL backups. Review version-specific Tempo/Loki/Pyroscope storage changes before upgrading persisted volumes.

For production, add durable managed storage, tested encrypted off-host backups, restore drills, RPO/RTO targets, capacity limits, HA/failover where required, separate runtime/migration accounts, secrets rotation, incident ownership and independent monitoring. This single-node stack cannot survive host or disk loss without external recovery. Restart policies and health checks do not provide HA.

## Vulnerability reporting

No public security inbox is configured for this generated project. If you adopt it in an organization, use that organization's private security channel and establish an owner before publication. Avoid public issues containing exploit details or real secrets. Provide affected versions, minimal reproduction, impact and suggested mitigation with synthetic data. Disable affected public access, preserve necessary evidence safely and rotate credentials promptly if compromise is suspected.
