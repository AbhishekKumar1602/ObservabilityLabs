"""Best-effort, TTL-bounded cache-aside. PostgreSQL remains authoritative."""

import logging
from time import monotonic
from uuid import UUID

from fastapi import Request
from pydantic import ValidationError
from redis.asyncio import ConnectionPool, Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import RedisError

from app.config import Settings
from app.metrics import Metrics
from app.schemas import ItemRead

logger = logging.getLogger(__name__)


class Cache:
    def __init__(self, settings: Settings, metrics: Metrics, client: Redis | None = None) -> None:
        self.settings, self.metrics = settings, metrics
        self._bypass_until = 0.0
        self.pool: ConnectionPool | None = None
        if client is None:
            self.pool = ConnectionPool(
                host=settings.redis_host,
                port=settings.redis_port,
                db=settings.redis_db,
                password=settings.redis_password.get_secret_value(),
                decode_responses=True,
                max_connections=settings.redis_pool_size,
                socket_timeout=settings.redis_timeout_seconds,
                socket_connect_timeout=settings.redis_timeout_seconds,
                retry=Retry(NoBackoff(), 0),
                retry_on_timeout=False,
                health_check_interval=30,
            )
            client = Redis(connection_pool=self.pool)
        self.client = client

    def key(self, item_id: UUID) -> str:
        return f"{self.settings.service_name}:{self.settings.environment}:items:v1:{item_id}"

    def _failure(self, operation: str, error: Exception) -> None:
        self.metrics.redis_errors.labels(operation).inc()
        self.metrics.dependency_up.labels("redis").set(0)
        logger.warning("cache_operation_failed", extra={"operation": operation, "error_type": type(error).__name__})

    async def get_item(self, item_id: UUID) -> ItemRead | None:
        if monotonic() < self._bypass_until:
            self.metrics.cache_misses.inc()
            return None
        try:
            raw = await self.client.get(self.key(item_id))
            if raw is not None:
                try:
                    item = ItemRead.model_validate_json(raw)
                    if item.id != item_id:
                        raise ValueError("cache identity mismatch")
                except (ValidationError, ValueError) as error:
                    self._failure("decode", error)
                    await self.invalidate(item_id)
                else:
                    self.metrics.cache_hits.inc()
                    return item
        except (RedisError, OSError, TimeoutError) as error:
            self._failure("get", error)
        self.metrics.cache_misses.inc()
        return None

    async def set_item(self, item: ItemRead) -> None:
        if monotonic() < self._bypass_until:
            return
        try:
            await self.client.set(self.key(item.id), item.model_dump_json(), ex=self.settings.cache_ttl_seconds)
        except (RedisError, OSError, TimeoutError) as error:
            self._failure("set", error)

    async def invalidate(self, item_id: UUID) -> None:
        try:
            await self.client.delete(self.key(item_id))
        except (RedisError, OSError, TimeoutError) as error:
            self._failure("delete", error)
            # Protect this single worker from surviving stale entries after recovery.
            self._bypass_until = monotonic() + self.settings.cache_ttl_seconds

    async def check(self) -> bool:
        try:
            healthy = bool(await self.client.ping())
        except (RedisError, OSError, TimeoutError) as error:
            self._failure("ping", error)
            return False
        self.metrics.dependency_up.labels("redis").set(int(healthy))
        return healthy

    async def close(self) -> None:
        await self.client.aclose()
        if self.pool is not None:
            await self.pool.aclose()


def get_cache(request: Request) -> Cache:
    return request.app.state.cache
