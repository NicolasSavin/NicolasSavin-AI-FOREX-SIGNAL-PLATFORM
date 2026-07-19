from __future__ import annotations

import json
from typing import Any

SCHEMA = {
  "summary":"string", "market_overview":"string", "primary_symbol":"EURUSD|null", "symbols":["EURUSD"],
  "timeframe":"M15|M30|H1|H4|D1|W1|null", "direction":"BUY|SELL|WAIT|NEUTRAL",
  "confidence":"0-100|null", "confidence_label":"LOW|MEDIUM|HIGH|null", "confidence_score":"0.0-1.0|null",
  "entry":"number|null", "entry_zone":["number","number"], "stop_loss":"number|null", "take_profit":"number|null", "targets":["number"],
  "detected_levels":[{"type":"support|resistance|entry|stop_loss|take_profit|target", "price":"number", "symbol":"EURUSD"}],
  "trade_ideas":[{"symbol":"EURUSD", "timeframe":"H4|null", "direction":"BUY|SELL|WAIT", "confidence":"0-100|null", "confidence_label":"LOW|MEDIUM|HIGH|null", "confidence_score":"0.0-1.0|null", "entry":"number|null", "entry_zone":["number","number"], "stop_loss":"number|null", "take_profit":"number|null", "targets":["number"], "reason":"string", "source_evidence":["short quote"]}],
  "diagnostics":{"trade_signal_detected":"boolean", "structured_completeness":"0-100", "levels_detected":"boolean", "reason_missing_direction":"string|null", "reason_missing_levels":"string|null", "reason_missing_targets":"string|null"},
  "non_actionable_reason":"string", "reasoning":["string"], "risks":["string"], "opportunities":["string"], "contradictions":["string"], "institutional_view":"string", "news_impact":"string", "recommended_action":"BUY|SELL|WAIT|IGNORE"
}

class PromptBuilder:
    def build(self, context: dict[str, Any]) -> str:
        safe_context = json.dumps(context, ensure_ascii=False, default=str, indent=2)
        schema = json.dumps(SCHEMA, ensure_ascii=False, indent=2)
        return f"""You are a Senior Institutional FX Analyst and high-precision Trade Intelligence extractor for FXPilot. Your job is NOT to write a generic summary. Your priority is to extract every actionable trading setup stated in the source. Answer ONLY valid JSON. Return ONLY one valid JSON object. No Markdown.

Required JSON contract:
{schema}

Extraction priorities:
1. Detect explicit trading bias before summarizing. If the author explicitly says BUY, buy, long, покупка, лонг, direction MUST be BUY. If the author explicitly says SELL, sell, short, продажа, шорт, direction MUST be SELL. Use NEUTRAL only when no trading bias exists. Use WAIT only when the explicit recommendation is to wait/stand aside and there is no explicit BUY/SELL setup.
2. Create one trade_ideas item for EACH detected setup. If one review discusses EURUSD BUY and XAUUSD SELL, return two TradeIdea objects with their own symbol, direction, timeframe, confidence and levels.
3. Extract prices exactly as mentioned. Populate entry, entry_zone, stop_loss, take_profit and targets whenever explicitly stated. Never invent prices. Never invent levels, never use 0 as a placeholder, and never convert support/resistance into SL/TP unless the author names it as SL/TP/target.
4. Support entry zones such as "1.0850-1.0870", "buy zone", "entry area", "зона входа" as two numeric values in ascending order. Put single entry prices in entry.
5. Confidence must describe extraction/setup confidence. Return both confidence_label (LOW/MEDIUM/HIGH) and confidence_score (0.0-1.0). If the author gives a percent, map it directly; otherwise estimate from evidence completeness: HIGH for explicit direction+symbol+entry+SL+TP, MEDIUM for explicit direction+symbol with partial levels, LOW for directional call without levels.
6. Diagnostics are mandatory: trade_signal_detected, structured_completeness, levels_detected, reason_missing_direction, reason_missing_levels, reason_missing_targets. Put null reasons when not missing.
7. Keep backward-compatible top-level fields. Top-level direction should represent the primary/highest-confidence setup; trade_ideas is the complete list.
8. Summaries must be brief and secondary to structured extraction. Do not hide a BUY/SELL signal in summary while returning NEUTRAL.

Compact examples:
BUY with levels: {{"primary_symbol":"EURUSD","symbols":["EURUSD"],"timeframe":"H4","direction":"BUY","confidence":85,"confidence_label":"HIGH","confidence_score":0.85,"entry":null,"entry_zone":[1.085,1.087],"stop_loss":1.081,"take_profit":null,"targets":[1.092,1.098],"trade_ideas":[{{"symbol":"EURUSD","timeframe":"H4","direction":"BUY","confidence":85,"confidence_label":"HIGH","confidence_score":0.85,"entry":null,"entry_zone":[1.085,1.087],"stop_loss":1.081,"take_profit":null,"targets":[1.092,1.098],"reason":"explicit buy zone, stop and targets stated","source_evidence":["buy zone 1.0850-1.0870"]}}],"diagnostics":{{"trade_signal_detected":true,"structured_completeness":95,"levels_detected":true,"reason_missing_direction":null,"reason_missing_levels":null,"reason_missing_targets":null}}}}
SELL without levels: {{"primary_symbol":"XAUUSD","symbols":["XAUUSD"],"direction":"SELL","confidence_label":"LOW","confidence_score":0.35,"trade_ideas":[{{"symbol":"XAUUSD","direction":"SELL","confidence_label":"LOW","confidence_score":0.35,"entry":null,"entry_zone":[],"stop_loss":null,"take_profit":null,"targets":[],"reason":"explicit sell recommendation","source_evidence":["sell gold"]}}],"diagnostics":{{"trade_signal_detected":true,"structured_completeness":45,"levels_detected":false,"reason_missing_direction":null,"reason_missing_levels":"no explicit entry/SL/TP levels","reason_missing_targets":"no explicit targets"}}}}
No signal: {{"primary_symbol":null,"symbols":[],"direction":"NEUTRAL","confidence":null,"confidence_label":null,"confidence_score":null,"entry":null,"entry_zone":[],"stop_loss":null,"take_profit":null,"targets":[],"trade_ideas":[],"non_actionable_reason":"Only broad market commentary; no trading bias or setup.","diagnostics":{{"trade_signal_detected":false,"structured_completeness":20,"levels_detected":false,"reason_missing_direction":"no explicit trading bias","reason_missing_levels":"no explicit entry/SL/TP levels","reason_missing_targets":"no explicit targets"}}}}

Supplied context:
{safe_context}
"""
