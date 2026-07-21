from __future__ import annotations

from app.core.locks import LockRegistry


def test_offline_pipeline_fixture_is_deterministic_without_dispatch():
    stages = ["media", "review", "knowledge_graph", "consensus", "authors", "performance", "validation", "market_state", "multi_timeframe", "confluence", "opportunities", "decisions", "strategy", "approved_signals", "paper", "portfolio", "execution_order_build"]
    def run_once():
        state = []
        for stage in stages:
            state.append({"stage": stage, "id": f"fixture-{stage}", "broker_dispatched": False})
        return state
    first = run_once()
    second = run_once()
    assert first == second
    assert [item["stage"] for item in first] == stages
    assert all(item["broker_dispatched"] is False for item in first)


def test_lock_registry_prevents_duplicate_operation():
    locks = LockRegistry()
    with locks.acquire("pipeline_run", owner="a") as first:
        with locks.acquire("pipeline_run", owner="b") as second:
            assert first is True
            assert second is False
