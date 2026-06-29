from fastapi.testclient import TestClient

from app import main


def test_health_ok(monkeypatch) -> None:
    monkeypatch.setattr(main, "check_db", lambda: True)

    response = TestClient(main.app).get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok"}


def test_health_degraded(monkeypatch) -> None:
    monkeypatch.setattr(main, "check_db", lambda: False)

    response = TestClient(main.app).get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "degraded", "db": "error"}
