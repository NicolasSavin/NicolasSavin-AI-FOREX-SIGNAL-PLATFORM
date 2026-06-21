from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

ARCHIVE_FILE = Path("archive.json")
MIN_FACTOR_SAMPLE = 30
MAX_ADJUSTMENT = 8

_FACTORS: tuple[tuple[str, str], ...] = (
    ("setup_type", "setup_type"),
    ("symbol", "symbol"),
    ("narrative_source", "narrative_source"),
    ("news_risk", "news_risk"),
    ("sentiment_alignment", "sentiment_alignment"),
    ("options_bias", "options_bias"),
)
_UNAVAILABLE = {"", "unavailable", "unknown", "missing", "n/a", "na", "none", "null", "—"}


def _load_archive(path: Path = ARCHIVE_FILE) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
    except Exception:
        return []


def _num(value: Any) -> float | None:
    try:
        if value in (None, "", "—"):
            return None
        parsed = float(value)
        if math.isnan(parsed) or math.isinf(parsed):
            return None
        return parsed
    except Exception:
        return None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _norm(value: Any, *, upper: bool = False) -> str:
    text = _text(value)
    return text.upper() if upper else text.lower()


def _is_available(value: Any) -> bool:
    return _norm(value) not in _UNAVAILABLE


def _is_win(item: dict[str, Any]) -> bool | None:
    result = _norm(item.get("result") or item.get("status"))
    if result in {"tp", "tp_hit", "win", "won"}:
        return True
    if result in {"sl", "sl_hit", "loss", "lost"}:
        return False
    return None


def _snapshot(item: dict[str, Any]) -> dict[str, Any]:
    snap = item.get("learning_snapshot")
    if isinstance(snap, dict):
        merged = dict(item.get("idea") or {}) if isinstance(item.get("idea"), dict) else {}
        merged.update(snap)
        return merged
    if isinstance(item.get("idea"), dict):
        merged = dict(item["idea"])
        merged.update({k: v for k, v in item.items() if k not in {"idea"}})
        return merged
    return item


def score_bucket(score: Any) -> str:
    value = _num(score)
    if value is None:
        return "unavailable"
    if value < 60:
        return "50-60"
    if value < 70:
        return "60-70"
    if value < 80:
        return "70-80"
    if value < 90:
        return "80-90"
    return "90-100"


def build_learning_statistics(archive: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = archive if isinstance(archive, list) else _load_archive()
    groups: dict[str, dict[str, dict[str, int]]] = {name: defaultdict(lambda: {"total": 0, "wins": 0}) for name, _ in _FACTORS}
    groups["score_bucket"] = defaultdict(lambda: {"total": 0, "wins": 0})
    closed = 0
    wins_total = 0
    for item in rows:
        if not isinstance(item, dict):
            continue
        win = _is_win(item)
        if win is None:
            continue
        closed += 1
        wins_total += 1 if win else 0
        snap = _snapshot(item)
        for name, key in _FACTORS:
            value = snap.get(key)
            if name == "symbol":
                value = _norm(value, upper=True)
            else:
                value = _norm(value)
            if not _is_available(value):
                continue
            row = groups[name][value]
            row["total"] += 1
            row["wins"] += 1 if win else 0
        bucket = score_bucket(snap.get("score") or snap.get("prop_score"))
        if _is_available(bucket):
            row = groups["score_bucket"][bucket]
            row["total"] += 1
            row["wins"] += 1 if win else 0

    def finalize(group: dict[str, dict[str, int]]) -> dict[str, dict[str, Any]]:
        return {k: {**v, "winrate": round(v["wins"] / v["total"] * 100, 2) if v["total"] else 0.0} for k, v in group.items()}

    return {
        "closed_total": closed,
        "baseline_winrate": round(wins_total / closed * 100, 2) if closed else 0.0,
        **{name: finalize(group) for name, group in groups.items()},
    }


def learning_adjustment_for_idea(idea: dict[str, Any], archive: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if not isinstance(idea, dict):
        return {"learning_adjustment": 0, "learning_reasons": [], "learning_sample_size": 0}
    if bool(idea.get("fallback_active") or idea.get("is_fallback")) or _norm(idea.get("source")) == "fallback":
        return {"learning_adjustment": 0, "learning_reasons": ["learning skipped: fallback signal"], "learning_sample_size": 0}

    stats = build_learning_statistics(archive)
    baseline = float(stats.get("baseline_winrate") or 0.0)
    total_adjustment = 0
    reasons: list[str] = []
    sample_size = 0

    checks: list[tuple[str, str, str]] = []
    for name, key in _FACTORS:
        value = idea.get(key)
        normalized = _norm(value, upper=(name == "symbol")) if name == "symbol" else _norm(value)
        checks.append((name, normalized, key))
    checks.append(("score_bucket", score_bucket(idea.get("score") or idea.get("prop_score")), "score"))

    for factor, value, _key in checks:
        if not _is_available(value):
            continue
        row = (stats.get(factor) or {}).get(value)
        if not isinstance(row, dict):
            continue
        total = int(row.get("total") or 0)
        if total < MIN_FACTOR_SAMPLE:
            continue
        winrate = float(row.get("winrate") or 0.0)
        delta = winrate - baseline
        points = 0
        if delta >= 15:
            points = 2
        elif delta >= 7:
            points = 1
        elif delta <= -15:
            points = -2
        elif delta <= -7:
            points = -1
        if points:
            total_adjustment += points
            sample_size += total
            sign = "+" if points > 0 else ""
            reasons.append(f"{factor}={value}: winrate {winrate:.1f}% vs baseline {baseline:.1f}% ({total} closed, {sign}{points})")

    total_adjustment = max(-MAX_ADJUSTMENT, min(MAX_ADJUSTMENT, total_adjustment))
    return {"learning_adjustment": int(total_adjustment), "learning_reasons": reasons, "learning_sample_size": int(sample_size)}


def apply_learning_adjustment(idea: dict[str, Any], archive: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    enriched = dict(idea)
    result = learning_adjustment_for_idea(enriched, archive)
    base = _num(enriched.get("learning_score_base"))
    if base is None:
        base = _num(enriched.get("score") or enriched.get("prop_score") or enriched.get("confidence"))
    adjustment = int(result["learning_adjustment"] or 0)
    if base is not None:
        score = int(round(max(0, min(100, base + adjustment))))
        enriched["learning_score_base"] = int(round(base))
        for key in ("score", "confidence", "prop_score", "propScore", "propConfidence"):
            enriched[key] = score
        for nested_key in ("advisor_signal", "prop_signal_score"):
            nested = enriched.get(nested_key)
            if isinstance(nested, dict):
                nested["score"] = score
    enriched.update(result)
    return enriched
