import asyncio
import os
from pathlib import Path
from time import monotonic

import pytest_asyncio
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from redis.exceptions import ConnectionError
from sqlalchemy import delete
from sqlalchemy.engine import make_url

from app.cache import Cache
from app.config import Settings
from app.database import Database
from app.main import create_app
from app.metrics import Metrics
from app.models import Item


class FakeRedis:
    def __init__(self) -> None:
        self.available = True
        self.data: dict[str, tuple[str, float]] = {}

    def check_connection(self) -> None:
        if not self.available:
            raise ConnectionError("test cache unavailable")

    async def ping(self) -> bool:
        self.check_connection()
        return True

    async def get(self, key: str) -> str | None:
        self.check_connection()
        value = self.data.get(key)
        if value is None:
            return None
        if value[1] <= monotonic():
            self.data.pop(key)
            return None
        return value[0]

    async def set(self, key: str, value: str, ex: int) -> bool:
        self.check_connection()
        self.data[key] = (value, monotonic() + ex)
        return True

    async def delete(self, key: str) -> int:
        self.check_connection()
        return int(self.data.pop(key, None) is not None)

    async def aclose(self) -> None:
        self.data.clear()


@pytest_asyncio.fixture
async def app(tmp_path):
    settings = Settings(
        _env_file=None,
        environment="test",
        postgres_password="isolated-test-password",
        redis_password="isolated-test-password",
        otel_enabled=False,
        pyroscope_enabled=False,
        demo_enabled=True,
        startup_attempts=1,
    )
    target = os.getenv("TEST_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    if target.startswith("postgresql") and not (make_url(target).database or "").endswith("_test"):
        raise ValueError("TEST_DATABASE_URL must use a disposable database ending in _test")
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.attributes["database_url"] = target
    await asyncio.to_thread(command.upgrade, config, "head")
    metrics = Metrics()
    db = Database(settings, metrics, url=target)
    async with db.sessions.begin() as session:
        await session.execute(delete(Item))
    cache = Cache(settings, metrics, client=FakeRedis())
    application = create_app(settings, db, cache)
    async with application.router.lifespan_context(application):
        yield application


@pytest_asyncio.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
