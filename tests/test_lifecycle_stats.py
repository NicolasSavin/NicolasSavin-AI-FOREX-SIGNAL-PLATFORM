from __future__ import annotations

from app.services.idea_lifecycle import build_lifecycle_stats


def test_lifecycle_stats_adds_dashboard_fields_without_changing_legacy_total() -> None:
    active = {"EURUSD": {"entry": 1.10, "sl": 1.09, "tp": 1.12}}
    archive = [
        {"symbol": "GBPUSD", "entry": 1.25, "sl": 1.24, "tp": 1.27, "result": "TP", "closed_at_utc": "2026-06-04T08:00:00+00:00"},
        {"symbol": "USDJPY", "entry": 150.0, "sl": 151.0, "tp": 148.0, "result": "SL", "closed_at_utc": "2020-01-01T08:00:00+00:00"},
    ]

    stats = build_lifecycle_stats(active, archive)

    assert stats["total"] == 2
    assert stats["total_ideas"] == 3
    assert stats["active"] == 1
    assert stats["tp"] == 1
    assert stats["sl"] == 1
    assert stats["average_rr"] == 2.0
    assert "today_tp" in stats
    assert "today_sl" in stats


def test_archive_can_optionally_include_active_without_changing_default(monkeypatch) -> None:
    from app import main

    lifecycle = {"active": [{"symbol": "EURUSD", "status": "active"}], "archive": [{"symbol": "GBPUSD", "result": "TP"}]}
    monkeypatch.setattr(main, "apply_idea_lifecycle", lambda ideas: lifecycle)

    assert main.api_archive() == {"archive": lifecycle["archive"], "total": 1}
    assert main.api_archive(include_active=True) == {
        "archive": lifecycle["archive"],
        "total": 1,
        "active": lifecycle["active"],
        "items": [*lifecycle["active"], *lifecycle["archive"]],
    }
