"""Pure ASGI context and metrics; no BaseHTTPMiddleware context propagation trap."""

import logging
import re
from time import perf_counter
from uuid import uuid4

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.logging_config import request_id_context
from app.metrics import Metrics

logger = logging.getLogger(__name__)
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "TRACE", "CONNECT"}
EXCLUDED = {"/health/live", "/health/ready", "/metrics"}


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, metrics: Metrics, header: str) -> None:
        self.app, self.metrics, self.header = app, metrics, header

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        values = Headers(scope=scope).getlist(self.header)
        incoming = values[0] if len(values) == 1 else ""
        request_id = incoming if SAFE_ID.fullmatch(incoming) else str(uuid4())
        token = request_id_context.set(request_id)
        scope.setdefault("state", {})["request_id"] = request_id
        method = scope["method"] if scope["method"] in METHODS else "OTHER"
        track = scope["path"] not in EXCLUDED
        status, started, start = 500, False, perf_counter()
        if track:
            self.metrics.in_progress.inc()

        async def send_with_id(message: Message) -> None:
            nonlocal status, started
            if message["type"] == "http.response.start":
                status, started = message["status"], True
                MutableHeaders(scope=message)[self.header] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            self.metrics.exceptions.labels("unexpected").inc()
            logger.error("unhandled_request_exception", exc_info=True)
            trace.get_current_span().set_status(Status(StatusCode.ERROR, "internal_error"))
            if started:
                raise
            response = JSONResponse(
                {
                    "error": {
                        "code": "internal_error",
                        "message": "An internal error occurred",
                        "request_id": request_id,
                    }
                },
                status_code=500,
            )
            await response(scope, receive, send_with_id)
        finally:
            duration = perf_counter() - start
            route = getattr(scope.get("route"), "path", "__unmatched__")
            if track:
                self.metrics.in_progress.dec()
                self.metrics.requests.labels(method, route, str(status)).inc()
                self.metrics.duration.labels(method, route).observe(duration)
                logger.info(
                    "request_completed",
                    extra={
                        "http.method": method,
                        "http.route": route,
                        "http.status_code": status,
                        "duration_ms": round(duration * 1000, 3),
                    },
                )
            request_id_context.reset(token)
