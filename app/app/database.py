import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import perf_counter

from fastapi import Request
from sqlalchemy import URL, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from app.metrics import Metrics
from app.models import Item

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, settings: Settings, metrics: Metrics, url: str | URL | None = None) -> None:
        self.settings, self.metrics = settings, metrics
        target = url if url is not None else settings.database_url
        options: dict = {"pool_pre_ping": True, "hide_parameters": True}
        if not str(target).startswith("sqlite"):
            options.update(
                pool_size=settings.db_pool_size,
                max_overflow=settings.db_max_overflow,
                pool_timeout=settings.db_timeout_seconds,
                pool_recycle=1800,
                connect_args={
                    "timeout": settings.db_timeout_seconds,
                    "command_timeout": settings.db_timeout_seconds,
                    "server_settings": {
                        "application_name": settings.service_name,
                        "statement_timeout": str(int(settings.db_timeout_seconds * 1000)),
                    },
                },
            )
        self.engine = create_async_engine(target, **options)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, autoflush=False)

    @asynccontextmanager
    async def operation(self, name: str) -> AsyncIterator[None]:
        started = perf_counter()
        try:
            yield
            self.metrics.dependency_up.labels("postgres").set(1)
        except (SQLAlchemyError, TimeoutError):
            self.metrics.dependency_up.labels("postgres").set(0)
            raise
        finally:
            self.metrics.db_duration.labels(name).observe(perf_counter() - started)

    async def check(self) -> bool:
        try:
            async with asyncio.timeout(self.settings.db_timeout_seconds + 0.5):
                async with self.engine.connect() as connection:
                    await connection.execute(text("SELECT 1"))
                    await connection.execute(select(Item.id).limit(0))
            healthy = True
        except (SQLAlchemyError, TimeoutError, OSError):
            healthy = False
        self.metrics.dependency_up.labels("postgres").set(int(healthy))
        return healthy

    async def startup_check(self) -> None:
        for attempt in range(self.settings.startup_attempts):
            if await self.check():
                return
            logger.warning("postgres_startup_probe_failed")
            if attempt + 1 < self.settings.startup_attempts:
                await asyncio.sleep(min(2**attempt, 3))
        logger.error("postgres_unavailable_starting_not_ready")

    async def close(self) -> None:
        await self.engine.dispose()


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.database.sessions() as session:
        try:
            yield session
        except BaseException:
            await session.rollback()
            raise


def get_database(request: Request) -> Database:
    return request.app.state.database
