from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Callable

_DIRECTION_MAP = {"BUY": "BUY", "BULLISH": "BUY", "LONG": "BUY", "SELL": "SELL", "BEARISH": "SELL", "SHORT": "SELL", "WAIT": "WAIT", "HOLD": "WAIT", "NEUTRAL": "WAIT", "IGNORE": "WAIT"}


def _norm_symbol(value: Any) -> str:
    return str(value or "").replace("/", "").replace(" ", "").upper()


def _norm_timeframe(value: Any) -> str:
    return str(value or "").strip().upper()


def _direction(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in _DIRECTION_MAP:
        return _DIRECTION_MAP[text]
    if "BUY" in text or "BULL" in text or "LONG" in text:
        return "BUY"
    if "SELL" in text or "BEAR" in text or "SHORT" in text:
        return "SELL"
    return "WAIT"


def _percent(part: int, total: int) -> int:
    return round((part / total) * 100) if total else 0


def _number(value: Any) -> float | None:
    try:
        if value in (None, "", "Unknown"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        raw = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(raw)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


class ConsensusEngine:
    """Aggregates all imported media opinions for one symbol/timeframe without replacing existing APIs."""

    def __init__(
        self,
        *,
        media_catalog_loader: Callable[[], list[dict[str, Any]]],
        review_payload_builder: Callable[[dict[str, Any]], dict[str, Any]],
        committee_builder: Callable[[str], dict[str, Any]],
    ) -> None:
        self.media_catalog_loader = media_catalog_loader
        self.review_payload_builder = review_payload_builder
        self.committee_builder = committee_builder

    def build(self, symbol: str, timeframe: str | None = None, *, date_from: str | None = None, date_to: str | None = None) -> dict[str, Any]:
        wanted_symbol = _norm_symbol(symbol)
        wanted_timeframe = _norm_timeframe(timeframe)
        start = _date(date_from)
        end = _date(date_to)
        videos = [v for v in self.media_catalog_loader() if _norm_symbol(v.get("symbol")) == wanted_symbol]
        if wanted_timeframe:
            videos = [v for v in videos if _norm_timeframe(v.get("timeframe")) == wanted_timeframe]
        if start or end:
            filtered = []
            for video in videos:
                published = _date(video.get("published_at"))
                if start and published and published < start:
                    continue
                if end and published and published > end:
                    continue
                filtered.append(video)
            videos = filtered

        opinions = [self._opinion(video) for video in videos]
        counts = Counter(item["direction"] for item in opinions)
        total = len(opinions)
        agreement_count = max((counts.get("BUY", 0), counts.get("SELL", 0), counts.get("WAIT", 0)), default=0)
        overall = "WAIT"
        if total and agreement_count:
            winners = [d for d in ("BUY", "SELL", "WAIT") if counts.get(d, 0) == agreement_count]
            overall = winners[0] if len(winners) == 1 else "WAIT"
        avg_conf = round(sum(item["confidence"] for item in opinions) / total) if total else 0
        avg_committee = round(sum(item["committee_score"] for item in opinions) / total) if total else 0
        agreement = _percent(agreement_count, total)
        return {
            "symbol": wanted_symbol,
            "timeframe": wanted_timeframe or "ALL",
            "date_window": {"from": date_from, "to": date_to},
            "overall_direction": overall,
            "consensus_strength": self._strength(agreement),
            "agreement_percent": agreement,
            "average_confidence": avg_conf,
            "average_committee_score": avg_committee,
            "bullish_count": counts.get("BUY", 0),
            "bearish_count": counts.get("SELL", 0),
            "neutral_count": counts.get("WAIT", 0),
            "bullish_percent": _percent(counts.get("BUY", 0), total),
            "bearish_percent": _percent(counts.get("SELL", 0), total),
            "neutral_percent": _percent(counts.get("WAIT", 0), total),
            "opinions": opinions,
            "top_authors": self._leaderboard(opinions),
            "disagreements": self._disagreements(counts),
            "market_summary": self._summary(wanted_symbol, wanted_timeframe or "все TF", overall, agreement, counts),
        }

    def _opinion(self, video: dict[str, Any]) -> dict[str, Any]:
        payload = self.review_payload_builder(video)
        analysis = payload.get("analysis") or payload.get("ai_review") or {}
        knowledge = payload.get("knowledge") or payload.get("knowledge_context") or {}
        committee = self.committee_builder(str(video.get("id") or ""))
        direction = _direction(committee.get("decision") or analysis.get("direction") or knowledge.get("direction"))
        targets = analysis.get("targets") or ([analysis.get("tp")] if analysis.get("tp") is not None else [])
        return {
            "video_id": video.get("id"),
            "title": video.get("title"),
            "author": video.get("author") or video.get("source_id") or "Unknown",
            "published_at": video.get("published_at"),
            "timeframe": video.get("timeframe"),
            "direction": direction,
            "confidence": int(_number(analysis.get("confidence") or committee.get("overall_score")) or 0),
            "entry": _number(analysis.get("entry")),
            "stop": _number(analysis.get("sl") or analysis.get("stop_loss")),
            "targets": [_number(item) for item in targets if _number(item) is not None],
            "committee_score": int(_number(committee.get("overall_score")) or 0),
            "committee_verdict": committee.get("committee_verdict") or "WATCH",
            "agreement_score": int(_number(committee.get("agreement_score") or knowledge.get("agreement_score")) or 0),
        }

    def _leaderboard(self, opinions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in opinions:
            grouped[item["author"]].append(item)
        rows = []
        for author, items in grouped.items():
            latest = sorted(items, key=lambda x: str(x.get("published_at") or ""), reverse=True)[0]
            rows.append({"author": author, "historical_accuracy": None, "historical_accuracy_label": "placeholder", "current_confidence": round(sum(i["confidence"] for i in items) / len(items)), "committee_score": round(sum(i["committee_score"] for i in items) / len(items)), "latest_opinion": latest["direction"]})
        return sorted(rows, key=lambda x: (x["committee_score"], x["current_confidence"]), reverse=True)

    def _disagreements(self, counts: Counter) -> list[str]:
        parts = [f"{counts.get('BUY',0)} authors BUY", f"{counts.get('SELL',0)} authors SELL", f"{counts.get('WAIT',0)} WAIT"]
        active = sum(1 for key in ("BUY", "SELL", "WAIT") if counts.get(key, 0))
        return [", ".join(parts)] if active > 1 else []

    def _strength(self, agreement: int) -> str:
        if agreement >= 75: return "STRONG"
        if agreement >= 55: return "MODERATE"
        if agreement > 0: return "WEAK"
        return "NO_DATA"

    def _summary(self, symbol: str, timeframe: str, overall: str, agreement: int, counts: Counter) -> str:
        if not sum(counts.values()):
            return f"Для {symbol} {timeframe} пока нет импортированных видео с анализом."
        return f"Consensus по {symbol} {timeframe}: {overall}, согласие {agreement}%; BUY {counts.get('BUY',0)}, SELL {counts.get('SELL',0)}, WAIT {counts.get('WAIT',0)}."
