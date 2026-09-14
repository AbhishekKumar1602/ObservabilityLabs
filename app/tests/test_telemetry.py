import io
import json
import logging

from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.metrics import NoOpMeterProvider
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import ALWAYS_ON

from app.logging_config import JsonFormatter


async def test_request_logs_correlate_to_real_span(app, client):
    exporter = InMemorySpanExporter()
    provider = TracerProvider(sampler=ALWAYS_ON)
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(JsonFormatter(app.state.settings))
    logger = logging.getLogger("app.middleware")
    logger.addHandler(handler)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    FastAPIInstrumentor.instrument_app(
        app, tracer_provider=provider, meter_provider=NoOpMeterProvider(), exclude_spans=["receive", "send"]
    )
    try:
        response = await client.get("/api/v1/items", headers={"X-Request-ID": "trace-contract"})
        assert response.status_code == 200
        records = [json.loads(line) for line in output.getvalue().splitlines() if line.startswith("{")]
        record = next(x for x in records if x.get("message") == "request_completed")
        assert record["request_id"] == "trace-contract"
        trace_ids = {f"{span.context.trace_id:032x}" for span in exporter.get_finished_spans()}
        assert record["trace_id"] in trace_ids
        assert len(record["span_id"]) == 16
    finally:
        FastAPIInstrumentor.uninstrument_app(app)
        provider.shutdown()
        logger.removeHandler(handler)
