from __future__ import annotations

import json, math, re
from typing import Any

VALID_DIRECTIONS = {"BUY", "SELL", "WAIT", "NEUTRAL"}
VALID_TIMEFRAMES = {"M15", "M30", "H1", "H4", "D1", "W1", "M1", "M5"}
INVALID_SYMBOLS = {"", "MARKET", "UNKNOWN", "NONE", "NULL", "N/A", "NA"}

_ALIAS_PATTERNS: list[tuple[str, str]] = [
    ("EURUSD", r"\bEUR\s*/?\s*USD\b|\bEURUSD\b|евро\s*(?:доллар|бакс)|евро/доллар|euro\s*dollar"),
    ("GBPUSD", r"\bGBP\s*/?\s*USD\b|\bGBPUSD\b|фунт\s*(?:доллар|бакс)|pound\s*dollar|cable"),
    ("USDJPY", r"\bUSD\s*/?\s*JPY\b|\bUSDJPY\b|доллар\s*иен|доллар/иен|dollar\s*yen"),
    ("USDCHF", r"\bUSD\s*/?\s*CHF\b|\bUSDCHF\b|доллар\s*франк|dollar\s*franc"),
    ("USDCAD", r"\bUSD\s*/?\s*CAD\b|\bUSDCAD\b|доллар\s*канад|dollar\s*cad"),
    ("AUDUSD", r"\bAUD\s*/?\s*USD\b|\bAUDUSD\b|австрал(?:иец|ийский доллар)|aussie"),
    ("NZDUSD", r"\bNZD\s*/?\s*USD\b|\bNZDUSD\b|новозеланд|kiwi"),
    ("XAUUSD", r"\bXAU\s*/?\s*USD\b|\bXAUUSD\b|\bgold\b|золото"),
    ("BTCUSD", r"\bBTC\s*/?\s*USD\b|\bBTCUSD\b|\bbitcoin\b|биткоин|биткойн"),
    ("ETHUSD", r"\bETH\s*/?\s*USD\b|\bETHUSD\b|\bethereum\b|эфириум|эфир"),
    ("SPX", r"\bSPX\b|\bS&P\s*500\b|\bSP500\b|s\s*&\s*p|эс\s*энд\s*пи"),
    ("NAS100", r"\bNAS100\b|\bNASDAQ\s*100\b|\bNDX\b|насдак"),
    ("DAX", r"\bDAX\b|\bGER40\b|немецкий\s*индекс"),
    ("UKOIL", r"\bUKOIL\b|\bBRENT\b|brent|брент"),
]

EXPLICIT_ACTION_RE = re.compile(r"\b(buy|sell|long|short|покуп|прода|лонг|шорт|entry|setup|trade|stop|target|tp|sl|recommend)\b", re.I)


def normalize_symbol(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw or raw.upper() in INVALID_SYMBOLS: return None
    for sym, pat in _ALIAS_PATTERNS:
        if re.search(pat, raw, re.I | re.U): return sym
    norm = raw.upper().replace("/", "").replace(" ", "")
    if norm in INVALID_SYMBOLS: return None
    return {"SP500":"SPX", "BRENT":"UKOIL", "NDX":"NAS100"}.get(norm, norm)


def unique_symbols(values: Any) -> list[str]:
    if not isinstance(values, list): values = [values]
    out=[]
    for v in values:
        if isinstance(v, list):
            vals = unique_symbols(v)
        else:
            vals = [normalize_symbol(v)]
        for s in vals:
            if s and s not in out: out.append(s)
    return out


def extract_symbols_from_text(*parts: Any) -> list[str]:
    text = "\n".join(str(p or "") for p in parts)
    return unique_symbols([s for s,p in _ALIAS_PATTERNS if re.search(p, text, re.I | re.U)])


def normalize_direction(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw: return "NEUTRAL"
    if re.search(r"wait|stand aside|no trade|hold off|confirmation|жд", raw): return "WAIT"
    if re.search(r"mixed|unclear|informational|neutral|ignore|no directional|обзор", raw): return "NEUTRAL"
    if re.search(r"\b(long|buy|bullish)\b|покуп|лонг", raw): return "BUY"
    if re.search(r"\b(short|sell|bearish)\b|прода|шорт", raw): return "SELL"
    return "NEUTRAL"


def normalize_timeframe(value: Any) -> str | None:
    raw = str(value or "").strip().lower().replace(" ", "")
    aliases={"15m":"M15","m15":"M15","30m":"M30","m30":"M30","1h":"H1","h1":"H1","hourly":"H1","4h":"H4","h4":"H4","daily":"D1","day":"D1","d1":"D1","weekly":"W1","w1":"W1","1d":"D1","1w":"W1"}
    val=aliases.get(raw, raw.upper())
    return val if val in VALID_TIMEFRAMES else None


def to_float_or_none(value: Any) -> float | None:
    if value is None or value == "": return None
    try:
        n=float(str(value).strip().replace("%", "").replace(",", "."))
    except (TypeError, ValueError): return None
    if not math.isfinite(n) or n <= 0: return None
    return round(n, 8)


def normalize_confidence(value: Any) -> int | None:
    if value is None or value == "": return None
    try: num=float(str(value).strip().replace("%", "").replace(",", "."))
    except (TypeError, ValueError): return None
    if 0 <= num <= 1: num *= 100
    if num < 0 or num > 100: return None
    return int(round(num))

_ALIAS_MAP={"instrument":"symbol","ticker":"symbol","asset":"symbol","pair":"symbol","market":"symbol","time_frame":"timeframe","tf":"timeframe","period":"timeframe","bias":"direction","side":"direction","signal":"direction","action":"direction","recommendation":"direction","confidence_percent":"confidence","probability":"confidence","score":"confidence","entry_price":"entry","entry_level":"entry","buy_price":"entry","sell_price":"entry","sl":"stop_loss","stop":"stop_loss","stoploss":"stop_loss","stop_loss_price":"stop_loss","tp":"take_profit","takeprofit":"take_profit","take_profit_price":"take_profit","target":"targets","target_prices":"targets","tp_levels":"targets","profit_targets":"targets","ideas":"trade_ideas","trades":"trade_ideas","setups":"trade_ideas","signals":"trade_ideas","reason":"reasoning"}

def normalize_aliases(obj: Any) -> Any:
    if isinstance(obj, list): return [normalize_aliases(x) for x in obj]
    if not isinstance(obj, dict): return obj
    out={}
    for k,v in obj.items():
        nk=_ALIAS_MAP.get(str(k), str(k)); nv=normalize_aliases(v)
        if nk in {"symbols","targets"} and not isinstance(nv, list): nv=[nv]
        out[nk]=nv
    return out


def recover_json_payload(content: Any) -> tuple[dict[str, Any] | None, str, str | None]:
    if isinstance(content, dict): return normalize_aliases(content), "success", None
    text=str(content or "").strip()
    if not text: return None, "failed", "empty"
    fence=re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S|re.I)
    candidates=[fence.group(1)] if fence else []
    candidates.append(text)
    start=text.find("{"); end=text.rfind("}")
    if start>=0 and end>start: candidates.append(text[start:end+1])
    for cand in candidates:
        try:
            obj=json.loads(cand)
            if isinstance(obj, str): obj=json.loads(obj)
            if isinstance(obj, dict):
                for key in ("content","message","review","data","payload","arguments"):
                    val=obj.get(key)
                    if isinstance(val, str) and val.strip().startswith("{"):
                        return normalize_aliases(json.loads(val)), "partial", None
                    if isinstance(val, dict): return normalize_aliases(val), "partial", None
                return normalize_aliases(obj), "success" if cand.strip()==text else "partial", None
        except Exception as exc: last=exc.__class__.__name__
    return None, "failed", locals().get("last", "JSONDecodeError")
