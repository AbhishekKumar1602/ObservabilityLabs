from uuid import uuid4

from prometheus_client.parser import text_string_to_metric_families


async def test_metrics(client):
    response = await client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    names = {f.name for f in text_string_to_metric_families(response.text)}
    assert {"application_dependency_up", "application_cache_hits", "application_exceptions"} <= names


async def test_bounded_route_labels(client):
    for _ in range(3):
        await client.get(f"/api/v1/items/{uuid4()}")
        await client.get(f"/random/{uuid4()}")
    families = list(text_string_to_metric_families((await client.get("/metrics")).text))
    samples = [s for f in families for s in f.samples if s.name == "application_http_requests_total"]
    assert {s.labels["route"] for s in samples} == {"/api/v1/items/{item_id}", "__unmatched__"}
    assert all(s.value == 3 for s in samples)
    assert all(set(s.labels) == {"method", "route", "status_code"} for s in samples)
    assert [s.value for f in families for s in f.samples if s.name == "application_http_requests_in_progress"] == [0.0]


async def test_metrics_disabled(client, app):
    app.state.settings.metrics_enabled = False
    assert (await client.get("/metrics")).status_code == 404
