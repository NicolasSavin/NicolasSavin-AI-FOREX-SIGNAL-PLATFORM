from __future__ import annotations

from typing import Any, Callable

from app.services.media_identity import resolve_media_video

from app.services.investment_committee.models import CommitteeInput, InvestmentCommitteeReport
from app.services.investment_committee.provider import InvestmentCommitteeProvider
from app.services.investment_committee.rule_provider import RuleCommitteeProvider


class InvestmentCommitteeEngine:
    """Final analysis layer that composes metadata, transcript, Rule AI, Knowledge Layer and LLM Review."""

    def __init__(
        self,
        *,
        media_catalog_loader: Callable[[], list[dict[str, Any]]],
        review_payload_builder: Callable[[dict[str, Any]], dict[str, Any]],
        provider: InvestmentCommitteeProvider | None = None,
    ) -> None:
        self.media_catalog_loader = media_catalog_loader
        self.review_payload_builder = review_payload_builder
        self.provider = provider or RuleCommitteeProvider()

    def build_for_video(self, video_id: str) -> InvestmentCommitteeReport:
        video = resolve_media_video(video_id, self.media_catalog_loader())
        if not video:
            raise ValueError("TV video not found")
        warnings: list[str] = []
        try:
            review_payload = self.review_payload_builder(video)
        except Exception as exc:
            review_payload = {"video": video}
            warnings.append(f"review_payload_unavailable: {exc.__class__.__name__}: {exc}")
        for key in ("transcript", "knowledge_context", "knowledge", "llm_review"):
            value = review_payload.get(key)
            if isinstance(value, dict) and (value.get("error") or value.get("status") in {"unavailable", "error"}):
                warnings.append(f"{key}_warning: {value.get('error') or value.get('status')}")
        context = CommitteeInput(
            video=review_payload.get("video") or video,
            transcript=review_payload.get("transcript") or {},
            rule_ai_review=review_payload.get("analysis") or review_payload.get("ai_review") or {},
            knowledge_layer=review_payload.get("knowledge_context") or review_payload.get("knowledge") or {},
            llm_review=review_payload.get("llm_review") or {},
        )
        try:
            report = self.provider.evaluate(context)
        except Exception as exc:
            warnings.append(f"committee_provider_unavailable: {exc.__class__.__name__}: {exc}")
            report = InvestmentCommitteeReport(
                video=video,
                summary="Investment Committee работает в деградированном режиме: часть AI/knowledge слоёв недоступна.",
                overall_score=0,
                decision="WAIT",
                signal_quality="D",
                risk_level="HIGH",
                agreement_score=0,
                institutional_bias="NEUTRAL",
                pros=[],
                cons=["Недостаточно данных для полноценного committee review."],
                conflicts=[],
                committee_verdict="WATCH",
                provider="rule-committee-degraded",
            )
        if warnings:
            report.warnings.extend(warnings)
            report.degraded = True
        return report
