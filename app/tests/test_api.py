from time import monotonic
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from app.database import get_session

PAYLOAD = {"name": "Notebook", "description": "Synthetic data", "price": "12.50"}


async def test_crud(client):
    created = await client.post("/api/v1/items", json=PAYLOAD)
    assert created.status_code == 201
    item = created.json()
    path = f"/api/v1/items/{item['id']}"
    assert created.headers["location"] == path
    assert item["created_at"] and item["updated_at"]
    assert item["price"] == "12.50"
    page = (await client.get("/api/v1/items?limit=1")).json()
    assert page["total"] == 1 and page["items"][0]["id"] == item["id"]
    assert (await client.get(path)).json() == item
    assert (await client.put(path, json={**PAYLOAD, "name": "Updated"})).status_code == 200
    assert (await client.get(path)).json()["name"] == "Updated"
    response = await client.delete(path)
    assert response.status_code == 204 and not response.content
    assert (await client.get(path)).status_code == 404
    assert (await client.get("/api/v1/items")).json()["total"] == 0


@pytest.mark.parametrize("method", ["get", "put", "delete"])
async def test_not_found(client, method):
    options = {"json": PAYLOAD} if method == "put" else {}
    response = await getattr(client, method)(f"/api/v1/items/{uuid4()}", **options)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "", "price": "1"},
        {"name": "   ", "price": "1"},
        {"name": "x", "price": "-1"},
        {"name": "x", "price": "1.234"},
        {"name": "x", "price": "NaN"},
        {"name": "x", "price": "1000001"},
        {"name": "x", "price": "1", "password": "never-echo-this-secret"},
    ],
)
async def test_validation(client, payload):
    response = await client.post("/api/v1/items", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert "never-echo-this-secret" not in response.text


async def test_invalid_id_and_pagination(client):
    for path in ("/api/v1/items/invalid", "/api/v1/items?limit=101", "/api/v1/items?offset=-1"):
        assert (await client.get(path)).status_code == 422


async def test_request_ids(client):
    assert (await client.get("/health/live", headers={"X-Request-ID": "review_123"})).headers[
        "x-request-id"
    ] == "review_123"
    for headers in (
        {},
        {"X-Request-ID": "has spaces"},
        {"X-Request-ID": "x" * 65},
        [("X-Request-ID", "a"), ("X-Request-ID", "b")],
    ):
        assert UUID((await client.get("/health/live", headers=headers)).headers["x-request-id"])


async def test_cache_lifecycle(client, app):
    item = (await client.post("/api/v1/items", json=PAYLOAD)).json()
    path = f"/api/v1/items/{item['id']}"
    cache = app.state.cache
    key = cache.key(UUID(item["id"]))
    await client.get(path)
    assert key in cache.client.data
    await client.get(path)
    assert app.state.metrics.cache_hits._value.get() == 1
    await client.put(path, json={**PAYLOAD, "name": "Fresh"})
    assert key not in cache.client.data
    assert (await client.get(path)).json()["name"] == "Fresh"
    raw, _ = cache.client.data[key]
    cache.client.data[key] = (raw, monotonic() - 1)
    before = app.state.metrics.cache_misses._value.get()
    await client.get(path)
    assert app.state.metrics.cache_misses._value.get() == before + 1
    await client.delete(path)
    assert key not in cache.client.data


async def test_corrupt_cache(client, app):
    item = (await client.post("/api/v1/items", json=PAYLOAD)).json()
    key = app.state.cache.key(UUID(item["id"]))
    await app.state.cache.client.set(key, '{"invalid":true}', ex=30)
    assert (await client.get(f"/api/v1/items/{item['id']}")).status_code == 200
    assert app.state.metrics.redis_errors.labels("decode")._value.get() == 1


async def test_redis_outage_and_stale_recovery(client, app):
    item = (await client.post("/api/v1/items", json=PAYLOAD)).json()
    path = f"/api/v1/items/{item['id']}"
    await client.get(path)
    app.state.cache.client.available = False
    assert (await client.get(path)).status_code == 200
    assert (await client.put(path, json={**PAYLOAD, "name": "Written during outage"})).status_code == 200
    app.state.cache.client.available = True
    assert (await client.get(path)).json()["name"] == "Written during outage"
    app.state.cache.client.available = False
    assert (await client.delete(path)).status_code == 204
    assert (await client.post("/api/v1/items", json=PAYLOAD)).status_code == 201
    assert app.state.metrics.redis_errors.labels("delete")._value.get() >= 1


async def test_database_error_sanitized(client, app):
    async def broken_session():
        raise OperationalError("SELECT private_data", {}, Exception("password=hidden-password"))
        yield

    app.dependency_overrides[get_session] = broken_session
    response = await client.get("/api/v1/items")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_unavailable"
    assert "private_data" not in response.text and "hidden-password" not in response.text


async def test_failed_commit_rolls_back(client, app):
    cls = app.state.database.sessions.class_.sync_session_class

    def fail_once(session):
        raise OperationalError("test", {}, Exception("test commit failure"))

    event.listen(cls, "before_commit", fail_once, once=True)
    try:
        response = await client.post("/api/v1/items", json=PAYLOAD)
        assert response.status_code == 503
        assert (await client.get("/api/v1/items")).json()["total"] == 0
        assert not app.state.cache.client.data
    finally:
        event.remove(cls, "before_commit", fail_once)


async def test_unexpected_error(client, app):
    @app.get("/test-unexpected")
    async def unexpected():
        raise RuntimeError("private-details")

    response = await client.get("/test-unexpected")
    assert response.status_code == 500
    assert "private-details" not in response.text
    assert response.json()["error"]["code"] == "internal_error"
    assert response.headers["x-request-id"]


async def test_demo_guard(client, app):
    assert (await client.get("/api/v1/demo/work?iterations=1000")).status_code == 200
    assert (await client.get("/api/v1/demo/work?iterations=1000001")).status_code == 422
    app.state.settings.demo_enabled = False
    assert (await client.get("/api/v1/demo/work")).status_code == 404
