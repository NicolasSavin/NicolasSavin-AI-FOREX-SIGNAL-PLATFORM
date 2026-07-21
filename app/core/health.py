from __future__ import annotations

from typing import Any

from app.services.storage_manifest import build_storage_manifest
from app.services.storage_paths import storage_health


def _components(settings: Any, review_storage: Any | None) -> dict[str, Any]:
    storage = storage_health()
    manifest = build_storage_manifest()
    schemas_ok = all(item.get("validation_status") in {"ok", "missing", "legacy"} for item in manifest.get("stores", []))
    return {
        "storage": {"ok": bool(storage.get("healthy")), "status": storage.get("status"), "mode": storage.get("mode")},
        "schemas": {"ok": schemas_ok, "legacy_count": sum(1 for item in manifest.get("stores", []) if item.get("validation_status") == "legacy")},
        "scheduler": {"ok": settings.scheduler_interval_seconds > 0, "enabled": settings.scheduler_enabled},
        "execution": {"ok": settings.execution_mode == "DRY_RUN" and settings.kill_switch_enabled_default, "mode": settings.execution_mode, "kill_switch_default": settings.kill_switch_enabled_default},
        "configuration": {"ok": True, "ops_token_present": settings.ops_token_present, "external_providers_required": False},
    }


def build_readiness(settings: Any, services: Any, review_storage: Any | None = None) -> dict[str, Any]:
    components = _components(settings, review_storage)
    ready = all(bool(item.get("ok")) for item in components.values())
    return {"ready": ready, "status": "ready" if ready else "degraded", "components": components}


def build_ops_health(settings: Any, services: Any, review_storage: Any | None, lock_registry: Any) -> dict[str, Any]:
    payload = build_readiness(settings, services, review_storage)
    payload["locks"] = lock_registry.diagnostics() if lock_registry else []
    payload["secrets_exposed"] = False
    return payload
