"""One registry per app; direct Prometheus scraping, one Uvicorn worker."""

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    GCCollector,
    Histogram,
    PlatformCollector,
    ProcessCollector,
)


class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        for collector in (ProcessCollector, PlatformCollector, GCCollector):
            collector(registry=self.registry)
        self.requests = Counter(
            "application_http_requests_total",
            "Completed requests",
            ["method", "route", "status_code"],
            registry=self.registry,
        )
        self.duration = Histogram(
            "application_http_request_duration_seconds",
            "Request duration including response",
            ["method", "route"],
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
            registry=self.registry,
        )
        self.in_progress = Gauge("application_http_requests_in_progress", "Active requests", registry=self.registry)
        self.exceptions = Counter(
            "application_exceptions_total", "Handled server exceptions", ["kind"], registry=self.registry
        )
        self.db_duration = Histogram(
            "application_postgres_operation_duration_seconds",
            "DB operation including pool wait and transaction",
            ["operation"],
            registry=self.registry,
        )
        self.cache_hits = Counter("application_cache_hits_total", "Validated cache hits", registry=self.registry)
        self.cache_misses = Counter(
            "application_cache_misses_total", "Cache misses including bypass", registry=self.registry
        )
        self.redis_errors = Counter(
            "application_redis_errors_total", "Cache failures", ["operation"], registry=self.registry
        )
        self.dependency_up = Gauge(
            "application_dependency_up",
            "Application-observed dependency status, not exporter telemetry",
            ["dependency"],
            registry=self.registry,
        )
        for name in ("postgres", "redis"):
            self.dependency_up.labels(name).set(0)
        for name in ("database", "unexpected"):
            self.exceptions.labels(name).inc(0)
        for name in ("get", "set", "delete", "ping", "decode"):
            self.redis_errors.labels(name).inc(0)
