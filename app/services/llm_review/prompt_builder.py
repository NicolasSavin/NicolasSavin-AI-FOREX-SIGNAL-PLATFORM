from __future__ import annotations

import json
from typing import Any


REQUIRED_JSON_FIELDS = [
    "summary", "direction", "confidence", "agreement_score", "entry", "stop_loss", "take_profit",
    "targets", "reasoning", "risks", "opportunities", "contradictions", "institutional_view",
    "news_impact", "market_bias", "recommended_action",
]


class PromptBuilder:
    def build(self, context: dict[str, Any]) -> str:
        safe_context = json.dumps(context, ensure_ascii=False, default=str, indent=2)
        fields = json.dumps(REQUIRED_JSON_FIELDS, ensure_ascii=False)
        return f"""You are a Senior Institutional FX Analyst.

Rules:
- Answer ONLY valid JSON. No markdown. No prose outside JSON.
- Use only the supplied context.
- Never invent prices.
- Never invent symbols.
- If information is missing, write "Unknown".
- Clearly distinguish proxy metrics from real market metrics.
- Provide a professional trading review, not financial advice.

Return one JSON object with exactly these business fields: {fields}.
Use arrays for targets, reasoning, risks, opportunities, contradictions.
confidence and agreement_score must be integers from 0 to 100.

Supplied context:
{safe_context}
"""
