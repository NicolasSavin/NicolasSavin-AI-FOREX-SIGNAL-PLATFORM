from __future__ import annotations

import json
from typing import Any

SCHEMA = {
  "summary":"string", "market_overview":"string", "primary_symbol":"EURUSD|null", "symbols":["EURUSD"],
  "timeframe":"M15|M30|H1|H4|D1|W1|null", "direction":"BUY|SELL|WAIT|NEUTRAL", "confidence":"0-100|null",
  "entry":"number|null", "entry_zone":["number","number"], "stop_loss":"number|null", "take_profit":"number|null", "targets":["number"],
  "detected_levels":[{"type":"support|resistance|entry|stop_loss|take_profit|target", "price":"number", "symbol":"EURUSD"}],
  "trade_ideas":[{"symbol":"EURUSD", "timeframe":"H4|null", "direction":"BUY|SELL|WAIT", "confidence":"0-100|null", "entry":"number|null", "entry_zone":["number","number"], "stop_loss":"number|null", "take_profit":"number|null", "targets":["number"], "reason":"string", "source_evidence":["short quote"]}],
  "non_actionable_reason":"string", "reasoning":["string"], "risks":["string"], "opportunities":["string"], "contradictions":["string"], "institutional_view":"string", "news_impact":"string", "recommended_action":"BUY|SELL|WAIT|IGNORE"
}

class PromptBuilder:
    def build(self, context: dict[str, Any]) -> str:
        safe_context = json.dumps(context, ensure_ascii=False, default=str, indent=2)
        schema = json.dumps(SCHEMA, ensure_ascii=False, indent=2)
        return f"""You are a Senior Institutional FX Analyst extracting structured trading entities for FXPilot. Answer ONLY valid JSON. Return ONLY one valid JSON object. No Markdown, no prose outside JSON.

Contract and anti-fabrication rules:
- Match this exact JSON schema; use null for absent scalar values and [] for absent arrays: {schema}
- Extract only instruments explicitly discussed. Never output MARKET/UNKNOWN/N/A as a symbol.
- Normalize aliases: gold/xau usd→XAUUSD; euro dollar/eur usd→EURUSD; bitcoin/btc usd→BTCUSD; brent→UKOIL; sp500/S&P 500→SPX; nasdaq→NAS100.
- Timeframes only M15,M30,H1,H4,D1,W1; normalize 15m/m15, 30m/m30, 1h/h1/hourly, 4h/h4, daily/day/d1, weekly/w1. Do not infer timeframe from video duration.
- Direction: WAIT means the author explicitly recommends waiting; NEUTRAL means no reliable directional position. Do not convert general bullish/bearish commentary into BUY/SELL unless there is an explicit recommendation, setup/plan, trade level, stop/target, or clearly stated directional position.
- An actionable trade idea is a concrete BUY/SELL/WAIT plan or recommendation. Broad market commentary is non-actionable.
- Never invent prices. Do not invent entry, entry_zone, stop_loss, take_profit, targets, detected_levels, prices, symbols, timeframe, or confidence. Preserve decimal prices as numbers. Never use 0 as a placeholder.
- Confidence is confidence in the extracted idea, not the author. Use null if not stated.
- One video may contain multiple trade_ideas. Directional SELL/BUY can be valid without prices if explicitly recommended.
- detected_levels are evidence only; do not turn them into stops/targets automatically.

Compact examples:
A BUY: {{"primary_symbol":"EURUSD","symbols":["EURUSD"],"timeframe":"H4","direction":"BUY","confidence":75,"entry":null,"entry_zone":[1.0850,1.0870],"stop_loss":1.0810,"take_profit":null,"targets":[1.0920,1.0980],"trade_ideas":[{{"symbol":"EURUSD","timeframe":"H4","direction":"BUY","confidence":75,"entry":null,"entry_zone":[1.0850,1.0870],"stop_loss":1.0810,"take_profit":null,"targets":[1.0920,1.0980],"reason":"buy zone stated","source_evidence":["buy zone 1.0850-1.0870"]}}]}}
B SELL no exact entry: {{"primary_symbol":"XAUUSD","symbols":["XAUUSD"],"timeframe":null,"direction":"SELL","confidence":null,"entry":null,"entry_zone":[],"stop_loss":null,"take_profit":null,"targets":[],"trade_ideas":[{{"symbol":"XAUUSD","timeframe":null,"direction":"SELL","confidence":null,"entry":null,"entry_zone":[],"stop_loss":null,"take_profit":null,"targets":[],"reason":"explicit sell recommendation","source_evidence":["recommend selling gold"]}}]}}
C Commentary: {{"primary_symbol":null,"symbols":[],"timeframe":null,"direction":"NEUTRAL","confidence":null,"entry":null,"entry_zone":[],"stop_loss":null,"take_profit":null,"targets":[],"trade_ideas":[],"non_actionable_reason":"Only broad market discussion; no trade plan."}}
D Multiple: {{"primary_symbol":"BTCUSD","symbols":["EURUSD","BTCUSD"],"direction":"BUY","trade_ideas":[{{"symbol":"EURUSD","timeframe":"H1","direction":"SELL","confidence":60,"entry":null,"entry_zone":[],"stop_loss":null,"take_profit":null,"targets":[],"reason":"separate EURUSD idea","source_evidence":[]}},{{"symbol":"BTCUSD","timeframe":"D1","direction":"BUY","confidence":80,"entry":null,"entry_zone":[],"stop_loss":null,"take_profit":null,"targets":[],"reason":"higher confidence BTC idea","source_evidence":[]}}]}}

Supplied context:
{safe_context}
"""
