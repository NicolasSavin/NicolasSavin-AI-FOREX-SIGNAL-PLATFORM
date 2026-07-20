from __future__ import annotations
import os
from datetime import datetime, timezone
from typing import Any

def clamp(v: Any, default: float = 0) -> float:
    try: n = float(v)
    except Exception: n = default
    return round(max(0.0, min(100.0, n)), 2)

def env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw: return default
    val = float(raw)
    if val < 0: raise ValueError(f"{name} must be non-negative")
    return val

def now_iso() -> str: return datetime.now(timezone.utc).isoformat()

def parse_dt(value: Any) -> datetime | None:
    if not value: return None
    try: return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception: return None

def freshness(updated_at: Any, expiry_hours: float) -> float:
    dt = parse_dt(updated_at)
    if not dt: return 0
    if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
    age_h = max(0, (datetime.now(timezone.utc) - dt).total_seconds() / 3600)
    return clamp(100 * max(0, 1 - (age_h / max(0.01, expiry_hours))))

def normalize_direction(value: Any) -> str:
    v = str(value or "").strip().upper().replace("_", " ")
    if v in {"BUY", "BULLISH", "LONG", "STRONG BUY", "STRONG_BUY"}: return "BUY"
    if v in {"SELL", "BEARISH", "SHORT", "STRONG SELL", "STRONG_SELL"}: return "SELL"
    if v in {"WAIT", "HOLD", "STANDBY"}: return "WAIT"
    if v in {"NEUTRAL", "IGNORE", "REJECT", "NONE"}: return "NEUTRAL"
    if v in {"MIXED", "CONFLICT"}: return "MIXED"
    return "NO_DATA"
