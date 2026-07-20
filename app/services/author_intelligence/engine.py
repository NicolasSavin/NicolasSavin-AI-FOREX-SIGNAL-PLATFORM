from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.services.storage_paths import DATA_DIR, atomic_write_json

_DIRECTION_MAP = {"BUY": "BUY", "BULLISH": "BUY", "LONG": "BUY", "SELL": "SELL", "BEARISH": "SELL", "SHORT": "SELL", "WAIT": "WAIT", "HOLD": "WAIT", "NEUTRAL": "WAIT", "IGNORE": "WAIT"}
AUTHOR_PROFILES_PATH = DATA_DIR / "author_profiles.json"
DEFAULT_ALIAS_RULES = {
    "gerchik": ["gerchik", "alexander gerchik", "gerchik & co", "gerchik and co", "герчик", "александр герчик"],
}


def _now() -> str: return datetime.now(timezone.utc).isoformat()
def _clean(value: Any) -> str: return re.sub(r"\s+", " ", str(value or "Unknown").strip()) or "Unknown"
def normalize_author_name(value: Any) -> str: return re.sub(r"[^a-zа-я0-9]+", " ", _clean(value).lower()).strip()

def _direction(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in _DIRECTION_MAP: return _DIRECTION_MAP[text]
    if "BUY" in text or "BULL" in text or "LONG" in text: return "BUY"
    if "SELL" in text or "BEAR" in text or "SHORT" in text: return "SELL"
    return "WAIT"

def _number(value: Any) -> float | None:
    try:
        if value in (None, "", "Unknown"): return None
        return float(value)
    except (TypeError, ValueError): return None

def _avg(values: list[float]) -> int: return round(sum(values) / len(values)) if values else 0

def _date(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)

@dataclass
class AuthorProfile:
    id: str; name: str; normalized_name: str; aliases: list[str] = field(default_factory=list)
    source_count: int = 0; review_count: int = 0; structured_review_count: int = 0; trade_idea_count: int = 0; symbol_count: int = 0
    first_seen: str | None = None; last_seen: str | None = None; last_review: str | None = None
    average_confidence: int = 0; average_agreement: int = 0; consensus_alignment: int = 0
    trust_score: int = 0; accuracy_score: int = 0; activity_score: int = 0; quality_score: int = 0; signal_score: int = 0
    followers: int | None = None; language: str = "unknown"; categories: list[str] = field(default_factory=list); status: str = "Experimental"
    average_review_length: int = 0; structured_extraction_percent: int = 0; trade_setup_percent: int = 0; entry_quality: int = 0; confidence_stability: int = 0
    buy_count: int = 0; sell_count: int = 0; wait_count: int = 0; tp_hit_ratio: int | None = None; sl_hit_ratio: int | None = None; rr: float | None = None
    prediction_accuracy: int | None = None; average_holding_time: str | None = None; trust_label: str = "Experimental"
    history: list[dict[str, Any]] = field(default_factory=list); symbols: list[str] = field(default_factory=list); trade_ideas: list[dict[str, Any]] = field(default_factory=list); trust_evolution: list[dict[str, Any]] = field(default_factory=list)

class AuthorIntelligenceEngine:
    """Builds persisted first-class author profiles from media, AI Review, Knowledge Graph and Consensus inputs."""
    def __init__(self, *, media_catalog_loader: Callable[[], list[dict[str, Any]]], review_payload_builder: Callable[[dict[str, Any]], dict[str, Any]], committee_builder: Callable[[str], dict[str, Any]], consensus_builder: Callable[[str], dict[str, Any]] | None = None, outcome_provider: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None, alias_rules: dict[str, list[str]] | None = None, profiles_path: Path = AUTHOR_PROFILES_PATH) -> None:
        self.media_catalog_loader=media_catalog_loader; self.review_payload_builder=review_payload_builder; self.committee_builder=committee_builder; self.consensus_builder=consensus_builder; self.outcome_provider=outcome_provider; self.alias_rules=alias_rules or DEFAULT_ALIAS_RULES; self.profiles_path=profiles_path

    def build_all(self) -> list[dict[str, Any]]:
        profiles=[self._build_author(a, v) for a,v in self._group_videos().items()]
        rows=[self._legacy(p) for p in sorted(profiles, key=lambda p:(p.trust_score,p.accuracy_score,p.activity_score), reverse=True)]
        self.persist_profiles(rows); return rows
    def persist_profiles(self, rows: list[dict[str, Any]]) -> None: atomic_write_json(self.profiles_path, {"updated_at": _now(), "profiles": rows})
    def debug(self) -> dict[str, Any]:
        groups=self._group_videos(); return {"profiles_path": str(self.profiles_path), "alias_rules": self.alias_rules, "author_groups": {k: len(v) for k,v in groups.items()}, "profile_count": len(groups)}
    def stats(self) -> dict[str, Any]:
        rows=self.build_all(); return {"authors": len(rows), "top_authors": rows[:5], "most_accurate": sorted(rows,key=lambda r:r["accuracy_score"], reverse=True)[:5], "most_active": sorted(rows,key=lambda r:r["activity_score"], reverse=True)[:5], "fastest_growing": sorted(rows,key=lambda r:(r["review_count"], r["last_review"] or ""), reverse=True)[:5], "highest_trust": sorted(rows,key=lambda r:r["trust_score"], reverse=True)[:5]}
    def build_for_author(self, author: str) -> dict[str, Any]:
        wanted=self._canonical(author)
        for name,videos in self._group_videos().items():
            if self._canonical(name)==wanted: return self._legacy(self._build_author(name,videos, include_opinions=True))
        raise ValueError("Author not found")
    def _canonical(self, name: Any) -> str:
        n=normalize_author_name(name)
        for canonical, aliases in self.alias_rules.items():
            if n in {normalize_author_name(a) for a in aliases}: return normalize_author_name(canonical)
        return n
    def _group_videos(self) -> dict[str, list[dict[str, Any]]]:
        grouped=defaultdict(list); names={}
        for video in self.media_catalog_loader():
            raw=_clean(video.get("author") or video.get("channel") or video.get("rss_author") or video.get("source_id"))
            key=self._canonical(raw); names.setdefault(key, raw); grouped[key].append(video)
        return {names[k]: v for k,v in grouped.items()}
    def _opinion(self, video: dict[str, Any]) -> dict[str, Any]:
        warnings=[]
        try: payload=self.review_payload_builder(video)
        except Exception as exc: payload={}; warnings.append(f"review_unavailable: {exc.__class__.__name__}: {exc}")
        analysis=payload.get("analysis") or payload.get("ai_review") or {}; knowledge=payload.get("knowledge") or payload.get("knowledge_context") or {}; llm=payload.get("llm_review") or {}
        try: committee=self.committee_builder(str(video.get("id") or ""))
        except Exception as exc: committee={"decision":"WAIT","overall_score":0,"agreement_score":0}; warnings.append(f"committee_unavailable: {exc.__class__.__name__}: {exc}")
        symbol=knowledge.get("symbol") or analysis.get("symbol") or video.get("symbol")
        direction=_direction(committee.get("decision") or analysis.get("direction") or knowledge.get("direction") or llm.get("direction"))
        confidence=int(_number(analysis.get("confidence") or llm.get("confidence") or knowledge.get("confidence") or committee.get("overall_score")) or 0)
        text=" ".join(str(x or "") for x in [analysis.get("summary"), llm.get("summary"), knowledge.get("summary"), video.get("title")])
        entry=_number(analysis.get("entry") or knowledge.get("entry")); targets=analysis.get("targets") or knowledge.get("targets") or []
        outcome=self.outcome_provider(video) if self.outcome_provider else None
        return {"video_id":video.get("id"),"title":video.get("title"),"author":video.get("author") or video.get("channel") or video.get("source_id") or "Unknown","channel_id":video.get("channel_id") or video.get("source_id"),"published_at":video.get("published_at"),"symbol":symbol,"timeframe":video.get("timeframe") or knowledge.get("timeframe") or analysis.get("timeframe"),"direction":direction,"confidence":max(0,min(100,confidence)),"committee_score":int(_number(committee.get("overall_score")) or 0),"agreement_score":int(_number(committee.get("agreement_score") or knowledge.get("agreement_score") or llm.get("agreement_score")) or 0),"entry":entry,"targets":targets if isinstance(targets,list) else [],"review_length":len(text),"structured":bool(symbol or entry or targets),"trade_setup":direction in {"BUY","SELL"} and bool(entry or targets),"outcome":outcome,"warnings":warnings,"errors_count":len(warnings),"review_status":"review_unavailable" if warnings else "ok"}
    def _build_author(self, author: str, videos: list[dict[str, Any]], *, include_opinions: bool=False) -> AuthorProfile:
        opinions=[self._opinion(v) for v in videos]; total=len(opinions); dirs=Counter(o["direction"] for o in opinions); symbols=sorted({str(o.get("symbol")) for o in opinions if o.get("symbol")}); confid=[o["confidence"] for o in opinions]
        structured=sum(o["structured"] for o in opinions); setups=sum(o["trade_setup"] for o in opinions); signal_count=sum(1 for o in opinions if o["direction"] in {"BUY", "SELL"}); agreements=[o["agreement_score"] for o in opinions]
        consensus_align=[]
        if self.consensus_builder:
            for o in opinions:
                if o.get("symbol"):
                    try: consensus_align.append(100 if self.consensus_builder(str(o["symbol"])).get("overall_direction")==o["direction"] else 0)
                    except Exception: pass
        alignment=_avg(consensus_align or agreements); activity=min(100,total*10+setups*5); quality=_avg([round(structured/total*100) if total else 0, round(setups/total*100) if total else 0, min(100,_avg([o["review_length"] for o in opinions])//4), _avg(agreements)])
        signal=_avg([round((dirs.get("BUY",0)+dirs.get("SELL",0))/total*100) if total else 0, _avg(confid), alignment]); accuracy=_avg([_avg([o["committee_score"] for o in opinions]), alignment, _avg(confid)])
        conflicts=min(dirs.get("BUY",0), dirs.get("SELL",0)); duplicate_ratio=max(0,total-len({o.get("video_id") for o in opinions})); trust=max(0,min(100,_avg([quality*.30, accuracy*.25, alignment*.20, activity*.15, signal*.10])-conflicts*4-duplicate_ratio*5))
        dates=sorted([_date(o.get("published_at")) for o in opinions if o.get("published_at")]); latest=max(opinions, key=lambda o:_date(o.get("published_at")), default={})
        profile=AuthorProfile(id=self._canonical(author).replace(" ","-"), name=author, normalized_name=self._canonical(author), aliases=sorted({o["author"] for o in opinions}), source_count=len({o.get("channel_id") for o in opinions if o.get("channel_id")}), review_count=total, structured_review_count=structured, trade_idea_count=signal_count, symbol_count=len(symbols), first_seen=dates[0].isoformat() if dates else None, last_seen=dates[-1].isoformat() if dates else None, last_review=latest.get("published_at"), average_confidence=_avg(confid), average_agreement=_avg(agreements), consensus_alignment=alignment, trust_score=trust, accuracy_score=accuracy, activity_score=activity, quality_score=quality, signal_score=signal, followers=next((_number(v.get("followers")) for v in videos if _number(v.get("followers")) is not None), None), language=str(next((v.get("language") for v in videos if v.get("language")), "unknown")), categories=sorted({str(c) for v in videos for c in (v.get("categories") or [])}) if videos else [], average_review_length=_avg([o["review_length"] for o in opinions]), structured_extraction_percent=round(structured/total*100) if total else 0, trade_setup_percent=round(setups/total*100) if total else 0, entry_quality=round(sum(1 for o in opinions if o.get("entry") is not None)/total*100) if total else 0, confidence_stability=max(0,100-(max(confid)-min(confid))) if confid else 0, buy_count=dirs.get("BUY",0), sell_count=dirs.get("SELL",0), wait_count=dirs.get("WAIT",0), prediction_accuracy=None, history=opinions if include_opinions else [], symbols=symbols, trade_ideas=[o for o in opinions if o["direction"] in {"BUY", "SELL"}], trust_evolution=[{"date": o.get("published_at"), "trust_score": trust} for o in opinions[-20:]])
        profile.status=profile.trust_label=self._trust_label(trust,total); return profile
    def _trust_label(self, score:int, sample:int)->str:
        if sample < 2: return "Experimental"
        if score >= 85: return "Elite"
        if score >= 70: return "High"
        if score >= 50: return "Medium"
        return "Low"
    def _legacy(self, p: AuthorProfile) -> dict[str, Any]:
        d=asdict(p); d.update({"author":p.name,"videos":p.review_count,"signals":p.trade_idea_count,"accuracy":p.accuracy_score,"accuracy_label":"proxy_committee_accuracy_until_real_market_outcomes_available","rating":p.trust_score,"tier":p.trust_label,"average_committee_score": _avg([i.get("committee_score", 0) for i in p.history]) if p.history else p.accuracy_score,"latest_opinion": (p.history[0]["direction"] if p.history else ("BUY" if p.buy_count>=max(p.sell_count,p.wait_count) and p.buy_count else "SELL" if p.sell_count>=p.wait_count and p.sell_count else "WAIT")),"bullish_count":p.buy_count,"bearish_count":p.sell_count,"neutral_count":p.wait_count,"report":{"summary":f"{p.name}: trust {p.trust_score}/100 ({p.trust_label}), proxy accuracy {p.accuracy_score}% на базе {p.review_count} обзоров. Метрики accuracy/performance помечены как proxy до подключения реальных market outcomes.","favorite_symbols":p.symbols,"favorite_markets":p.symbols,"risk_profile":"PROXY","warnings_count":sum(len(i.get('warnings') or []) for i in p.history),"errors_count":sum(int(i.get('errors_count') or 0) for i in p.history)}}); return d
