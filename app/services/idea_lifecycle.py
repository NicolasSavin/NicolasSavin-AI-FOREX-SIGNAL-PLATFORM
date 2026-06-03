from __future__ import annotations

import json
import math
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ACTIVE_FILE = Path("active_ideas.json")
ARCHIVE_FILE = Path("archive.json")


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(path: Path, fallback: Any) -> Any:
    try:
        if not path.exists():
            return fallback
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if data is not None else fallback
    except Exception:
        return fallback


def _save(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _num(value: Any) -> float | None:
    try:
        if value in (None, "", "—"):
            return None
        value = float(value)
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    except Exception:
        return None


def normalize_symbol(value: Any) -> str:
    symbol = str(value or "").upper().strip().replace("/", "")
    for suffix in (".CS", ".I", ".PRO", ".RAW", ".M", ".ECN"):
        if symbol.endswith(suffix):
            symbol = symbol[: -len(suffix)]
    if "." in symbol:
        symbol = symbol.split(".", 1)[0]
    return symbol


def action_of(idea: dict[str, Any]) -> str:
    raw = str(idea.get("action") or idea.get("signal") or idea.get("final_signal") or idea.get("direction") or "").upper()
    if "SELL" in raw or "BEAR" in raw or "ПРОДА" in raw:
        return "SELL"
    if "BUY" in raw or "BULL" in raw or "ПОКУП" in raw:
        return "BUY"
    advisor = idea.get("advisor_signal") if isinstance(idea.get("advisor_signal"), dict) else {}
    raw = str(advisor.get("action") or "").upper()
    if raw in {"BUY", "SELL"}:
        return raw
    return "WAIT"


def price_of(idea: dict[str, Any]) -> float | None:
    for key in ("current_price", "price", "last", "close", "entry", "entry_price"):
        value = _num(idea.get(key))
        if value is not None:
            return value
    candles = idea.get("candles") or idea.get("chartData") or idea.get("chart_data") or []
    if isinstance(candles, dict):
        candles = candles.get("candles") or []
    if isinstance(candles, list) and candles:
        return _num((candles[-1] or {}).get("close"))
    return None


def _get_levels(idea: dict[str, Any]) -> tuple[float | None, float | None, float | None]:
    advisor = idea.get("advisor_signal") if isinstance(idea.get("advisor_signal"), dict) else {}
    entry = _num(idea.get("entry") or idea.get("entry_price") or advisor.get("entry"))
    sl = _num(idea.get("sl") or idea.get("stop_loss") or advisor.get("sl"))
    tp = _num(idea.get("tp") or idea.get("take_profit") or advisor.get("tp"))
    return entry, sl, tp


def _is_tradable_idea(idea: dict[str, Any]) -> bool:
    action = action_of(idea)
    entry, sl, tp = _get_levels(idea)
    advisor = idea.get("advisor_signal") if isinstance(idea.get("advisor_signal"), dict) else {}
    allowed = bool(idea.get("advisor_allowed") or advisor.get("allowed"))
    mode = str(idea.get("prop_mode") or advisor.get("mode") or "").lower()
    grade = str(idea.get("prop_grade") or advisor.get("grade") or "").upper()
    return action in {"BUY", "SELL"} and entry is not None and sl is not None and tp is not None and (allowed or mode in {"prop_entry", "watchlist"} or grade in {"A", "B"})


def _idea_id(idea: dict[str, Any]) -> str:
    symbol = normalize_symbol(idea.get("symbol") or idea.get("pair") or idea.get("instrument"))
    action = action_of(idea)
    entry, sl, tp = _get_levels(idea)
    return f"{symbol}-{action}-{round(entry or 0, 5)}-{round(sl or 0, 5)}-{round(tp or 0, 5)}-{uuid.uuid4().hex[:8]}"


def _active_view(active: dict[str, Any], live_idea: dict[str, Any] | None = None) -> dict[str, Any]:
    idea = dict(active.get("idea") or {})
    idea["idea_id"] = active.get("idea_id")
    idea["lifecycle_status"] = "active"
    idea["status"] = "active"
    idea["locked_until_tp_sl"] = True
    idea["created_at_utc"] = active.get("created_at_utc")
    idea["last_checked_at_utc"] = active.get("last_checked_at_utc")
    idea["active_reason_ru"] = "Идея зафиксирована и не меняется до TP или SL."
    if live_idea:
        idea["live_price"] = price_of(live_idea)
        idea["live_score"] = live_idea.get("prop_score")
        idea["live_grade"] = live_idea.get("prop_grade")
    return idea


def _hit_status(active: dict[str, Any], current_price: float | None) -> tuple[str | None, float | None]:
    if current_price is None:
        return None, None
    action = str(active.get("action") or "").upper()
    sl = _num(active.get("sl"))
    tp = _num(active.get("tp"))
    if action == "BUY":
        if tp is not None and current_price >= tp:
            return "tp_hit", tp
        if sl is not None and current_price <= sl:
            return "sl_hit", sl
    if action == "SELL":
        if tp is not None and current_price <= tp:
            return "tp_hit", tp
        if sl is not None and current_price >= sl:
            return "sl_hit", sl
    return None, None


def apply_idea_lifecycle(ideas: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(ideas, list):
        ideas = []
    active_raw = _load(ACTIVE_FILE, {})
    if isinstance(active_raw, list):
        active = {normalize_symbol(item.get("symbol")): item for item in active_raw if isinstance(item, dict)}
    elif isinstance(active_raw, dict):
        active = {normalize_symbol(k): v for k, v in active_raw.items() if isinstance(v, dict)}
    else:
        active = {}
    archive = _load(ARCHIVE_FILE, [])
    if not isinstance(archive, list):
        archive = []

    live_by_symbol = {normalize_symbol(i.get("symbol") or i.get("pair") or i.get("instrument")): i for i in ideas if isinstance(i, dict)}
    changed = False

    for symbol, item in list(active.items()):
        live = live_by_symbol.get(symbol) or {}
        current_price = price_of(live) or price_of(item.get("idea") or {})
        status, close_price = _hit_status(item, current_price)
        item["last_checked_at_utc"] = now_utc()
        item["last_price"] = current_price
        if status:
            entry = _num(item.get("entry"))
            sl = _num(item.get("sl"))
            tp = _num(item.get("tp"))
            action = str(item.get("action") or "").upper()
            risk = abs((entry or 0) - (sl or 0)) or None
            reward = abs((close_price or current_price or 0) - (entry or 0)) if entry is not None else None
            result_r = None
            if risk and reward is not None:
                result_r = round((1 if status == "tp_hit" else -1) * reward / risk, 2)
            archived = dict(item)
            archived.update({"status": status, "closed_at_utc": now_utc(), "close_price": close_price or current_price, "result": "TP" if status == "tp_hit" else "SL", "result_r": result_r, "final_action": action, "final_tp": tp, "final_sl": sl})
            archive.insert(0, archived)
            active.pop(symbol, None)
            changed = True
        else:
            active[symbol] = item

    output: list[dict[str, Any]] = []
    for idea in ideas:
        if not isinstance(idea, dict):
            continue
        symbol = normalize_symbol(idea.get("symbol") or idea.get("pair") or idea.get("instrument"))
        if not symbol:
            output.append(idea)
            continue
        if symbol in active:
            output.append(_active_view(active[symbol], idea))
            continue
        if _is_tradable_idea(idea):
            entry, sl, tp = _get_levels(idea)
            action = action_of(idea)
            item = {"idea_id": _idea_id(idea), "symbol": symbol, "action": action, "entry": entry, "sl": sl, "tp": tp, "created_at_utc": now_utc(), "last_checked_at_utc": now_utc(), "status": "active", "idea": dict(idea)}
            active[symbol] = item
            output.append(_active_view(item, idea))
            changed = True
        else:
            idea = dict(idea)
            idea["lifecycle_status"] = "candidate"
            output.append(idea)

    if changed or True:
        _save(ACTIVE_FILE, active)
        _save(ARCHIVE_FILE, archive[:1000])
    return {"ideas": output, "active": list(active.values()), "archive": archive[:200], "statistics": build_lifecycle_stats(active, archive)}


def build_lifecycle_stats(active: dict[str, Any] | None = None, archive: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if active is None:
        raw = _load(ACTIVE_FILE, {})
        active = raw if isinstance(raw, dict) else {}
    if archive is None:
        raw_archive = _load(ARCHIVE_FILE, [])
        archive = raw_archive if isinstance(raw_archive, list) else []
    total = len(archive)
    wins = sum(1 for x in archive if x.get("status") == "tp_hit" or x.get("result") == "TP")
    losses = sum(1 for x in archive if x.get("status") == "sl_hit" or x.get("result") == "SL")
    by_symbol: dict[str, dict[str, int]] = {}
    for item in archive:
        symbol = normalize_symbol(item.get("symbol")) or "UNKNOWN"
        row = by_symbol.setdefault(symbol, {"total": 0, "tp": 0, "sl": 0})
        row["total"] += 1
        if item.get("status") == "tp_hit" or item.get("result") == "TP":
            row["tp"] += 1
        if item.get("status") == "sl_hit" or item.get("result") == "SL":
            row["sl"] += 1
    return {"total": total, "active": len(active), "archived": total, "tp": wins, "sl": losses, "winrate": round(wins / total * 100, 2) if total else 0.0, "by_symbol": by_symbol, "updated_at_utc": now_utc()}
