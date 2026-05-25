from __future__ import annotations

import logging
import os

from app.core.analytics_candles_bridge_patch import install_analytics_candles_bridge_patch
from app.core.legacy_ideas_market_post_patch import install_legacy_ideas_market_post_patch
from app.core.analytics_rich_article_patch import install_analytics_rich_article_patch
from app.core.prop_score_recovery_patch import install_prop_score_recovery_patch
from app.core.safe_ai_json_patch import install_safe_ai_json_patch
from app.core.safe_grok_text_patch import install_safe_grok_text_patch

logger = logging.getLogger(__name__)


def get_env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip()
    if not normalized:
        return default
    return normalized


def get_openrouter_api_key() -> str | None:
    return get_env("OPENROUTER_API_KEY")


def get_openrouter_model() -> str:
    model = get_env("OPENROUTER_MODEL")
    logger.info("OPENROUTER MODEL: %s", model)
    if model:
        return model
    return "x-ai/grok-4.3"


def get_twelvedata_api_key() -> str | None:
    return get_env("TWELVEDATA_API_KEY")


install_safe_ai_json_patch()
install_analytics_candles_bridge_patch()
install_analytics_rich_article_patch()
install_legacy_ideas_market_post_patch()
install_prop_score_recovery_patch()

# SAFE grok patch (не ломает FastAPI)
install_safe_grok_text_patch()
