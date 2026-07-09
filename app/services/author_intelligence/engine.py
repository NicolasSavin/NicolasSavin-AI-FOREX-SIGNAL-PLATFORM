from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Callable

_DIRECTION_MAP = {"BUY": "BUY", "BULLISH": "BUY", "LONG": "BUY", "SELL": "SELL", "BEARISH": "SELL", "SHORT": "SELL", "WAIT": "WAIT", "HOLD": "WAIT", "NEUTRAL": "WAIT", "IGNORE": "WAIT"}


def _direction(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in _DIRECTION_MAP:
        return _DIRECTION_MAP[text]
    if "BUY" in text or "BULL" in text or "LONG" in text:
        return "BUY"
    if "SELL" in text or "BEAR" in text or "SHORT" in text:
        return "SELL"
    return "WAIT"


def _number(value: Any) -> float | None:
    try:
        if value in (None, "", "Unknown"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _avg(values: list[float]) -> int:
    return round(sum(values) / len(values)) if values else 0


def _date(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


class AuthorIntelligenceEngine:
    """Evaluates YouTube authors using the existing TV intelligence layers.

    Real market-outcome performance is intentionally isolated in ``outcome_provider`` for
    future stages. Until such data exists, success/failure/accuracy fields are explicit
    proxy metrics derived from committee verdicts, agreement and consistency rather than
    fabricated market outcomes.
    """

    def __init__(
        self,
        *,
        media_catalog_loader: Callable[[], list[dict[str, Any]]],
        review_payload_builder: Callable[[dict[str, Any]], dict[str, Any]],
        committee_builder: Callable[[str], dict[str, Any]],
        outcome_provider: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
    ) -> None:
        self.media_catalog_loader = media_catalog_loader
        self.review_payload_builder = review_payload_builder
        self.committee_builder = committee_builder
        self.outcome_provider = outcome_provider

    def build_all(self) -> list[dict[str, Any]]:
        reports = [self._build_author(author, videos) for author, videos in self._group_videos().items()]
        return sorted(reports, key=lambda row: (row["rating"], row["accuracy"], row["activity_score"]), reverse=True)

    def build_for_author(self, author: str) -> dict[str, Any]:
        wanted = author.strip().lower()
        for name, videos in self._group_videos().items():
            if name.lower() == wanted:
                return self._build_author(name, videos, include_opinions=True)
        raise ValueError("Author not found")

    def _group_videos(self) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for video in self.media_catalog_loader():
            author = str(video.get("author") or video.get("channel") or video.get("source_id") or "Unknown").strip() or "Unknown"
            grouped[author].append(video)
        return dict(grouped)

    def _opinion(self, video: dict[str, Any]) -> dict[str, Any]:
        payload = self.review_payload_builder(video)
        analysis = payload.get("analysis") or payload.get("ai_review") or {}
        knowledge = payload.get("knowledge") or payload.get("knowledge_context") or {}
        llm = payload.get("llm_review") or {}
        committee = self.committee_builder(str(video.get("id") or ""))
        direction = _direction(committee.get("decision") or analysis.get("direction") or knowledge.get("direction") or llm.get("direction"))
        confidence = int(_number(analysis.get("confidence") or llm.get("confidence") or knowledge.get("confidence") or committee.get("overall_score")) or 0)
        risk_text = str(committee.get("risk_level") or "MEDIUM").upper()
        risk_score = 80 if risk_text == "HIGH" else 50 if risk_text == "MEDIUM" else 25
        outcome = self.outcome_provider(video) if self.outcome_provider else None
        return {
            "video_id": video.get("id"),
            "title": video.get("title"),
            "author": video.get("author") or video.get("channel") or video.get("source_id") or "Unknown",
            "channel_id": video.get("channel_id") or video.get("source_id"),
            "published_at": video.get("published_at"),
            "symbol": knowledge.get("symbol") or analysis.get("symbol") or video.get("symbol"),
            "timeframe": video.get("timeframe") or knowledge.get("timeframe") or analysis.get("timeframe"),
            "direction": direction,
            "confidence": max(0, min(100, confidence)),
            "committee_score": int(_number(committee.get("overall_score")) or 0),
            "agreement_score": int(_number(committee.get("agreement_score") or knowledge.get("agreement_score") or llm.get("agreement_score")) or 0),
            "risk_score": risk_score,
            "institutional_bias": committee.get("institutional_bias") or "NEUTRAL",
            "committee_verdict": committee.get("committee_verdict") or "WATCH",
            "outcome": outcome,
        }

    def _build_author(self, author: str, videos: list[dict[str, Any]], *, include_opinions: bool = False) -> dict[str, Any]:
        opinions = [self._opinion(video) for video in videos]
        total = len(opinions)
        directions = Counter(item["direction"] for item in opinions)
        outcomes = [item.get("outcome") for item in opinions if item.get("outcome")]
        successful = sum(1 for item in outcomes if item.get("status") == "success")
        failed = sum(1 for item in outcomes if item.get("status") == "failed")
        neutral = total - successful - failed
        avg_conf = _avg([item["confidence"] for item in opinions])
        avg_committee = _avg([item["committee_score"] for item in opinions])
        avg_agreement = _avg([item["agreement_score"] for item in opinions])
        avg_risk = _avg([item["risk_score"] for item in opinions])
        signal_count = sum(1 for item in opinions if item["direction"] in {"BUY", "SELL"})
        dominant = max(directions.values(), default=0)
        consistency = round((dominant / total) * 100) if total else 0
        activity = min(100, total * 12 + signal_count * 5)
        institutional = _avg([item["committee_score"] * 0.6 + item["agreement_score"] * 0.4 for item in opinions])
        proxy_accuracy = _avg([item["committee_score"] * 0.55 + item["agreement_score"] * 0.30 + item["confidence"] * 0.15 for item in opinions])
        win_rate = round((successful / (successful + failed)) * 100) if successful + failed else None
        rating = _avg([proxy_accuracy * 0.35, institutional * 0.25, consistency * 0.20, activity * 0.20])
        latest = sorted(opinions, key=lambda x: _date(x.get("published_at")), reverse=True)[0] if opinions else {}
        favorite_symbols = Counter(str(item.get("symbol") or "Unknown") for item in opinions if item.get("symbol")).most_common(5)
        favorite_timeframes = Counter(str(item.get("timeframe") or "Unknown") for item in opinions if item.get("timeframe")).most_common(5)
        strengths, weaknesses = self._strengths(avg_conf, avg_committee, avg_agreement, consistency, activity)
        row = {
            "author": author,
            "channel_id": next((item.get("channel_id") for item in opinions if item.get("channel_id")), None),
            "videos": total,
            "signals": signal_count,
            "successful_signals": successful,
            "failed_signals": failed,
            "neutral_signals": neutral,
            "accuracy": proxy_accuracy,
            "accuracy_label": "proxy_committee_accuracy_until_real_market_outcomes_available",
            "win_rate": win_rate,
            "average_confidence": avg_conf,
            "average_committee_score": avg_committee,
            "average_agreement": avg_agreement,
            "signal_frequency": round((signal_count / total) * 100) if total else 0,
            "bullish_count": directions.get("BUY", 0),
            "bearish_count": directions.get("SELL", 0),
            "neutral_count": directions.get("WAIT", 0),
            "average_risk": avg_risk,
            "institutional_score": institutional,
            "consistency_score": consistency,
            "activity_score": activity,
            "rating": rating,
            "tier": self._tier(rating),
            "latest_opinion": latest.get("direction", "WAIT"),
            "latest_video": latest,
            "report": {
                "summary": f"{author}: рейтинг {rating}/100, tier {self._tier(rating)}, proxy accuracy {proxy_accuracy}% на базе {total} видео. Реальные market outcomes будут подключены отдельным outcome_provider.",
                "strengths": strengths,
                "weaknesses": weaknesses,
                "favorite_markets": [symbol for symbol, _ in favorite_symbols],
                "favorite_symbols": [symbol for symbol, _ in favorite_symbols],
                "favorite_timeframes": [tf for tf, _ in favorite_timeframes],
                "bullish_bias": round((directions.get("BUY", 0) / total) * 100) if total else 0,
                "bearish_bias": round((directions.get("SELL", 0) / total) * 100) if total else 0,
                "risk_profile": "HIGH" if avg_risk >= 66 else "MEDIUM" if avg_risk >= 40 else "LOW",
            },
        }
        if include_opinions:
            row["opinions"] = opinions
        return row

    def _strengths(self, confidence: int, committee: int, agreement: int, consistency: int, activity: int) -> tuple[list[str], list[str]]:
        strengths = []
        weaknesses = []
        for label, value in (("Высокая уверенность", confidence), ("Сильный Committee Score", committee), ("Хорошее согласие слоёв", agreement), ("Последовательный bias", consistency), ("Высокая активность", activity)):
            (strengths if value >= 70 else weaknesses if value < 45 else []).append(label)
        return strengths or ["Стабильная база наблюдений"], weaknesses or ["Критичных слабых зон не выявлено"]

    def _tier(self, rating: int) -> str:
        if rating >= 85:
            return "Elite"
        if rating >= 72:
            return "Professional"
        if rating >= 60:
            return "Advanced"
        if rating >= 45:
            return "Average"
        return "Watchlist"
