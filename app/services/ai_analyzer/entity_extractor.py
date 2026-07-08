from __future__ import annotations

import re

from app.services.ai_analyzer.models import ExtractedEntities

SYMBOLS = ["EURUSD","GBPUSD","USDJPY","USDCHF","AUDUSD","NZDUSD","USDCAD","XAUUSD","XAGUSD","BTC","ETH","NASDAQ","SP500","US30","GER40","DXY","WTI","BRENT"]
TIMEFRAMES = ["M1","M5","M15","M30","H1","H4","D1","W1","MN"]
DIRECTION_ALIASES = {"BULLISH":"BUY","LONG":"BUY","BUY":"BUY","РОСТ":"BUY","ЛОНГ":"BUY","ПОКУПКА":"BUY","BEARISH":"SELL","SHORT":"SELL","SELL":"SELL","ПАДЕНИЕ":"SELL","ШОРТ":"SELL","ПРОДАЖА":"SELL","NEUTRAL":"NEUTRAL"}
INDICATORS = ["VWAP","Delta","CumDelta","Footprint","Volume Profile","Volume","POC","VAH","VAL","DOM","Liquidity","Absorption","Sweep","Iceberg","Imbalance","OrderFlow","Gamma","Options","Open Interest","OI"]
CONCEPTS = ["Liquidity","Absorption","Sweep","Iceberg","Imbalance","OrderFlow","Gamma","Options","Open Interest"]
PRICE_RE = re.compile(r"(?<![\w])\d{1,6}(?:[.,]\d{1,5})?(?![\w])")


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


class EntityExtractor:
    def extract(self, transcript: str) -> ExtractedEntities:
        text = transcript or ""
        upper = text.upper()
        symbols = [symbol for symbol in SYMBOLS if re.search(rf"(?<![A-Z0-9]){re.escape(symbol)}(?![A-Z0-9])", upper)]
        timeframes = [tf for tf in TIMEFRAMES if re.search(rf"(?<![A-Z0-9]){re.escape(tf)}(?![A-Z0-9])", upper)]
        directions = _unique([normalized for alias, normalized in DIRECTION_ALIASES.items() if re.search(rf"(?<![\wА-ЯЁ]){re.escape(alias)}(?![\wА-ЯЁ])", upper)])
        indicators = [name for name in INDICATORS if re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", text, flags=re.IGNORECASE)]
        concepts = [name for name in CONCEPTS if name in indicators]
        levels = _unique_numbers([float(match.group(0).replace(",", ".")) for match in PRICE_RE.finditer(text)])
        return ExtractedEntities(symbols=symbols, timeframes=timeframes, directions=directions, indicators=indicators, concepts=concepts, levels=levels)


def _unique_numbers(values: list[float]) -> list[float]:
    seen: set[float] = set()
    result: list[float] = []
    for value in values:
        if value not in seen:
            seen.add(value); result.append(value)
    return result
