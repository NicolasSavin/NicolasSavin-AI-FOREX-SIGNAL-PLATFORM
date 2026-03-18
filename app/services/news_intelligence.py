from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


RUSSIAN_IMPORTANCE = {
    "low": "низкая",
    "medium": "средняя",
    "high": "высокая",
}

RUSSIAN_IMPORTANCE_GENITIVE = {
    "low": "низкой",
    "medium": "средней",
    "high": "высокой",
}

CATEGORY_PRIORITY = (
    "Central Banks",
    "Macro",
    "Forex",
    "Gold",
    "Crypto",
    "Commodities",
    "Indices",
)

STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "of",
    "to",
    "for",
    "in",
    "on",
    "at",
    "with",
    "from",
    "by",
    "as",
    "is",
    "are",
    "be",
    "more",
    "than",
    "after",
    "amid",
    "into",
    "market",
    "markets",
    "news",
    "says",
    "say",
    "update",
    "analysis",
    "daily",
    "week",
    "today",
}

TRANSLATIONS = {
    "federal reserve": "ФРС",
    "fed": "ФРС",
    "ecb": "ЕЦБ",
    "bank of england": "Банк Англии",
    "boe": "Банк Англии",
    "bank of japan": "Банк Японии",
    "boj": "Банк Японии",
    "interest rates": "процентные ставки",
    "interest rate": "процентная ставка",
    "rate cut": "снижение ставки",
    "rate cuts": "снижения ставки",
    "rate hike": "повышение ставки",
    "rate hikes": "повышения ставки",
    "inflation": "инфляция",
    "cpi": "CPI",
    "ppi": "PPI",
    "payrolls": "рынок труда",
    "nonfarm payrolls": "Nonfarm Payrolls",
    "jobs": "занятость",
    "labour market": "рынок труда",
    "economy": "экономика",
    "recession": "рецессия",
    "growth": "рост",
    "yields": "доходности",
    "yield": "доходность",
    "us dollar": "доллар США",
    "dollar": "доллар",
    "euro": "евро",
    "pound": "фунт",
    "sterling": "фунт",
    "yen": "иена",
    "gold": "золото",
    "silver": "серебро",
    "oil": "нефть",
    "crude": "нефть",
    "bitcoin": "биткоин",
    "crypto": "крипторынок",
    "stocks": "акции",
    "equities": "акции",
    "shares": "акции",
    "surges": "резко растёт",
    "jumps": "резко растёт",
    "rises": "растёт",
    "falls": "снижается",
    "drops": "снижается",
    "slips": "снижается",
    "holds": "сохраняет",
    "keeps": "сохраняет",
    "signals": "сигнализирует",
    "warns": "предупреждает",
    "expects": "ожидает",
    "unexpectedly": "неожиданно",
    "stronger": "сильнее",
    "weaker": "слабее",
    "higher": "выше",
    "lower": "ниже",
    "set to": "готовится",
    "amid": "на фоне",
    "soft": "слабой",
    "uncertainty": "неопределённости",
    "global": "глобальной",
    "keep": "сохраняет",
    "open": "открытыми",
    "risks": "рисков",
    "rangebound": "в боковом диапазоне",
    "trade": "торги",
    "persists": "сохраняются",
    "losses": "потери",
    "near": "около",
    "supply": "предложения",
    "concerns": "опасения",
    "ease": "ослабевают",
    "holds": "держится",
    "hold": "сохраняет",
    "options": "опционы",
    "conflict": "конфликта",
    "policy": "политику",
    "markets": "рынки",
    "market": "рынок",
}

ASSET_RULES = {
    "EURUSD": {"eurusd", "eur", "euro", "ecb", "eurozone"},
    "GBPUSD": {"gbpusd", "gbp", "sterling", "boe", "bank of england", "uk"},
    "USDJPY": {"usdjpy", "jpy", "yen", "boj", "bank of japan", "japan"},
    "XAUUSD": {"xauusd", "gold", "bullion"},
    "BTCUSD": {"btcusd", "bitcoin", "btc", "crypto", "cryptocurrency"},
    "DXY": {"dxy", "dollar index", "us dollar", "usd", "greenback"},
    "NASDAQ": {"nasdaq", "tech stocks", "technology stocks"},
    "SP500": {"s&p 500", "sp500", "s&p", "wall street", "equities", "stocks"},
    "USOIL": {"oil", "wti", "brent", "crude", "usoil"},
}


@dataclass(slots=True)
class SignalRelation:
    has_related_signal: bool
    related_signal_symbol: str | None
    related_signal_direction: str | None
    effect_on_signal: str
    effect_on_signal_ru: str


class NewsIntelligenceService:
    def deduplicate(self, items: Iterable[dict]) -> list[dict]:
        unique: list[dict] = []
        seen_urls: set[str] = set()
        seen_titles: set[str] = set()
        seen_signatures: list[str] = []

        for item in items:
            canonical_url = self._canonicalize_url(item.get("source_url") or item.get("link") or "")
            title_key = self._normalize_text(item.get("title_original") or item.get("title") or "")
            signature = self._semantic_signature(
                f"{item.get('title_original', '')} {item.get('summary_original', '')} {item.get('source', '')}"
            )

            if canonical_url and canonical_url in seen_urls:
                continue
            if title_key and title_key in seen_titles:
                continue
            if signature and any(self._is_similar_signature(signature, known) for known in seen_signatures):
                continue

            if canonical_url:
                seen_urls.add(canonical_url)
            if title_key:
                seen_titles.add(title_key)
            if signature:
                seen_signatures.append(signature)
            unique.append(item)

        return unique

    def enrich(self, raw_item: dict, active_signals: list[dict]) -> dict:
        content = " ".join(
            part for part in [raw_item.get("title_original", ""), raw_item.get("summary_original", ""), raw_item.get("source", "")] if part
        )
        lowered = content.lower()

        assets = self._detect_assets(lowered)
        category = self._detect_category(lowered, assets)
        importance = self._detect_importance(lowered, category)
        directional_effects = self._directional_effects(lowered, assets, category)
        title_ru = self._generated_title_ru(category, assets, lowered)
        summary_ru = self._summary_ru(lowered, title_ru, assets, category, importance)
        what_happened_ru = self._what_happened_ru(title_ru)
        why_it_matters_ru = self._why_it_matters_ru(lowered, category, assets)
        market_impact_ru = self._market_impact_ru(directional_effects, assets)
        relation = self._signal_relation(active_signals, assets, directional_effects)
        published_at = raw_item.get("published_at") or raw_item.get("published_at_utc")

        identifier_seed = f"{raw_item.get('source','source')}|{raw_item.get('title_original', raw_item.get('title',''))}|{published_at or ''}"
        digest = hashlib.sha1(identifier_seed.encode("utf-8")).hexdigest()[:12]
        return {
            "id": f"news_{digest}",
            "title_original": raw_item.get("title_original") or raw_item.get("title") or "",
            "title_ru": title_ru,
            "summary_ru": summary_ru,
            "what_happened_ru": what_happened_ru,
            "why_it_matters_ru": why_it_matters_ru,
            "market_impact_ru": market_impact_ru,
            "category": category,
            "importance": importance,
            "importance_ru": RUSSIAN_IMPORTANCE[importance],
            "assets": assets,
            "source": raw_item.get("source") or "RSS",
            "source_url": raw_item.get("source_url") or raw_item.get("link"),
            "published_at": published_at,
            "signal_relation": asdict(relation),
        }

    def _detect_assets(self, content: str) -> list[str]:
        found: list[str] = []
        for asset, keywords in ASSET_RULES.items():
            if any(keyword in content for keyword in keywords):
                found.append(asset)

        if not found:
            if any(word in content for word in {"inflation", "fed", "federal reserve", "treasury", "us economy"}):
                found.extend(["DXY", "EURUSD", "XAUUSD"])
            elif any(word in content for word in {"ecb", "eurozone", "euro area"}):
                found.append("EURUSD")
            elif any(word in content for word in {"risk appetite", "wall street", "earnings"}):
                found.extend(["NASDAQ", "SP500"])

        deduped: list[str] = []
        for asset in found:
            if asset not in deduped:
                deduped.append(asset)
        return deduped[:5]

    def _detect_category(self, content: str, assets: list[str]) -> str:
        if any(word in content for word in {"fed", "ecb", "bank of england", "bank of japan", "central bank", "rate decision", "minutes"}):
            return "Central Banks"
        if any(word in content for word in {"inflation", "cpi", "ppi", "gdp", "payroll", "retail sales", "jobs", "economy", "recession"}):
            return "Macro"
        if "XAUUSD" in assets or "gold" in content:
            return "Gold"
        if "BTCUSD" in assets or any(word in content for word in {"bitcoin", "crypto", "ethereum", "etf"}):
            return "Crypto"
        if "USOIL" in assets or any(word in content for word in {"oil", "crude", "brent", "opec"}):
            return "Commodities"
        if any(asset in assets for asset in {"NASDAQ", "SP500"}) or any(word in content for word in {"nasdaq", "s&p", "stocks", "equities", "index"}):
            return "Indices"
        return "Forex"

    def _detect_importance(self, content: str, category: str) -> str:
        if category in {"Central Banks", "Macro"}:
            if any(word in content for word in {"inflation", "cpi", "rate", "payroll", "jobs", "fed", "ecb", "boe", "boj", "gdp", "tariff"}):
                return "high"
        if any(word in content for word in {"dollar", "eur", "gbp", "jpy", "gold", "oil", "bitcoin", "nasdaq", "s&p"}):
            return "medium"
        return "low"

    def _translate_title(self, title: str, category: str, assets: list[str], content: str) -> str:
        text = " ".join(title.split())
        lowered = text.lower()
        for source_suffix in [" - reuters", " - fxstreet", " - forexlive", " - investing.com", " - financial times"]:
            if lowered.endswith(source_suffix):
                text = text[: -len(source_suffix)]
                lowered = text.lower()
        for original, translated in sorted(TRANSLATIONS.items(), key=lambda item: len(item[0]), reverse=True):
            text = re.sub(rf"\b{re.escape(original)}\b", translated, text, flags=re.IGNORECASE)
        text = re.sub(r"\s+", " ", text).strip(" -–—")
        text = text.replace("USD/CAD", "USD/CAD").replace("EUR/USD", "EURUSD").replace("GBP/USD", "GBPUSD").replace("USD/JPY", "USDJPY")
        text = re.sub(r"\s+", " ", text).strip()
        if text and text[0].islower():
            text = text[0].upper() + text[1:]
        latin_words = re.findall(r"[A-Za-z]{4,}", text)
        if latin_words and len(latin_words) >= 2:
            return self._fallback_title_ru(category, assets, content)
        return text or self._fallback_title_ru(category, assets, content)



    @staticmethod
    def _primary_asset(assets: list[str]) -> str:
        for asset in assets:
            if asset != "DXY":
                return asset
        return assets[0] if assets else "рынку"

    def _generated_title_ru(self, category: str, assets: list[str], content: str) -> str:
        asset_text = self._primary_asset(assets)
        if any(word in content for word in {"inflation", "cpi", "ppi"}):
            return f"Инфляция меняет ожидания по {asset_text}"
        if any(word in content for word in {"fed", "federal reserve", "ecb", "boe", "boj", "central bank"}):
            return f"Центробанк дал новый сигнал по ставкам для {asset_text}"
        if any(word in content for word in {"payroll", "jobs", "labour", "employment"}):
            return f"Данные по занятости дали новый импульс для {asset_text}"
        if category == "Gold":
            return "Золото получило новый рыночный драйвер"
        if category == "Crypto":
            return "Крипторынок получил новый драйвер"
        if category == "Commodities":
            return f"Сырьевой рынок меняет настрой по {asset_text}"
        if category == "Indices":
            return "Фондовые индексы получили новый рыночный сигнал"
        if category == "Forex" and asset_text != "рынку":
            return f"Рынок пересматривает краткосрочный настрой по {asset_text}"
        return self._fallback_title_ru(category, assets, content)

    def _fallback_title_ru(self, category: str, assets: list[str], content: str) -> str:
        asset_text = self._primary_asset(assets)
        if category == "Central Banks":
            return f"Центробанк дал новый сигнал по ставкам для {asset_text}"
        if category == "Macro":
            return f"Вышли важные макроданные для {asset_text}"
        if category == "Gold":
            return "На рынке золота вышла важная новость"
        if category == "Crypto":
            return "На крипторынке появилась значимая новость"
        if category == "Commodities":
            return f"Сырьевой рынок получил новый драйвер для {asset_text}"
        if category == "Indices":
            return "Фондовые индексы получили новый рыночный сигнал"
        if asset_text == "рынку":
            return "Рынок Forex получил новый новостной драйвер"
        return f"На рынке Forex вышла новость по {asset_text}"

    def _summary_ru(self, content: str, title_ru: str, assets: list[str], category: str, importance: str) -> str:
        if category == "Central Banks":
            return "Новость меняет ожидания по ставкам и может быстро перестроить движение по валютам и золоту."
        if category == "Macro":
            return "Свежие макроданные могут усилить волатильность, потому что рынок пересматривает ожидания по доллару и рисковым активам."
        if category == "Crypto":
            return "Новость важна для крипторынка и может повлиять на интерес к риску в течение ближайших сессий."
        if category == "Gold":
            return "Событие способно быстро изменить спрос на золото как на защитный актив."
        if category == "Commodities":
            return "Новость влияет на сырьевой блок и может сдвинуть ожидания по нефти и связанным активам."
        asset_text = ", ".join(assets) if assets else "основным валютным инструментам"
        impact_text = RUSSIAN_IMPORTANCE_GENITIVE[importance]
        return f"Коротко: {title_ru}. Для трейдера это событие {impact_text} важности, которое стоит учитывать по {asset_text}."

    def _what_happened_ru(self, title_ru: str) -> str:
        return f"На рынке вышла новость: {title_ru.rstrip('.')}."

    def _why_it_matters_ru(self, content: str, category: str, assets: list[str]) -> str:
        if any(word in content for word in {"fed", "federal reserve", "ecb", "boe", "boj", "rate", "minutes"}):
            return "Такие сообщения двигают ожидания по ставкам, а значит напрямую влияют на валюты, золото и аппетит к риску."
        if any(word in content for word in {"inflation", "cpi", "ppi"}):
            return "Инфляция меняет взгляд рынка на будущие решения центробанков и часто запускает сильный импульс по доллару и золоту."
        if any(word in content for word in {"payroll", "jobs", "labour", "employment"}):
            return "Сильный или слабый рынок труда быстро влияет на ожидания по доллару и доходностям облигаций."
        if category == "Crypto":
            return "Для крипторынка важно, растёт ли готовность инвесторов к риску и куда движется долларовая ликвидность."
        if category == "Gold":
            return "Золото чувствительно к доллару, доходностям и спросу на защитные активы."
        if category == "Indices":
            return "Индексы реагируют на изменение ставок, ожиданий по прибыли и общего аппетита к риску."
        asset_text = ", ".join(assets) if assets else "рынка"
        return f"Это важно для {asset_text}, потому что новость может изменить краткосрочный баланс спроса и предложения."

    def _market_impact_ru(self, effects: dict[str, str], assets: list[str]) -> str:
        if not effects:
            asset_text = ", ".join(assets) if assets else "основным инструментам"
            return f"Влияние умеренное: следите за реакцией цены по {asset_text} после подтверждения движения."

        labels = []
        for asset, effect in effects.items():
            if effect == "bullish":
                labels.append(f"позитивно для {asset}")
            elif effect == "bearish":
                labels.append(f"негативно для {asset}")
        if not labels:
            return "Эффект смешанный: реакция рынка зависит от того, подтвердится ли импульс в цене."
        result = "; ".join(labels[:4])
        return result[:1].upper() + result[1:] + "."

    def _signal_relation(self, active_signals: list[dict], assets: list[str], effects: dict[str, str]) -> SignalRelation:
        tradable = [signal for signal in active_signals if signal.get("action") in {"BUY", "SELL"} and signal.get("lifecycle_state") == "active"]
        for signal in tradable:
            symbol = signal.get("symbol")
            if symbol not in assets:
                continue
            effect = effects.get(symbol)
            if effect == "bullish":
                effect_on_signal = "strengthens_signal" if signal.get("action") == "BUY" else "weakens_signal"
            elif effect == "bearish":
                effect_on_signal = "strengthens_signal" if signal.get("action") == "SELL" else "weakens_signal"
            else:
                effect_on_signal = "neutral_to_signal"

            relation_text = {
                "strengthens_signal": f"Новость усиливает текущий сигнал {signal.get('action')} по {symbol}",
                "weakens_signal": f"Новость ослабляет текущий сигнал {signal.get('action')} по {symbol}",
                "neutral_to_signal": f"Новость нейтральна для текущего сигнала {signal.get('action')} по {symbol}",
            }[effect_on_signal]
            return SignalRelation(
                has_related_signal=True,
                related_signal_symbol=symbol,
                related_signal_direction=signal.get("action"),
                effect_on_signal=effect_on_signal,
                effect_on_signal_ru=relation_text,
            )

        return SignalRelation(
            has_related_signal=False,
            related_signal_symbol=None,
            related_signal_direction=None,
            effect_on_signal="neutral_to_signal",
            effect_on_signal_ru="Связанного активного сигнала сейчас нет.",
        )

    def _directional_effects(self, content: str, assets: list[str], category: str) -> dict[str, str]:
        effects: dict[str, str] = {}
        hawkish = any(word in content for word in {"higher inflation", "inflation rises", "strong inflation", "rate hike", "hawkish", "strong jobs", "strong payroll", "higher yields"})
        dovish = any(word in content for word in {"lower inflation", "inflation cools", "rate cut", "dovish", "weak jobs", "weak payroll", "recession fears", "slowing economy"})
        risk_on = any(word in content for word in {"risk appetite", "optimism", "record high", "rally", "stimulus"})
        risk_off = any(word in content for word in {"geopolitical", "war", "tariff", "selloff", "safe haven", "uncertainty"})

        if any(word in content for word in {"fed", "federal reserve", "us inflation", "us jobs", "dollar"}):
            if hawkish:
                effects.update({"DXY": "bullish", "EURUSD": "bearish", "GBPUSD": "bearish", "XAUUSD": "bearish", "NASDAQ": "bearish", "SP500": "bearish", "BTCUSD": "bearish"})
            elif dovish:
                effects.update({"DXY": "bearish", "EURUSD": "bullish", "GBPUSD": "bullish", "XAUUSD": "bullish", "NASDAQ": "bullish", "SP500": "bullish", "BTCUSD": "bullish"})

        if any(word in content for word in {"ecb", "eurozone", "euro area"}):
            if hawkish:
                effects["EURUSD"] = "bullish"
            elif dovish:
                effects["EURUSD"] = "bearish"

        if any(word in content for word in {"boe", "bank of england", "uk inflation", "uk jobs"}):
            if hawkish:
                effects["GBPUSD"] = "bullish"
            elif dovish:
                effects["GBPUSD"] = "bearish"

        if any(word in content for word in {"boj", "bank of japan", "japan"}):
            if hawkish:
                effects["USDJPY"] = "bearish"
            elif dovish:
                effects["USDJPY"] = "bullish"

        if category == "Gold" or "gold" in content:
            if risk_off or dovish:
                effects["XAUUSD"] = "bullish"
            elif hawkish or risk_on:
                effects.setdefault("XAUUSD", "bearish")

        if category == "Crypto" or "bitcoin" in content or "crypto" in content:
            if risk_on:
                effects["BTCUSD"] = "bullish"
            elif risk_off or hawkish:
                effects.setdefault("BTCUSD", "bearish")

        if category == "Commodities" or any(word in content for word in {"oil", "crude", "opec"}):
            if any(word in content for word in {"supply cut", "sanctions", "disruption", "inventory draw"}):
                effects["USOIL"] = "bullish"
            elif any(word in content for word in {"inventory build", "demand fears", "oversupply"}):
                effects["USOIL"] = "bearish"

        if category == "Indices" and not any(asset in effects for asset in {"NASDAQ", "SP500"}):
            if risk_on:
                effects.update({"NASDAQ": "bullish", "SP500": "bullish"})
            elif risk_off or hawkish:
                effects.update({"NASDAQ": "bearish", "SP500": "bearish"})

        return {asset: effect for asset, effect in effects.items() if asset in assets or asset in {"DXY", "NASDAQ", "SP500"}}

    @staticmethod
    def _normalize_text(value: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.lower())).strip()

    def _semantic_signature(self, value: str) -> str:
        tokens = [token for token in self._normalize_text(value).split() if token not in STOPWORDS and len(token) > 2]
        return " ".join(sorted(set(tokens))[:10])

    @staticmethod
    def _is_similar_signature(left: str, right: str) -> bool:
        if not left or not right:
            return False
        if left == right:
            return True
        ratio = SequenceMatcher(None, left, right).ratio()
        left_tokens = set(left.split())
        right_tokens = set(right.split())
        overlap = len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))
        return ratio >= 0.86 or overlap >= 0.72

    @staticmethod
    def _canonicalize_url(url: str) -> str:
        if not url:
            return ""
        parsed = urlparse(url)
        query = urlencode(sorted((key, value) for key, value in parse_qsl(parsed.query) if not key.startswith("utm_")))
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", query, ""))
