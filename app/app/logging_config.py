"""JSON stdout; no request bodies/headers or raw exception messages."""

import json
import logging
import sys
import traceback
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

from opentelemetry import trace

from app.config import Settings

request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)


class JsonFormatter(logging.Formatter):
    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings

    def format(self, record: logging.LogRecord) -> str:
        event: dict[str, object] = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "service": self.settings.service_name,
            "environment": self.settings.environment,
            "message": record.getMessage(),
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
