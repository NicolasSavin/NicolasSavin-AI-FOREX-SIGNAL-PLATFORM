from __future__ import annotations

import json
import os
import time
from typing import Any

from openai import OpenAI

from app.services.llm_config import LLMConfig, resolve_llm_config
from app.services.llm_review.models import LLMReview
from app.services.llm_review.prompt_builder import PromptBuilder


class OpenAIReviewProvider:
    provider_name = "openai"

    def __init__(self, *, api_key: str | None = None, model: str | None = None, base_url: str | None = None, timeout: float | None = None, retries: int = 2, prompt_builder: PromptBuilder | None = None, config: LLMConfig | None = None) -> None:
        resolved = config or resolve_llm_config()
        self.api_key = (api_key if api_key is not None else resolved.api_key).strip()
        self.base_url = base_url if base_url is not None else resolved.base_url
        self.model = model or resolved.model
        self.provider_name = resolved.provider
        self.timeout = timeout if timeout is not None else float(os.getenv("FXPILOT_LLM_TIMEOUT", "30"))
        self.retries = max(0, int(retries))
        self.prompt_builder = prompt_builder or PromptBuilder()

    def _default_headers(self) -> dict[str, str] | None:
        if self.provider_name != "openrouter":
            return None
        return {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://fxpilot.ru",
            "X-Title": "FXPilot",
        }

    def generate_review(self, context: dict[str, Any]) -> LLMReview:
        if not self.api_key.strip():
            raise RuntimeError(f"LLM configuration error: API key is required for provider {self.provider_name}")
        prompt = self.prompt_builder.build(context)
        client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            default_headers=self._default_headers(),
            timeout=self.timeout,
            max_retries=self.retries,
        )
        started = time.perf_counter()
        response = client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        content = response.choices[0].message.content or "{}"
        payload = json.loads(content)
        tokens = getattr(getattr(response, "usage", None), "total_tokens", 0) or 0
        return LLMReview.from_payload(payload, provider=f"{self.provider_name}:{self.model}", tokens_used=int(tokens), latency_ms=latency_ms)
