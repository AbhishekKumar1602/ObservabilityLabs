from unittest.mock import AsyncMock


async def test_liveness_is_dependency_independent(client, app, monkeypatch):
    monkeypatch.setattr(app.state.database, "check", AsyncMock(return_value=False))
    app.state.cache.client.available = False
    response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


async def test_readiness(client):
    response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "dependencies": {"postgres": "up", "redis": "up"}}


async def test_redis_degradation(client, app):
    app.state.cache.client.available = False
    response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["dependencies"]["redis"] == "down"


async def test_postgres_failure(client, app, monkeypatch):
    monkeypatch.setattr(app.state.database, "check", AsyncMock(return_value=False))
    response = await client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json()["dependencies"]["postgres"] == "down"
