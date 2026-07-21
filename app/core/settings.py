from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


_TRUE = {"1", "true", "yes", "on"}


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in _TRUE


@dataclass(frozen=True)
class Settings:
    storage_mode: str = "local"
    data_dir: Path = Path("data")
    ops_token_present: bool = False
    llm_provider: str = "openrouter"
    llm_model: str = ""
    scheduler_enabled: bool = False
    scheduler_interval_seconds: int = 900
    execution_mode: str = "DRY_RUN"
    execution_enabled: bool = False
    kill_switch_enabled_default: bool = True
    external_providers_enabled: bool = False
    feature_flags: dict[str, bool] = field(default_factory=dict)
    cache_ttl_seconds: dict[str, int] = field(default_factory=dict)
    risk_thresholds: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(os.getenv("FXPILOT_DATA_DIR", "data")).expanduser()
        return cls(
            storage_mode=os.getenv("FXPILOT_STORAGE_MODE", "persistent" if os.getenv("FXPILOT_DATA_DIR") else "local").strip().lower() or "local",
            data_dir=data_dir,
            ops_token_present=bool(os.getenv("FXPILOT_OPS_TOKEN", "").strip()),
            llm_provider=os.getenv("LLM_PROVIDER", os.getenv("OPENROUTER_PROVIDER", "openrouter")).strip() or "openrouter",
            llm_model=os.getenv("OPENROUTER_MODEL", os.getenv("OPENAI_MODEL", "")).strip(),
            scheduler_enabled=_bool("FXPILOT_SCHEDULER_ENABLED", False),
            scheduler_interval_seconds=int(os.getenv("FXPILOT_SCHEDULER_INTERVAL_SECONDS", os.getenv("MEDIA_AUTOMATION_INTERVAL_SECONDS", "900"))),
            execution_mode=os.getenv("FXPILOT_EXECUTION_MODE", "DRY_RUN").strip().upper() or "DRY_RUN",
            execution_enabled=_bool("FXPILOT_EXECUTION_ENABLED", False),
            kill_switch_enabled_default=_bool("FXPILOT_EXECUTION_KILL_SWITCH", True),
            external_providers_enabled=_bool("FXPILOT_EXTERNAL_PROVIDERS_ENABLED", _bool("ALLOW_EXTERNAL_FALLBACK", True)),
            feature_flags={"orderflow": _bool("ORDERFLOW_ENGINE_ENABLED", False)},
            cache_ttl_seconds={"market_ideas": int(os.getenv("MARKET_IDEAS_CACHE_TTL_SECONDS", "60"))},
            risk_thresholds={"max_portfolio_risk_pct": float(os.getenv("FXPILOT_MAX_PORTFOLIO_RISK_PCT", "2.0"))},
        )

    def validate_startup(self) -> None:
        if self.execution_mode != "DRY_RUN":
            raise ValueError("LIVE execution is not implemented; FXPILOT_EXECUTION_MODE must remain DRY_RUN.")
        if self.scheduler_interval_seconds <= 0:
            raise ValueError("Scheduler interval must be positive.")
        if self.execution_enabled and not self.kill_switch_enabled_default:
            raise ValueError("Execution cannot start with kill switch disabled by default.")
