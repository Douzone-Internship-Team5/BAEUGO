from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_data_wrapper():
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"data": {"status": "ok", "database": None}}


def test_unknown_path_returns_error_format():
    response = client.get("/api/v1/unknown")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
