from fastapi.testclient import TestClient

from app.main import app
from app.services import visitor_counter


def _client_with_storage(monkeypatch, tmp_path):
    monkeypatch.setattr(visitor_counter, "VISITS_STORAGE_PATH", tmp_path / "visitor_counter.json")
    return TestClient(app)


def test_counter_response(monkeypatch, tmp_path):
    client = _client_with_storage(monkeypatch, tmp_path)

    response = client.get("/api/visits")

    assert response.status_code == 200
    payload = response.json()
    assert payload["today"] == 0
    assert payload["total"] == 0
    assert isinstance(payload["updated_at"], str)


def test_increment_once_per_session_cookie(monkeypatch, tmp_path):
    client = _client_with_storage(monkeypatch, tmp_path)

    first = client.get("/api/visits?increment=true")
    second = client.get("/api/visits?increment=true")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["today"] == 1
    assert first.json()["total"] == 1
    assert second.json()["today"] == 1
    assert second.json()["total"] == 1


def test_counter_recreates_missing_storage_file(monkeypatch, tmp_path):
    client = _client_with_storage(monkeypatch, tmp_path)
    storage_file = tmp_path / "visitor_counter.json"
    if storage_file.exists():
        storage_file.unlink()

    response = client.get("/api/visits")

    assert response.status_code == 200
    assert response.json()["today"] == 0
    assert response.json()["total"] == 0
