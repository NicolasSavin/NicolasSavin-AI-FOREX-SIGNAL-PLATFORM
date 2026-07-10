from __future__ import annotations

import json
from fastapi.testclient import TestClient


def test_ops_page_loads():
    import app.main as main
    response = TestClient(main.app).get("/ops")
    assert response.status_code == 200
    assert "Operations" in response.text


def test_missing_and_invalid_token_reject(monkeypatch):
    import app.main as main
    monkeypatch.setenv("FXPILOT_OPS_TOKEN", "secret-token")
    client = TestClient(main.app)
    assert client.post("/api/ops/cache/clear").status_code == 401
    assert client.post("/api/ops/cache/clear", headers={"X-FXPILOT-OPS-TOKEN": "bad"}).status_code == 403


def test_correct_token_permits_operation_and_token_not_returned(monkeypatch, tmp_path):
    import app.main as main
    token = "secret-token-never-echo"
    monkeypatch.setenv("FXPILOT_OPS_TOKEN", token)
    monkeypatch.setattr(main, "OPS_AUDIT_PATH", tmp_path / "ops_audit.json")
    response = TestClient(main.app).post("/api/ops/cache/clear", headers={"X-FXPILOT-OPS-TOKEN": token})
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert token not in response.text
    assert token not in (tmp_path / "ops_audit.json").read_text(encoding="utf-8")


def test_status_endpoint_exposes_no_secrets(monkeypatch):
    import app.main as main
    monkeypatch.setenv("FXPILOT_OPS_TOKEN", "ops-secret")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-secret")
    response = TestClient(main.app).get("/api/ops/status")
    assert response.status_code == 200
    body = response.text
    assert "ops-secret" not in body
    assert "openrouter-secret" not in body
    assert "api_key_present" in body


def test_reprocess_defaults_and_limit_validation(monkeypatch, tmp_path):
    import app.main as main
    calls = []
    monkeypatch.setenv("FXPILOT_OPS_TOKEN", "secret")
    monkeypatch.setattr(main, "OPS_AUDIT_PATH", tmp_path / "ops_audit.json")
    monkeypatch.setattr(main, "api_media_reviews_reprocess", lambda force=False, limit=None: calls.append({"force": force, "limit": limit}) or {"requested": 0, "failed": 0})
    client = TestClient(main.app)
    ok = client.post("/api/ops/reviews/reprocess", headers={"X-FXPILOT-OPS-TOKEN": "secret"})
    assert ok.status_code == 200
    assert calls == [{"force": False, "limit": 1}]
    too_many = client.post("/api/ops/reviews/reprocess?limit=21", headers={"X-FXPILOT-OPS-TOKEN": "secret"})
    assert too_many.status_code == 422


def test_duplicate_operation_returns_409_and_lock_releases_after_error(monkeypatch, tmp_path):
    import app.main as main
    monkeypatch.setenv("FXPILOT_OPS_TOKEN", "secret")
    monkeypatch.setattr(main, "OPS_AUDIT_PATH", tmp_path / "ops_audit.json")
    client = TestClient(main.app)
    lock = main.OPS_LOCKS["review_reprocess"]
    assert lock.acquire(blocking=False)
    try:
        duplicate = client.post("/api/ops/reviews/reprocess", headers={"X-FXPILOT-OPS-TOKEN": "secret"})
        assert duplicate.status_code == 409
    finally:
        lock.release()
    monkeypatch.setattr(main, "api_media_reviews_reprocess", lambda force=False, limit=None: (_ for _ in ()).throw(RuntimeError("boom")))
    failed = client.post("/api/ops/reviews/reprocess", headers={"X-FXPILOT-OPS-TOKEN": "secret"})
    assert failed.status_code == 500
    monkeypatch.setattr(main, "api_media_reviews_reprocess", lambda force=False, limit=None: {"requested": 0, "failed": 0})
    released = client.post("/api/ops/reviews/reprocess", headers={"X-FXPILOT-OPS-TOKEN": "secret"})
    assert released.status_code == 200


def test_audit_log_contains_safe_fields_only(monkeypatch, tmp_path):
    import app.main as main
    token = "safe-token"
    monkeypatch.setenv("FXPILOT_OPS_TOKEN", token)
    monkeypatch.setattr(main, "OPS_AUDIT_PATH", tmp_path / "ops_audit.json")
    client = TestClient(main.app)
    client.post("/api/ops/cache/clear", headers={"X-FXPILOT-OPS-TOKEN": token})
    audit = client.get("/api/ops/audit", headers={"X-FXPILOT-OPS-TOKEN": token}).json()["records"]
    assert audit
    assert set(audit[-1]).issubset({"timestamp", "operation", "parameters", "success", "duration_ms", "summary", "error"})
    assert token not in json.dumps(audit)


def test_ui_does_not_run_mutating_operations_on_page_load():
    text = open("app/static/ops.js", encoding="utf-8").read()
    before_load = text.split("loadStatus().catch", 1)[0]
    assert "postOp(" in before_load
    assert "loadStatus().catch" in text
    assert "localStorage" not in text


def test_post_wrapper_reuses_existing_business_implementation(monkeypatch, tmp_path):
    import app.main as main
    called = {"import": 0}
    monkeypatch.setenv("FXPILOT_OPS_TOKEN", "secret")
    monkeypatch.setattr(main, "OPS_AUDIT_PATH", tmp_path / "ops_audit.json")
    monkeypatch.setattr(main, "_run_media_import", lambda: called.__setitem__("import", called["import"] + 1) or {"success": True, "imported": 0})
    response = TestClient(main.app).post("/api/ops/media/import", headers={"X-FXPILOT-OPS-TOKEN": "secret"})
    assert response.status_code == 200
    assert called["import"] == 1
