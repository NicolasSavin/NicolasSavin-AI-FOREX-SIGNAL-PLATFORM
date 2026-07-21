from __future__ import annotations

import pytest

from app.core.settings import Settings
from app.services.storage_manifest import build_storage_manifest


def test_live_execution_is_rejected():
    settings = Settings(execution_mode="LIVE")
    with pytest.raises(ValueError):
        settings.validate_startup()


def test_kill_switch_defaults_safe():
    settings = Settings()
    assert settings.execution_mode == "DRY_RUN"
    assert settings.kill_switch_enabled_default is True


def test_storage_manifest_hides_absolute_paths():
    payload = build_storage_manifest()
    assert payload["data_dir"] == "configured"
    assert all("/" not in item["filename"] for item in payload["stores"])
