import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse, Response

from app.api import router
from app.cache import Cache
from app.config import Settings
from app.database import Database
from app.logging_config import configure_logging
from app.metrics import Metrics
from app.middleware import RequestContextMiddleware
from app.schemas import DependencyStatus, Readiness
from app.telemetry import Telemetry

logger = logging.getLogger(__name__)


def error_response(
    request: Request,
    status: int,
    code: str,
    message: str,
    details: list[dict] | None = None,
    headers: dict | None = None,
) -> JSONResponse:
    error: dict = {"code": code, "message": message, "request_id": getattr(request.state, "request_id", None)}
    if details is not None:
        error["details"] = details
    return JSONResponse({"error": error}, status_code=status, headers=headers)


async def probe_dependencies(database: Database, cache: Cache, interval: float) -> None:
    previous: tuple[bool, bool] | None = None
    while True:
        postgres, redis = await asyncio.gather(database.check(), cache.check())
        current = (postgres, redis)
        if current != previous:
            logger.log(
                logging.INFO if all(current) else logging.WARNING,
                "dependencies_ready" if all(current) else "dependencies_degraded",
            )
            previous = current
        await asyncio.sleep(interval)


def create_app(
    settings: Settings | None = None, database: Database | None = None, cache: Cache | None = None
) -> FastAPI:
    settings = settings or Settings()
    configure_logging(settings)
    metrics = database.metrics if database is not None else Metrics()
    database = database or Database(settings, metrics)
    cache = cache or Cache(settings, metrics)
    telemetry = Telemetry(settings)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        monitor: asyncio.Task | None = None
        try:
            await database.startup_check()
            await cache.check()
            await telemetry.start_profiles()
            monitor = asyncio.create_task(
                probe_dependencies(database, cache, settings.dependency_probe_interval_seconds)
            )
            logger.info("application_started")
            yield
        finally:
            if monitor is not None:
                monitor.cancel()
                with suppress(asyncio.CancelledError):
                    await monitor
            await telemetry.close(application, cache)
            await cache.close()
            await database.close()
            logger.info("application_stopped")

    app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan, debug=False)
    app.state.settings, app.state.database, app.state.cache = settings, database, cache
    app.state.metrics, app.state.tracer, app.state.demo_lock = metrics, telemetry.tracer, asyncio.Lock()
    app.include_router(router)

    @app.get("/health/live", tags=["health"])
    async def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health/ready", response_model=Readiness, tags=["health"], responses={503: {"model": Readiness}})
    async def ready() -> JSONResponse:
        postgres, redis = await asyncio.gather(database.check(), cache.check())
        body = Readiness(
            status="not_ready" if not postgres else ("ready" if redis else "degraded"),
            dependencies=DependencyStatus(postgres="up" if postgres else "down", redis="up" if redis else "down"),
        )
        return JSONResponse(body.model_dump(), status_code=200 if postgres else 503)

    @app.get("/metrics", include_in_schema=False)
    async def prometheus_metrics() -> Response:
        if not settings.metrics_enabled:
            raise HTTPException(404, "Not found")
        return Response(generate_latest(metrics.registry), headers={"Content-Type": CONTENT_TYPE_LATEST})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exception: RequestValidationError) -> JSONResponse:
        details = [
            {"location": list(e["loc"]), "type": e["type"], "message": e["msg"]} for e in exception.errors()[:10]
        ]
        return error_response(request, 422, "validation_error", "Request validation failed", details)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exception: HTTPException) -> JSONResponse:
        codes = {404: "not_found", 405: "method_not_allowed", 429: "too_many_requests"}
        return error_response(
            request,
            exception.status_code,
            codes.get(exception.status_code, "http_error"),
            str(exception.detail),
            headers=exception.headers,
        )

    async def database_error(request: Request, exception: Exception) -> JSONResponse:
        metrics.exceptions.labels("database").inc()
        metrics.dependency_up.labels("postgres").set(0)
        logger.error("database_request_failed", exc_info=(type(exception), exception, exception.__traceback__))
        return error_response(request, 503, "database_unavailable", "Required persistence is temporarily unavailable")

    app.add_exception_handler(SQLAlchemyError, database_error)
    app.add_exception_handler(TimeoutError, database_error)
    app.add_middleware(RequestContextMiddleware, metrics=metrics, header=settings.request_id_header)
    telemetry.instrument(app, database, cache)
    return app
