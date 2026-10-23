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


def test_removed_media_endpoints_return_404():
    # M-1 및 M-2 엔드포인트는 강사 브랜치 스코프에서 분리/제거되었으므로 404 NOT_FOUND 반환
    res_m1 = client.get("/api/v1/videos/1/stream")
    assert res_m1.status_code == 404
    assert res_m1.json()["error"]["code"] == "NOT_FOUND"

    res_m2 = client.get("/api/v1/media/courses/1/thumb.jpg")
    assert res_m2.status_code == 404
    assert res_m2.json()["error"]["code"] == "NOT_FOUND"
