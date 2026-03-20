import pytest


@pytest.mark.anyio
async def test_healthz_returns_ok(client):
    response = await client.get("/healthz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "mode" in data
    assert data["version"] == "0.1.0"
