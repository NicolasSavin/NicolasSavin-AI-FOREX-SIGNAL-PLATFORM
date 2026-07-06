from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.services.tv_source_manager import TvSourceManager


def test_tv_sources_api_contract():
    response = TestClient(app).get("/api/tv/sources")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 6
    assert set(payload[0]) == {"id", "name", "provider", "enabled", "priority", "categories", "last_import", "videos_count"}
    assert payload[0]["id"] == "gerchik"
    assert payload[0]["provider"] == "youtube"


def test_tv_source_manager_prepares_jobs_without_importing():
    manager = TvSourceManager(Path("data/tv_sources.json"), Path("data/tv_videos.json"))

    jobs = manager.prepare_import_jobs()

    assert len(jobs) == 6
    assert jobs[0]["source_id"] == "gerchik"
    assert jobs[0]["provider"] == "youtube"
    assert jobs[0]["status"] == "queued"
