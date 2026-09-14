import asyncio
import logging
import socket
from uuid import uuid4

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.metrics import NoOpMeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

from app.cache import Cache
from app.config import Settings
from app.database import Database

logger = logging.getLogger(__name__)


class Telemetry:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.provider: TracerProvider | None = None
        self.profile_started = False
        self.sql_instrumentor: SQLAlchemyInstrumentor | None = None
        if settings.otel_enabled:
            self.provider = TracerProvider(
                resource=Resource.create(
                    {
                        "service.name": settings.service_name,
                        "service.version": settings.app_version,
                        "deployment.environment.name": settings.environment,
                        "service.instance.id": f"{socket.gethostname()}-{uuid4()}",
                    }
                ),
                sampler=ParentBased(TraceIdRatioBased(settings.trace_sample_ratio)),
            )
            self.provider.add_span_processor(
                BatchSpanProcessor(
                    OTLPSpanExporter(
                        endpoint=settings.otel_exporter_otlp_endpoint,
                        insecure=settings.otel_exporter_otlp_endpoint.startswith("http://"),
                        timeout=3,
                    ),
                    max_queue_size=2048,
                    max_export_batch_size=256,
                    schedule_delay_millis=1000,
                )
            )
        self.tracer = (
            self.provider.get_tracer("items.application", settings.app_version)
            if self.provider
            else trace.NoOpTracerProvider().get_tracer("items.application")
        )

    def instrument(self, app: FastAPI, database: Database, cache: Cache) -> None:
        if self.provider is None:
            return
        meters = NoOpMeterProvider()
        FastAPIInstrumentor.instrument_app(
            app,
            tracer_provider=self.provider,
            meter_provider=meters,
            excluded_urls=r".*/health/(live|ready)$,.*/metrics$",
            exclude_spans=["receive", "send"],
        )
        self.sql_instrumentor = SQLAlchemyInstrumentor()
        self.sql_instrumentor.instrument(
            engine=database.engine.sync_engine,
            tracer_provider=self.provider,
            meter_provider=meters,
            enable_commenter=False,
        )
        RedisInstrumentor.instrument_client(cache.client, tracer_provider=self.provider)

    async def start_profiles(self) -> None:
        if not self.settings.pyroscope_enabled:
            return
        try:
            import pyroscope

            await asyncio.to_thread(
                pyroscope.configure,
                application_name=self.settings.service_name,
                server_address=self.settings.pyroscope_server_address,
                sample_rate=self.settings.pyroscope_sample_rate,
                oncpu=True,
                gil_only=True,
                enable_logging=False,
                tags={"environment": self.settings.environment},
            )
            self.profile_started = True
            logger.info("profiling_started")
        except Exception:
            logger.error("profiling_initialization_failed", exc_info=True)

    async def close(self, app: FastAPI, cache: Cache) -> None:
        if self.profile_started:
            try:
                import pyroscope

                await asyncio.to_thread(pyroscope.shutdown)
            except Exception:
                logger.error("profiling_shutdown_failed", exc_info=True)
        if self.provider is not None:
            FastAPIInstrumentor.uninstrument_app(app)
            RedisInstrumentor.uninstrument_client(cache.client)
            if self.sql_instrumentor is not None:
                self.sql_instrumentor.uninstrument()
            await asyncio.to_thread(self.provider.shutdown)
