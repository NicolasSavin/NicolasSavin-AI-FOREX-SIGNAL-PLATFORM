from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Any

from app.services.ai_gateway import gateway, result_meta

logger = logging.getLogger(__name__)


def _patch_news_service(module: Any) -> None:
    original = getattr(module, "rewrite_news_with_xai", None)
    if not callable(original) or getattr(module, "_AI_GATEWAY_NEWS_PATCHED", False):
        return

    def patched_rewrite_news_with_xai(
        title: str,
        summary: str,
        source: str,
        published_at: str,
        markets: list[str],
    ) -> dict[str, Any] | None:
        prompt = (
            "Перепиши финансовую новость на русском для forex-трейдера. "
            "Используй только входной текст, не добавляй новых фактов. "
            "Верни JSON с полями: title_ru, summary_ru, market_impact_ru, affected_assets, sentiment, humor_ru. "
            "Если JSON не получается, верни обычный русский текст.\n\n"
            f"title: {title}\n"
            f"summary: {summary}\n"
            f"source: {source}\n"
            f"published_at: {published_at}\n"
            f"markets: {markets}\n"
        )
        result = gateway().complete_sync(
            system=(
                "Ты русскоязычный forex news analyst. Объясняй: что произошло, "
                "почему важно, какие валюты/активы могут реагировать. Без выдуманных фактов."
            ),
            user=prompt,
            primary_model=getattr(module, "XAI_MODEL", None),
            temperature=0.35,
            max_tokens=700,
            expect_json=True,
            task="news_rewrite",
        )
        if result.ok:
            data = result.data if isinstance(result.data, dict) else {}
            strip_html = getattr(module, "strip_html", lambda value: str(value or ""))
            payload = {
                "title_ru": strip_html(str(data.get("title_ru") or title)).strip(),
                "summary_ru": strip_html(str(data.get("summary_ru") or result.text or summary)).strip(),
                "market_impact_ru": strip_html(str(data.get("market_impact_ru") or "Влияние оценивается по валютам, доллару, золоту и риск-сентименту.")).strip(),
                "affected_assets": data.get("affected_assets") if isinstance(data.get("affected_assets"), list) else (markets or ["USD", "EURUSD", "XAUUSD"]),
                "sentiment": strip_html(str(data.get("sentiment") or "neutral")).strip().lower()[:20] or "neutral",
                "humor_ru": strip_html(str(data.get("humor_ru") or "Рынок снова проверяет, кто читал новость до конца.")).strip(),
            }
            payload.update(result_meta(result))
            return payload

        fallback = original(title, summary, source, published_at, markets)
        if fallback:
            return fallback
        return {
            "title_ru": title,
            "summary_ru": summary or "Описание новости временно недоступно.",
            "market_impact_ru": "Grok/OpenRouter временно недоступен, показан исходный текст новости.",
            "affected_assets": markets or ["USD", "EURUSD", "XAUUSD"],
            "sentiment": "neutral",
            "humor_ru": "Сегодня без фирменной шутки Grok — ждём следующий апдейт.",
            "ai_provider": "grok",
            "ai_status": "fallback_local",
            "ai_error": result.error,
        }

    module.rewrite_news_with_xai = patched_rewrite_news_with_xai
    setattr(module, "_AI_GATEWAY_NEWS_PATCHED", True)
    logger.info("ai_gateway_news_patch_installed")


def install_ai_news_runtime_patch() -> None:
    if getattr(sys, "_AI_GATEWAY_NEWS_PATCH_THREAD", False):
        return
    setattr(sys, "_AI_GATEWAY_NEWS_PATCH_THREAD", True)

    def worker() -> None:
        deadline = time.time() + 30
        while time.time() < deadline:
            module = sys.modules.get("app.services.news_service")
            if module is not None:
                _patch_news_service(module)
            time.sleep(0.25)

    threading.Thread(target=worker, name="ai-news-runtime-patcher", daemon=True).start()
