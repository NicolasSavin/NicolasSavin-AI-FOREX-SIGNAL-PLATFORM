from __future__ import annotations

import json
from typing import Any

SCHEMA = {
  "summary":"string", "market_overview":"string", "symbols":["EURUSD","XAUUSD"], "primary_symbol":"EURUSD|null",
  "timeframe":"M1|M5|M15|M30|H1|H4|D1|W1|null", "direction":"BUY|SELL|WAIT|NEUTRAL", "confidence":0,
  "entry":0.0, "entry_zone":[0.0,0.0], "stop_loss":0.0, "take_profit":0.0, "targets":[0.0],
  "detected_levels":[{"type":"support|resistance|entry|stop_loss|take_profit|target", "price":0.0, "symbol":"EURUSD"}],
  "trade_ideas":[{"symbol":"EURUSD", "timeframe":"H4", "direction":"BUY|SELL|WAIT|NEUTRAL", "entry":0.0, "entry_zone":[0.0,0.0], "stop_loss":0.0, "take_profit":0.0, "targets":[0.0], "confidence":0, "reasoning":"string"}],
  "risks":["string"], "opportunities":["string"], "contradictions":["string"], "institutional_view":"string", "news_impact":"string", "recommended_action":"BUY|SELL|WAIT|IGNORE"
}

class PromptBuilder:
    def build(self, context: dict[str, Any]) -> str:
        safe_context = json.dumps(context, ensure_ascii=False, default=str, indent=2)
        schema = json.dumps(SCHEMA, ensure_ascii=False, indent=2)
        return f"""You are a Senior Institutional FX Analyst extracting structured trading entities from FXPilot TV review context.

Rules:
- Answer ONLY valid JSON. No Markdown. No explanations outside JSON.
- Analyze only facts found in supplied video metadata, transcript and FXPilot context.
- Never invent symbols. Never invent prices. Never invent an instrument, timeframe or price. Missing values must be null or empty arrays.
- Distinguish broad market discussion from an actionable trade idea.
- Extract every explicitly mentioned symbol and create separate trade_ideas for several actionable instruments.
- If no exact instrument exists, use primary_symbol = null, not MARKET.
- If no actionable forecast exists, use direction = NEUTRAL and recommended_action = IGNORE.
- Normalize confidence to 0-100 integer.
- Normalize direction to BUY, SELL, WAIT or NEUTRAL.
- Normalize timeframe to M1, M5, M15, M30, H1, H4, D1, W1 or null.
- Map aliases: gold→XAUUSD; euro dollar→EURUSD; pound dollar→GBPUSD; dollar yen→USDJPY; S&P 500/SP500→SPX; Bitcoin→BTCUSD; Ethereum→ETHUSD; Brent→UKOIL.
- Clearly label proxy metrics vs real market metrics in text fields when relevant.

Return exactly one JSON object compatible with this schema (use null for missing numeric/string fields):
{schema}

Supplied context:
{safe_context}
"""
