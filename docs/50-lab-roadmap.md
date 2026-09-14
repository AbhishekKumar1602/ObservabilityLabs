# 50 Progressive Hands-On Labs

This roadmap is the curriculum contract for the repository. Each lab adds one operational or observability idea, preserves evidence in `lab-notes/`, and builds on prior behavior. The learning sequence prioritizes understanding the workload and the events it produces first, then metrics, visualization, logs, traces, profiling, alerting, reliability, and finally multi-signal incident investigation.

The core platform is intentionally limited to FastAPI, PostgreSQL, Redis, Docker Compose, Prometheus, Node Exporter, PostgreSQL Exporter, Redis Exporter, Grafana, Alertmanager, Loki, Tempo, Pyroscope, and the minimum OpenTelemetry SDK/Collector functionality required for log transport, distributed tracing, sampling, event context, and cross-signal correlation.

## Phase 1: Know the Workload and Events Before Monitoring It

| **Lab** | **Title**                                                     | **Primary Outcome**                                                                                                                                                                                                                                                                                      |
| ------- | ------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
|   1     | Follow One FastAPI Request End-to-End                         | Trace a request through FastAPI routing, application logic, Redis/PostgreSQL dependencies, and the response path before introducing observability tooling.                                                                                                                                               |
|   2     | PostgreSQL Persistence and Transaction Boundaries             | Demonstrate durable state, commit/rollback behavior, SQLAlchemy session lifecycle, connection reuse, and database failure semantics.                                                                                                                                                                     |
|   3     | Redis Cache-Aside and Graceful Degradation                    | Compare cache hits, misses, TTL expiration, PostgreSQL fallback, stale-data behavior, and optional dependency failure.                                                                                                                                                                                   |
|   4     | Liveness, Readiness, and Dependency Health                    | Distinguish process liveness from service readiness and model required versus optional dependencies correctly.                                                                                                                                                                                           |
|   5     | Docker Compose Networking, Storage, and Restart Behavior      | Explain service DNS, published versus internal ports, persistent volumes, container recreation, restart behavior, and operational changes that later affect telemetry.                                                                                                                                   |
|   6     | Events, Structured Logging, Request IDs, and Evidence Capture | Distinguish real events, event records, log records, log lines, metric observations, span events, change events, audit events, Kubernetes Events, and profile samples; produce safe JSON logs, propagate request IDs across application work, and establish the standard `lab-notes/` evidence workflow. |

## Phase 2: Prometheus and Metrics Deep Dive

| **Lab** | **Title**                                        | **Primary Outcome**                                                                                                                                                                            |
| ------- | ------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
|   7     | Raw OpenMetrics Before Prometheus                | Read raw metric exposition and identify metric families, samples, HELP/TYPE metadata, counters, gauges, and histograms while distinguishing metric observations from individual event records. |
|   8     | Instrument RED Metrics                           | Implement useful request rate, error, and duration metrics for FastAPI with bounded route, method, and status dimensions and understand how repeated request events modify metric state.       |
|   9     | Metric Design, Business Metrics, and Cardinality | Design application, dependency, and business metrics while demonstrating why arbitrary IDs, event IDs, request IDs, and other unbounded values are unsafe labels.                              |
|  10     | Prometheus Discovery and Scrape Lifecycle        | Start Prometheus, inspect targets, and understand discovery, scrape intervals, `up`, failed scrapes, target health, and stale series.                                                          |
|  11     | PromQL Selectors, Matchers, and Aggregation      | Query series safely with selectors and matchers and aggregate while preserving the intended dimensions and scope.                                                                              |
|  12     | Counter Math: `rate`, `irate`, and `increase`    | Interpret counters across resets and choose correct functions and time windows for throughput and event-count calculations.                                                                    |
|  13     | Histograms, Buckets, and Quantiles               | Calculate p50, p95, and p99 latency correctly and understand cumulative buckets, interpolation, and low-volume uncertainty.                                                                    |
|  14     | Host Monitoring with Node Exporter and USE       | Diagnose CPU, memory, disk, filesystem, load, network, utilization, saturation, and resource errors on the host.                                                                               |
|  15     | PostgreSQL and Redis Exporters                   | Compare application-level dependency observations with PostgreSQL and Redis server-level metrics.                                                                                              |
|  16     | Recording Rules and Query Cost                   | Precompute stable service-level queries, inspect generated series, and understand the operational value of recording rules.                                                                    |
|  17     | Relabeling and Ingestion Guardrails              | Normalize targets, drop unnecessary telemetry, restrict unsafe dimensions, and understand target versus metric relabeling.                                                                     |
|  18     | Prometheus TSDB, Retention, and Capacity         | Inspect WAL and blocks and estimate series count, ingestion rate, disk growth, retention cost, and query pressure.                                                                             |

## Phase 3: Grafana, Alerting, and Reliability

| **Lab** | **Title**                                                | **Primary Outcome**                                                                                                                        |
| ------- | -------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
|  19     | Grafana Explore and Data Source Fundamentals             | Query Prometheus through Grafana while distinguishing visualization from the underlying telemetry source.                                  |
|  20     | Build a RED Application Dashboard                        | Build request-rate, error-rate, and latency panels from operational requirements rather than available metrics alone.                      |
|  21     | Build USE and Dependency Dashboards                      | Correlate application behavior with host, PostgreSQL, Redis, resource, and dependency evidence.                                            |
|  22     | Dashboard Variables, UX, Drilldowns, and Provisioning    | Apply controlled variables, units, legends, thresholds, links, reusable panels, and version-controlled dashboard provisioning.             |
|  23     | Prometheus Alert Rule Lifecycle                          | Observe inactive, pending, firing, and resolved states and validate rule expressions and `for` durations experimentally.                   |
|  24     | Alertmanager Routing, Grouping, Inhibition, and Silences | Route by severity or ownership and test grouping, repeat behavior, inhibition, maintenance silences, and notification control.             |
|  25     | Define SLIs, SLOs, and Error Budgets                     | Convert actual user-facing behavior into measurable availability and latency objectives with explicit eligibility and success definitions. |
|  26     | Multi-Window Burn Rates and the SLO Operations Dashboard | Detect fast and slow error-budget consumption and build a dashboard around objectives, budget, traffic, dependencies, and burn.            |

## Phase 4: Logs, Events, and Loki

| **Lab** | **Title**                                         | **Primary Outcome**                                                                                                                                                  |
| ------- | ------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
|  27     | Loki Architecture and Log Ingestion               | Follow structured application event/log records through a minimal OpenTelemetry Collector pipeline into Loki and understand the ingestion path.                      |
|  28     | Loki Labels, Structured Metadata, and Cardinality | Distinguish indexed labels from structured metadata and prevent request IDs, trace IDs, event IDs, URLs, and other high-cardinality values from becoming labels.     |
|  29     | LogQL Selectors, Filters, and Parsing             | Query bounded log streams, filter lines, parse JSON, extract event fields, format output, and understand efficient search scope.                                     |
|  30     | Metrics from Log Events                           | Convert carefully selected log events into rates and aggregations while understanding why log-derived metrics should not replace proper instrumentation.             |
|  31     | Log-Based Alerts                                  | Alert on high-value log events while controlling noise, expensive queries, duplicate alerts, and brittle text matching.                                              |
|  32     | Log Shipping Failure, Retention, and Cost         | Interrupt the shipping path, observe buffering and possible event-record loss, recover safely, and evaluate retention, cardinality, storage, and query implications. |

## Phase 5: OpenTelemetry and Tempo

| **Lab** | **Title**                                                | **Primary Outcome**                                                                                                                                                                  |
| ------- | -------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
|  33     | OpenTelemetry Tracing Fundamentals                       | Understand traces, spans, trace/span IDs, resources, instrumentation scopes, OTLP, and Collector receiver-processor-exporter pipelines.                                              |
|  34     | Automatic FastAPI, SQLAlchemy, and Redis Instrumentation | Compare framework, database, Redis, and HTTP client spans generated through automatic instrumentation.                                                                               |
|  35     | Distributed Context Propagation                          | Introduce a small downstream FastAPI service and follow W3C Trace Context across a real HTTP service boundary.                                                                       |
|  36     | Custom Spans, Events, Attributes, Status, and Redaction  | Add useful business and diagnostic context, distinguish span events from logs and span attributes, and avoid leaking secrets or introducing unnecessary high-cardinality attributes. |
|  37     | Tempo Search and TraceQL                                 | Find slow, failed, and attribute-matched traces and use trace structure to identify critical-path dependencies.                                                                      |
|  38     | Head Sampling and Diagnostic Loss                        | Compare trace sample ratios and quantify storage savings against lost diagnostic evidence, including span events contained in discarded traces.                                      |
|  39     | Tail Sampling in the Collector                           | Preserve errors and high-latency traces while reducing ordinary trace volume using explicit sampling policies.                                                                       |
|  40     | Collector Queues, Retries, Memory, and Backpressure      | Observe exporter failure, buffering, retries, memory pressure, dropped telemetry risk, and recovery behavior.                                                                        |
|  41     | Tempo Span Metrics, Service Graphs, and Exemplars        | Generate RED-style metrics from traces, visualize service relationships, and navigate from metric observations to representative traces.                                             |

## Phase 6: Pyroscope and Continuous Profiling

| **Lab** | **Title**                                     | **Primary Outcome**                                                                                                                                                                       |
| ------- | --------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
|  42     | Pyroscope Continuous Profiling Fundamentals   | Instrument the Python application, read CPU flamegraphs, understand sampled stacks, distinguish profile samples from event records and metric samples, and identify expensive code paths. |
|  43     | CPU Work, Waiting, and Differential Profiling | Compare CPU-heavy and waiting-heavy requests and use before/after profiles to understand what profiling can and cannot explain.                                                           |
|  44     | Trace-to-Profile and Four-Signal Correlation  | Correlate Prometheus metrics, Loki log/event records, Tempo traces and span events, and Pyroscope profiles for one request or incident path.                                              |

## Phase 7: Failure Engineering and Professional Operations

| **Lab** | **Title**                                                        | **Primary Outcome**                                                                                                                                                                                                                          |
| ------- | ---------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
|  45     | Redis Failure Incident                                           | Prove cache fallback, readiness semantics, latency impact, database amplification, telemetry changes, emitted events, and clean Redis recovery.                                                                                              |
|  46     | PostgreSQL Failure Incident                                      | Separate process health from broken data-path behavior and investigate pools, failed queries, transactions, readiness, emitted events, and durable recovery.                                                                                 |
|  47     | Observability Backend Failure                                    | Break Prometheus, Loki, Tempo, Pyroscope, and Collector paths individually and determine application impact, telemetry loss, event-record loss, buffering, and recovery behavior.                                                            |
|  48     | Security and Telemetry Hardening Review                          | Review exposed ports, credentials, sensitive telemetry, audit-relevant records, administrative APIs, container privileges, and unnecessary network exposure.                                                                                 |
|  49     | Configuration Validation, Backup, Restore, Upgrade, and Rollback | Automate configuration validation, protect important state/configuration, restore from backup, rehearse pinned-version upgrade and rollback checkpoints, and preserve useful change evidence.                                                |
|  50     | Capstone Reliability Game Day and Evidence-Based Postmortem      | Diagnose multiple injected failures using metrics, logs/events, traces/span events, profiles, change evidence, and operational context; restore service safely; prove recovery; and produce a professional incident timeline and postmortem. |

## Standard Lab Structure

Every full lab guide should contain:

1. Purpose, scope, and explicit exclusions.
2. Prerequisites and inherited system state.
3. Measurable learning objectives.
4. Current architecture and signal flow.
5. Relevant system events, state changes, or operational changes involved in the lab.
6. A clean starting-state check.
7. Concepts immediately paired with commands and observations.
8. Prediction checkpoints before changes or failures.
9. A controlled experiment with bounded blast radius.
10. Evidence capture from more than one layer whenever appropriate.
11. Correlation with another signal or system layer where meaningful.
12. Recovery and proof of recovery.
13. Troubleshooting paths for expected mistakes.
14. Production implications and tradeoffs.
15. Knowledge checks.
16. A lab notebook template.
17. Observable completion criteria.
18. A transition explaining why the next lab exists.

## Signal Ownership Model

| **Signal**                            | **Primary Tool**         | **Main Question**                                                  |
| ------------------------------------- | ------------------------ | ------------------------------------------------------------------ |
| Events                                | Application / System     | What actually happened or changed?                                 |
| Metrics                               | Prometheus               | Is something wrong, how much, and since when?                      |
| Visualization                         | Grafana                  | How do operational signals relate over time?                       |
| Logs / Event Records                  | Loki                     | What specific recorded events happened?                            |
| Traces                                | Tempo                    | Where did a request spend time or fail?                            |
| Span Events                           | Tempo                    | What noteworthy occurrence happened during a particular span?      |
| Profiles                              | Pyroscope                | Where did the application spend CPU or other sampled resources?    |
| Telemetry transport and trace context | OpenTelemetry            | How does trace/log telemetry move while preserving context?        |
| Alert delivery                        | Alertmanager             | Who should be notified, under what conditions, and how often?      |
| Change Events                         | Application / Operations | What deployment, configuration, or infrastructure change occurred? |
| Audit Events                          | Application / Platform   | Who or what performed a governed operation on which target?        |
| Kubernetes Events                     | Kubernetes API           | What Kubernetes lifecycle or warning occurrence was reported?      |

## OpenTelemetry Coverage Boundary

OpenTelemetry is included only where it materially improves the platform.

Prometheus remains responsible for native application and infrastructure metrics so the curriculum can teach scraping, exporters, PromQL, recording rules, relabeling, cardinality, and TSDB behavior directly.

The OpenTelemetry SDK and Collector are primarily used for:

* distributed tracing and context propagation;
* creating spans and span events;
* sending trace data to Tempo;
* minimal structured log/event transport into Loki;
* head and tail sampling;
* Collector queues, retries, memory protection, and backpressure;
* cross-signal correlation where trace context is required.

Pyroscope remains the profiling backend rather than routing profiling concepts through OpenTelemetry.

Events are treated as occurrences or state changes rather than as an additional telemetry backend. Depending on the event and investigation need, evidence of an event may appear as a log record, metric change, span event, change record, audit record, or correlated profile activity.

Kubernetes Events are introduced conceptually so their place in the event model is understood, but hands-on Kubernetes Event collection is outside this Docker Compose-based repository.

## Professional Coverage Boundary

The 50 labs provide hands-on depth for the repository's actual observability platform:

* FastAPI application behavior;
* PostgreSQL and Redis dependency behavior;
* Docker Compose operations;
* observability event terminology and event-record modeling;
* distinction between events, log records, log lines, metric observations, span events, change events, audit events, Kubernetes Events, and profile samples;
* Prometheus instrumentation, PromQL, exporters, recording rules, relabeling, retention, and capacity;
* Grafana exploration, dashboarding, provisioning, and SLO visualization;
* Alertmanager routing, grouping, inhibition, and silencing;
* Loki ingestion, LogQL, structured metadata, log/event alerts, retention, and failure behavior;
* OpenTelemetry tracing, span events, propagation, sampling, and Collector operations;
* Tempo TraceQL, exemplars, span metrics, and service graphs;
* Pyroscope profiling and trace-to-profile correlation;
* four-signal correlation across metrics, logs, traces, and profiles;
* SLIs, SLOs, error budgets, and multi-window burn-rate alerting;
* telemetry security and exposure review;
* change and audit evidence concepts;
* observability backend failure;
* configuration validation;
* backup, restore, upgrades, and rollback;
* multi-signal incident investigation and event-aware postmortem practice.

Some production observability topics cannot be proven faithfully on one Docker Compose environment.

High availability, multi-region disaster recovery, object-storage-scale backends, multi-tenancy, SSO, enterprise secret management, production TLS architecture, independent synthetic probes, browser real-user monitoring, large-scale distributed profiling, enterprise audit pipelines, production Kubernetes Event collection, and organization-wide telemetry governance require additional infrastructure or multiple failure domains.

The labs identify those boundaries rather than presenting a single-machine environment as production proof.

Use this curriculum as the observability core. Add Kubernetes, Kubernetes Events, cloud-native deployment, cloud audit events, high availability, distributed storage, security identity, large-scale event processing, and organization-specific operational practices only after the underlying observability concepts are understood.