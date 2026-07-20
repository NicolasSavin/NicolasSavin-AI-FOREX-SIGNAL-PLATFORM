from __future__ import annotations
import os
from datetime import datetime, timezone
from typing import Any

def now_iso(): return datetime.now(timezone.utc).isoformat()
def clamp(v:Any, lo=0.0, hi=100.0)->float:
    try: return round(max(lo,min(hi,float(v or 0))),2)
    except Exception: return 0.0
def env_float(name:str, default:float, lo=0.0, hi=100.0)->float:
    try: return clamp(os.getenv(name, default), lo, hi)
    except Exception: return default
def sym(v:Any)->str: return str(v or '').replace('/','').replace(' ','').upper()
def norm_dir(v:Any)->str:
    s=str(v or '').upper();
    if s in {'LONG','BUY'}: return 'BUY'
    if s in {'SHORT','SELL'}: return 'SELL'
    if s in {'WAIT','HOLD'}: return 'WAIT'
    if s in {'NEUTRAL','MIXED','NO_DATA'}: return s
    return 'NO_DATA' if not s else s
def parse_dt(v:Any):
    if not v: return None
    try: return datetime.fromisoformat(str(v).replace('Z','+00:00'))
    except Exception: return None
def age_hours(v:Any)->float|None:
    d=parse_dt(v)
    if not d: return None
    if not d.tzinfo: d=d.replace(tzinfo=timezone.utc)
    return max(0.0,(datetime.now(timezone.utc)-d).total_seconds()/3600)
