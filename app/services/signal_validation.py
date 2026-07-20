from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Protocol

from app.services.storage_paths import DATA_DIR, atomic_write_json

VALIDATION_PATH = DATA_DIR / "signal_validations.json"
VALIDATION_AUDIT_PATH = DATA_DIR / "signal_validation_audit.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _num(value: Any) -> float | None:
    try:
        if value in (None, "", "Unknown"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _direction(value: Any) -> str:
    text = str(value or "").upper().strip()
    if text in {"BUY", "LONG", "BULLISH"} or "BUY" in text or "LONG" in text:
        return "BUY"
    if text in {"SELL", "SHORT", "BEARISH"} or "SELL" in text or "SHORT" in text:
        return "SELL"
    return "UNKNOWN"


def _symbol(value: Any) -> str:
    return str(value or "MARKET").replace("/", "").replace(" ", "").upper()


@dataclass
class ValidationResult:
    id: str
    signal_id: str
    symbol: str
    direction: str
    status: str = "pending"
    outcome: str = "UNKNOWN"
    rr: float | None = None
    profit_points: float | None = None
    loss_points: float | None = None
    holding_time: float | None = None
    entry_time: str | None = None
    exit_time: str | None = None
    max_favorable_excursion: float | None = None
    max_adverse_excursion: float | None = None
    author: str | None = None
    timeframe: str | None = None
    published_at: str | None = None
    entry: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    targets: list[float] = field(default_factory=list)
    validation_events: list[dict[str, Any]] = field(default_factory=list)
    provider: str | None = None
    data_status: str = "unavailable"
    warning_ru: str | None = None
    updated_at: str = field(default_factory=_now)

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


class HistoricalMarketDataProvider(Protocol):
    def load_ohlc(self, symbol: str, timeframe: str, start: datetime, end: datetime, limit: int = 500) -> dict[str, Any]: ...


class ExistingMarketDataValidationProvider:
    """Adapter over the existing candle function; it never fabricates missing data."""

    def __init__(self, candle_loader: Any) -> None:
        self.candle_loader = candle_loader

    def load_ohlc(self, symbol: str, timeframe: str, start: datetime, end: datetime, limit: int = 500) -> dict[str, Any]:
        payload = self.candle_loader(symbol, timeframe, limit)
        candles = payload.get("candles") if isinstance(payload, dict) else []
        filtered = []
        for c in candles or []:
            ts = _dt(c.get("time") or c.get("datetime") or c.get("timestamp") or c.get("date"))
            if ts is None or (start <= ts <= end):
                filtered.append(c)
        return {**(payload if isinstance(payload, dict) else {}), "candles": filtered}


class SignalValidationEngine:
    def __init__(self, provider: HistoricalMarketDataProvider, *, storage_path: Path = VALIDATION_PATH, audit_path: Path = VALIDATION_AUDIT_PATH) -> None:
        self.provider = provider
        self.storage_path = storage_path
        self.audit_path = audit_path

    def validate_signal(self, idea: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
        result = self._existing_or_new(idea)
        if result.status in {"validated", "expired", "invalid"} and not force:
            return result.model_dump()
        self._event(result, "running", "Запущена объективная проверка сигнала по историческим OHLC.")
        result.status = "running"
        entry = _num(idea.get("entry") or idea.get("entry_price"))
        zone = idea.get("entry_zone") if isinstance(idea.get("entry_zone"), (list, tuple)) else None
        if entry is None and zone:
            vals = [_num(v) for v in zone if _num(v) is not None]
            entry = sum(vals) / len(vals) if vals else None
        sl = _num(idea.get("stop_loss") or idea.get("sl"))
        targets = idea.get("targets") if isinstance(idea.get("targets"), list) else []
        tps = [_num(v) for v in targets if _num(v) is not None]
        tp = _num(idea.get("take_profit") or idea.get("tp") or idea.get("target"))
        if tp is not None and tp not in tps:
            tps.insert(0, tp)
        result.entry, result.stop_loss, result.take_profit, result.targets = entry, sl, tp, tps
        if result.direction not in {"BUY", "SELL"} or entry is None or sl is None or not tps:
            result.status = "invalid"; result.outcome = "UNKNOWN"; result.warning_ru = "Недостаточно уровней entry/SL/TP для валидации без подмены данных."
            self._event(result, "invalid", result.warning_ru); self._persist(result); return result.model_dump()
        published = _dt(idea.get("published_at")) or datetime.now(timezone.utc) - timedelta(days=2)
        expiry = published + self._expiry_delta(str(idea.get("timeframe") or "M15"))
        data = self.provider.load_ohlc(result.symbol, result.timeframe or "M15", published, expiry, 500)
        candles = data.get("candles") or []
        result.provider = data.get("provider") or data.get("source")
        result.data_status = "real" if candles and not data.get("error") else "unavailable"
        if not candles:
            result.status = "pending" if expiry > datetime.now(timezone.utc) else "expired"
            result.outcome = "UNKNOWN" if result.status == "pending" else "NOT_TRIGGERED"
            result.warning_ru = "Исторические свечи недоступны; результат не подменяется proxy-метрикой."
            self._event(result, result.status, result.warning_ru); self._persist(result); return result.model_dump()
        self._simulate(result, candles, entry, sl, tps, expiry)
        self._persist(result)
        return result.model_dump()

    def validate_many(self, ideas: list[dict[str, Any]], *, limit: int = 50) -> dict[str, Any]:
        items = [self.validate_signal(i) for i in ideas[:limit]]
        return {"items": items, "stats": self.stats()}

    def all(self) -> list[dict[str, Any]]:
        return self._read().get("items", [])

    def get(self, validation_id: str) -> dict[str, Any] | None:
        return next((i for i in self.all() if str(i.get("id")) == validation_id or str(i.get("signal_id")) == validation_id), None)

    def stats(self) -> dict[str, Any]:
        items = self.all(); counts = defaultdict(int)
        for i in items: counts[i.get("status") or "unknown"] += 1
        return {"total": len(items), "pending": counts["pending"], "running": counts["running"], "validated": counts["validated"], "failed": counts["invalid"], "expired": counts["expired"], "latest": sorted(items, key=lambda x: x.get("updated_at") or "", reverse=True)[:20]}

    def author_metrics(self) -> dict[str, Any]:
        return {"authors": self._group_metrics("author")}

    def symbol_metrics(self) -> dict[str, Any]:
        rows = self._group_metrics("symbol")
        for r in rows:
            r["best_authors"] = self._rank_authors(r["key"], True); r["worst_authors"] = self._rank_authors(r["key"], False)
        return {"symbols": rows}

    def debug(self) -> dict[str, Any]:
        return {"storage_path": str(self.storage_path), "audit_path": str(self.audit_path), "provider": self.provider.__class__.__name__, "data_label": "real_historical_ohlc_only_no_proxy_substitution"}

    def historical_author_weight(self, author: str) -> dict[str, Any]:
        rows = [r for r in self._group_metrics("author") if str(r["key"]).lower() == str(author).lower()]
        acc = rows[0]["win_rate"] if rows else 50
        return {"trust_score": max(1, min(100, acc)), "weight_label": "validated_historical_performance"}

    def _simulate(self, r: ValidationResult, candles: list[dict[str, Any]], entry: float, sl: float, tps: list[float], expiry: datetime) -> None:
        entered = False; entry_dt = None; mfe = 0.0; mae = 0.0
        primary_tp = tps[0]
        for c in candles:
            ts = _dt(c.get("time") or c.get("datetime") or c.get("timestamp") or c.get("date")) or expiry
            high, low = _num(c.get("high")), _num(c.get("low"))
            if high is None or low is None: continue
            if not entered:
                entered = low <= entry if r.direction == "BUY" else high >= entry
                if entered: entry_dt = ts; r.entry_time = ts.isoformat(); self._event(r, "entry", "Вход найден в исторической свече.")
                else: continue
            favorable = high - entry if r.direction == "BUY" else entry - low
            adverse = entry - low if r.direction == "BUY" else high - entry
            mfe, mae = max(mfe, favorable), max(mae, adverse)
            hit_sl = low <= sl if r.direction == "BUY" else high >= sl
            hit_tp = high >= primary_tp if r.direction == "BUY" else low <= primary_tp
            if hit_tp or hit_sl:
                r.status = "validated"; r.outcome = "TP" if hit_tp else "SL"; r.exit_time = ts.isoformat(); break
            if ts >= expiry:
                r.status = "expired"; r.outcome = "UNKNOWN"; r.exit_time = ts.isoformat(); break
        if not entered:
            r.status = "expired"; r.outcome = "NOT_TRIGGERED"; r.exit_time = expiry.isoformat()
        elif r.status == "running":
            r.status = "expired"; r.outcome = "UNKNOWN"; r.exit_time = expiry.isoformat()
        r.max_favorable_excursion = round(mfe, 6); r.max_adverse_excursion = round(mae, 6)
        risk = abs(entry - sl); reward = abs(primary_tp - entry)
        r.loss_points = round(risk, 6); r.profit_points = round(reward if r.outcome == "TP" else -risk if r.outcome == "SL" else mfe, 6)
        r.rr = round(reward / risk, 3) if risk else None
        if entry_dt and r.exit_time:
            r.holding_time = round(((_dt(r.exit_time) or entry_dt) - entry_dt).total_seconds() / 3600, 3)
        self._event(r, r.status, f"Итог: {r.outcome}")

    def _group_metrics(self, key: str) -> list[dict[str, Any]]:
        groups = defaultdict(list)
        for i in self.all(): groups[i.get(key) or "Unknown"].append(i)
        rows=[]
        for k, arr in groups.items():
            wins=sum(1 for x in arr if x.get("outcome")=="TP"); losses=sum(1 for x in arr if x.get("outcome")=="SL"); decided=wins+losses
            streak = self._streak(arr)
            rows.append({"key": k, "signals": len(arr), "wins": wins, "losses": losses, "win_rate": round(wins/decided*100) if decided else 0, "loss_rate": round(losses/decided*100) if decided else 0, "average_rr": round(mean([x["rr"] for x in arr if x.get("rr") is not None]),3) if any(x.get("rr") is not None for x in arr) else 0, "average_holding_time": round(mean([x["holding_time"] for x in arr if x.get("holding_time") is not None]),3) if any(x.get("holding_time") is not None for x in arr) else 0, "max_drawdown": min([x.get("profit_points") or 0 for x in arr], default=0), "current_streak": streak[0], "best_streak": streak[1], "signal_frequency": len(arr), "accuracy": round(wins/decided*100) if decided else 0})
        return sorted(rows, key=lambda x: (x["win_rate"], x["signals"]), reverse=True)

    def _rank_authors(self, symbol: str, best: bool) -> list[dict[str, Any]]:
        rows = [i for i in self.all() if i.get("symbol") == symbol]
        old = self.storage_path; return sorted(self._group_metrics_for(rows, "author"), key=lambda x: x["win_rate"], reverse=best)[:5]

    def _group_metrics_for(self, rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
        saved = self.all; self.all = lambda: rows  # type: ignore[method-assign]
        try: return self._group_metrics(key)
        finally: self.all = saved  # type: ignore[method-assign]

    def _streak(self, arr: list[dict[str, Any]]) -> tuple[int, int]:
        cur=best=0; last=None
        for x in sorted(arr, key=lambda v: v.get("exit_time") or v.get("updated_at") or ""):
            val = 1 if x.get("outcome")=="TP" else -1 if x.get("outcome")=="SL" else 0
            if val == 0: continue
            cur = cur + val if (last == val or last is None) else val; last = val; best = max(best, cur)
        return cur, best

    def _existing_or_new(self, idea: dict[str, Any]) -> ValidationResult:
        sid = str(idea.get("id") or idea.get("video_id") or self._hash(idea))
        existing = self.get(sid)
        if existing: return ValidationResult(**{k: existing.get(k) for k in ValidationResult.__dataclass_fields__})
        return ValidationResult(id=f"val_{sid}", signal_id=sid, symbol=_symbol(idea.get("symbol") or idea.get("pair") or idea.get("instrument")), direction=_direction(idea.get("direction") or idea.get("signal") or idea.get("action")), author=idea.get("author") or idea.get("source_id"), timeframe=str(idea.get("timeframe") or idea.get("tf") or "M15").upper(), published_at=idea.get("published_at"))

    def _hash(self, idea: dict[str, Any]) -> str:
        raw = "|".join(str(idea.get(k) or "") for k in ("symbol","direction","entry","stop_loss","take_profit","published_at"))
        return hashlib.sha1(raw.encode()).hexdigest()[:16]

    def _expiry_delta(self, tf: str) -> timedelta:
        return timedelta(hours=24 if tf.upper().startswith("M") else 24*7 if tf.upper().startswith("H") else 24*30)

    def _event(self, r: ValidationResult, kind: str, message: str) -> None:
        r.updated_at = _now(); r.validation_events.append({"at": r.updated_at, "event": kind, "message_ru": message})

    def _read(self) -> dict[str, Any]:
        try:
            import json
            return json.loads(self.storage_path.read_text(encoding="utf-8")) if self.storage_path.exists() else {"items": []}
        except Exception:
            return {"items": []}

    def _persist(self, r: ValidationResult) -> None:
        payload = self._read(); items = [i for i in payload.get("items", []) if i.get("id") != r.id and i.get("signal_id") != r.signal_id]
        items.append(r.model_dump()); atomic_write_json(self.storage_path, {"updated_at": _now(), "items": items})
        atomic_write_json(self.audit_path, {"updated_at": _now(), "events": [e for i in items for e in (i.get("validation_events") or [])][-1000:]})
