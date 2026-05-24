from __future__ import annotations

import json
import logging
import sys
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)


def _short_text(value: Any) -> bool:
    text = str(value or "").strip()
    return len(text) < 900 or text.count("\n") < 4


def _parse_json_or_text(value: str) -> dict[str, Any]:
    text = str(value or "").strip().replace("```json", "").replace("```", "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {"article_ru": text}
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
                return parsed if isinstance(parsed, dict) else {"article_ru": text}
            except Exception:
                pass
    return {"article_ru": text}


def install_analytics_rich_article_patch() -> None:
    if getattr(sys, "_ANALYTICS_RICH_ARTICLE_PATCH_STARTED", False):
        return
    setattr(sys, "_ANALYTICS_RICH_ARTICLE_PATCH_STARTED", True)

    def patcher() -> None:
        deadline = time.time() + 30
        while time.time() < deadline:
            module = sys.modules.get("app.main")
            target = getattr(module, "_build_mt4_chat_analytics_response", None) if module else None
            chat_service = getattr(module, "chat_service", None) if module else None
            store = getattr(module, "MT4_CANDLE_STORE", None) if module else None
            normalize_symbol = getattr(module, "normalize_symbol", None) if module else None

            if callable(target) and chat_service is not None and isinstance(store, dict) and callable(normalize_symbol):
                if getattr(module, "_ANALYTICS_RICH_ARTICLE_PATCHED", False):
                    return

                async def wrapped_build_mt4_chat_analytics_response(pair: str, use_fundamental: bool = False) -> dict[str, Any]:
                    payload = await target(pair, use_fundamental)
                    if not isinstance(payload, dict):
                        return payload

                    article = payload.get("article_ru") or payload.get("journalistic_summary_ru") or payload.get("summary_ru")
                    if not _short_text(article):
                        return payload

                    client = getattr(chat_service, "client", None)
                    model = str(getattr(chat_service, "model", "") or "").removesuffix(":online")
                    symbol = str(normalize_symbol(pair) or pair or "").upper().strip()
                    candles = ((store.get(f"{symbol}:M15") or {}).get("candles") or [])[-90:]
                    if not client or not model or not candles:
                        return payload

                    prompt = (
                        "Сделай подробный профессиональный FX-анализ на русском для страницы аналитики. "
                        "Верни строго JSON с полями article_ru, summary_ru, scenario_ru, risk_ru, invalidation_ru, volume_ru, options_ru. "
                        "article_ru должен быть 900-1600 слов/символов минимум: структурированный разбор без воды. "
                        "Обязательно раскрой: 1) текущая ситуация, 2) структура M15, 3) ликвидность/SMC, "
                        "4) ключевые уровни support/resistance, 5) основной сценарий, 6) альтернативный сценарий, "
                        "7) инвалидация, 8) риск-менеджмент. Не обещай прибыль. Не выдумывай новости. "
                        "Если фундаментальных/опционных/реальных объемных данных нет — прямо напиши ограничение.\n\n"
                        f"PAIR: {symbol}\n"
                        f"CURRENT_PAYLOAD: {json.dumps(payload, ensure_ascii=False)[:3000]}\n"
                        f"M15_CANDLES: {json.dumps(candles, ensure_ascii=False)[:12000]}"
                    )
                    try:
                        response = await client.chat.completions.create(
                            model=model,
                            messages=[
                                {"role": "system", "content": "Ты senior FX/SMC analyst. Пиши строго по-русски, конкретно, структурно, без маркетинга."},
                                {"role": "user", "content": prompt},
                            ],
                            temperature=0.18,
                            max_tokens=1800,
                            timeout=30,
                        )
                        text = (response.choices[0].message.content or "").strip() if response.choices else ""
                        data = _parse_json_or_text(text)
                        rich_article = str(data.get("article_ru") or "").strip()
                        if len(rich_article) > len(str(article or "")):
                            payload.update(
                                {
                                    "article_ru": rich_article,
                                    "summary_ru": str(data.get("summary_ru") or payload.get("summary_ru") or payload.get("summary") or ""),
                                    "scenario_ru": str(data.get("scenario_ru") or payload.get("scenario_ru") or ""),
                                    "risk_ru": str(data.get("risk_ru") or payload.get("risk_ru") or ""),
                                    "invalidation_ru": str(data.get("invalidation_ru") or payload.get("invalidation_ru") or ""),
                                    "volume_ru": str(data.get("volume_ru") or payload.get("volume_ru") or ""),
                                    "options_ru": str(data.get("options_ru") or payload.get("options_ru") or ""),
                                    "ai_status": "ok_rich_article",
                                    "ai_model_used": model,
                                    "ai_error": None,
                                }
                            )
                            logger.info("analytics_rich_article_generated symbol=%s len=%s", symbol, len(rich_article))
                    except Exception as exc:
                        logger.exception("analytics_rich_article_failed symbol=%s error=%s", symbol, type(exc).__name__)
                        payload["ai_rich_error"] = type(exc).__name__
                    return payload

                module._build_mt4_chat_analytics_response = wrapped_build_mt4_chat_analytics_response
                setattr(module, "_ANALYTICS_RICH_ARTICLE_PATCHED", True)
                logger.info("analytics_rich_article_patch_installed")
                return
            time.sleep(0.25)
        logger.warning("analytics_rich_article_patch_timeout")

    threading.Thread(target=patcher, name="analytics-rich-article-patcher", daemon=True).start()
