from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_root_endpoint():
    response = client.get("/")

    assert response.status_code == 200
    assert response.json()["application"] == "OpsPulse API"


def test_liveness_endpoint():
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json()["status"] == "alive"


def test_readiness_endpoint():
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_metrics_endpoint():
    client.get("/")

    response = client.get("/metrics")

    assert response.status_code == 200
    assert "opspulse_http_requests_total" in response.text
    assert "opspulse_http_request_duration_seconds" in response.text
