from __future__ import annotations


def test_health_endpoint(client):
    response = client.get("/s/health/")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
