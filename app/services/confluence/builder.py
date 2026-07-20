from __future__ import annotations
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from app.services.storage_paths import DATA_DIR, atomic_write_json
from .models import ConfluenceCollection, ConfluenceState, FactorAssessment
from .statistics import clamp, env_float, freshness, normalize_direction, now_iso

CONFLUENCE_PATH = DATA_DIR / "confluence.json"

class ConfluenceConfig:
    ENV = {
        "market_state":"FXPILOT_CONFLUENCE_WEIGHT_MARKET_STATE", "multi_timeframe":"FXPILOT_CONFLUENCE_WEIGHT_MULTI_TIMEFRAME",
        "consensus":"FXPILOT_CONFLUENCE_WEIGHT_CONSENSUS", "signal_validation":"FXPILOT_CONFLUENCE_WEIGHT_VALIDATION",
        "author_intelligence":"FXPILOT_CONFLUENCE_WEIGHT_AUTHORS", "performance":"FXPILOT_CONFLUENCE_WEIGHT_PERFORMANCE",
        "structured_reviews":"FXPILOT_CONFLUENCE_WEIGHT_REVIEWS", "order_flow":"FXPILOT_CONFLUENCE_WEIGHT_ORDER_FLOW"}
    DEFAULTS = {"market_state":20,"multi_timeframe":20,"consensus":20,"signal_validation":15,"author_intelligence":10,"performance":10,"structured_reviews":5,"order_flow":0}
    EXPIRY = {"market_state":6,"multi_timeframe":6,"consensus":12,"structured_reviews":48,"signal_validation":24,"author_intelligence":168,"performance":168,"order_flow":1}
    def __init__(self) -> None:
        raw = {k: env_float(self.ENV[k], v) for k,v in self.DEFAULTS.items()}
        total = sum(raw.values()) or 1
        self.weights = {k: round(v / total * 100, 4) for k,v in raw.items()}
        self.min_margin = env_float("FXPILOT_CONFLUENCE_MIN_DIRECTION_MARGIN", 12)
        self.min_quality = env_float("FXPILOT_CONFLUENCE_MIN_DATA_QUALITY", 35)
        self.actionable_score = env_float("FXPILOT_CONFLUENCE_ACTIONABLE_SCORE", 70)
        self.strong_score = env_float("FXPILOT_CONFLUENCE_STRONG_SCORE", 85)

class ConfluenceBuilder:
    def __init__(self, *, symbol_loader: Callable[[], list[str]], market_state_loader: Callable[[], dict[str, Any]], multi_timeframe_loader: Callable[[], dict[str, Any]], consensus_builder: Callable[[str], dict[str, Any]], validation_loader: Callable[[], dict[str, Any]], author_loader: Callable[[], list[dict[str, Any]]], performance_loader: Callable[[], dict[str, Any]], review_ideas_loader: Callable[[], list[dict[str, Any]]], storage_path: Path = CONFLUENCE_PATH, config: ConfluenceConfig | None = None) -> None:
        self.symbol_loader=symbol_loader; self.market_state_loader=market_state_loader; self.multi_timeframe_loader=multi_timeframe_loader; self.consensus_builder=consensus_builder; self.validation_loader=validation_loader; self.author_loader=author_loader; self.performance_loader=performance_loader; self.review_ideas_loader=review_ideas_loader; self.storage_path=storage_path; self.config=config or ConfluenceConfig()

    def build_all(self) -> dict[str, Any]:
        started=perf_counter(); errors=[]
        ms=self._safe(self.market_state_loader,{"items":[]},errors,"market_state"); mtf=self._safe(self.multi_timeframe_loader,{"items":[]},errors,"multi_timeframe")
        val=self._safe(self.validation_loader,{"items":[],"symbols":[]},errors,"signal_validation"); authors=self._safe(self.author_loader,[],errors,"author_intelligence")
        perf=self._safe(self.performance_loader,{"items":[]},errors,"performance"); ideas=self._safe(self.review_ideas_loader,[],errors,"structured_reviews")
        symbols=set(self.symbol_loader())|{self._sym(i.get("symbol")) for i in ms.get("items",[])}|{self._sym(i.get("symbol")) for i in mtf.get("items",[])}|{self._sym(i.get("symbol")) for i in ideas}; symbols.discard(""); symbols.discard("MARKET")
        items=[]
        for s in sorted(symbols):
            try: items.append(self.build_symbol(s, ms, mtf, val, authors, perf, ideas).model_dump())
            except Exception as exc: errors.append(f"{s}: {exc.__class__.__name__}: {exc}")
        coll=ConfluenceCollection(items=items,total=len(items),generated_at=now_iso(),diagnostics={"build_time_ms":int((perf_counter()-started)*1000),"errors":errors,"weights":self.config.weights,"score_interpretation":{"0-29":"INSUFFICIENT","30-49":"WEAK","50-69":"MODERATE","70-84":"STRONG","85-100":"EXCEPTIONAL"},"score_note":"Confluence score is deterministic agreement strength, not expected profit or win probability."})
        payload=coll.model_dump(); atomic_write_json(self.storage_path,payload); return payload

    def build_symbol(self,symbol, ms_payload, mtf_payload, val_payload, authors, perf_payload, ideas) -> ConfluenceState:
        ms=next((i for i in ms_payload.get("items",[]) if self._sym(i.get("symbol"))==symbol),{})
        mtf=next((i for i in mtf_payload.get("items",[]) if self._sym(i.get("symbol"))==symbol),{})
        cons=self._safe(lambda:self.consensus_builder(symbol),{},[],"consensus")
        factors=[self._market_state(ms), self._mtf(mtf), self._consensus(cons), self._validation(symbol,val_payload), self._authors(symbol,authors,cons), self._performance(symbol,perf_payload), self._reviews(symbol,ideas), self._missing("order_flow","Order Flow зарезервирован для будущего подключения; сейчас недоступен.")]
        available=[f for f in factors if f.available and f.configured_weight>0]
        avail_weight=sum(f.configured_weight for f in available) or 0
        for f in factors:
            f.effective_weight=round(f.configured_weight/avail_weight*100,2) if f in available and avail_weight else 0
            f.contribution=round(f.normalized_score*f.effective_weight/100*f.freshness_score/100*f.data_quality_score/100,2) if f.available else 0
        totals={d:sum(f.contribution for f in factors if f.direction==d) for d in ["BUY","SELL","WAIT","NEUTRAL"]}
        conflict=self._conflict(factors, totals); quality=clamp(sum(f.data_quality_score*f.effective_weight/100 for f in available)); fresh=clamp(sum(f.freshness_score*f.effective_weight/100 for f in available)); conf=clamp(sum(f.confidence*f.effective_weight/100 for f in available))
        agreement=clamp(max(totals.values()) - min(totals["BUY"], totals["SELL"]) + conf*0.3)
        score=clamp(sum(f.contribution for f in factors) * 0.85 + agreement*0.25 + fresh*0.15 - conflict*0.25)
        direction=self._direction(totals, conflict, quality, len(available)); rec=self._recommendation(direction, score)
        supporting=[f.factor for f in factors if f.available and f.direction==direction and direction in {"BUY","SELL","WAIT","NEUTRAL"}]
        conflicting=[f.factor for f in factors if f.available and direction in {"BUY","SELL"} and f.direction in {"BUY","SELL"} and f.direction!=direction]
        missing=[f.factor for f in factors if not f.available]
        warnings=self._warnings(ms, mtf, factors, quality, conflict)
        actionable=rec in {"BUY","SELL","STRONG_BUY","STRONG_SELL"} and score>=self.config.actionable_score and quality>=self.config.min_quality and conflict<60
        reason=self._reason(symbol,direction,score,supporting,conflicting,missing,warnings)
        return ConfluenceState(symbol=symbol,direction=direction,recommendation=rec,confluence_score=score,confidence=conf,agreement_score=agreement,conflict_score=conflict,data_quality_score=quality,freshness_score=fresh,actionable=actionable,supporting_factors=supporting,conflicting_factors=conflicting,missing_factors=missing,factors=factors,primary_reason=reason,warnings=warnings,review_count=int(ms.get("review_count") or len([i for i in ideas if self._sym(i.get("symbol"))==symbol])),author_count=int(ms.get("author_count") or 0),validated_signal_count=int(mtf.get("validated_signal_count") or 0),dominant_timeframe=mtf.get("dominant_tf"),updated_at=now_iso())

    def _factor(self,name,av,dirn,raw,conf,qual,reason,updated=None):
        fr=freshness(updated,self.config.EXPIRY.get(name,24)) if updated else (85 if av else 0)
        if av and fr<25: qual=qual*0.65
        return FactorAssessment(factor=name,available=av,direction=normalize_direction(dirn),raw_score=clamp(raw),normalized_score=clamp(raw),configured_weight=self.config.weights.get(name,0),confidence=clamp(conf),freshness_score=fr if av else 0,data_quality_score=clamp(qual),reason=reason,updated_at=updated)
    def _missing(self,n,r): return self._factor(n,False,"NO_DATA",0,0,0,r,None)
    def _market_state(self, m):
        av=bool(m); qual=clamp((m.get("agreement",0)*.3)+(m.get("confidence",0)*.25)+(min(int(m.get("review_count") or 0),8)/8*25)+(min(int(m.get("author_count") or 0),4)/4*20)) if av else 0
        raw=clamp(((m.get("confidence",0) or 0)*.4)+((m.get("agreement",0) or 0)*.3)+((m.get("validation_score",0) or 0)*.15)+((m.get("author_score",0) or 0)*.1)+((m.get("performance_score",0) or 0)*.05))
        return self._factor("market_state",av,m.get("direction"),raw,m.get("confidence",0),qual,f"Market State: {m.get('direction','нет данных')}, agreement {m.get('agreement',0)}%, reviews {m.get('review_count',0)}.",m.get("updated_at"))
    def _mtf(self,m):
        raw=clamp((m.get("confidence",0)*.35)+(m.get("alignment_score",0)*.45)+((100-(m.get("conflict_score",0) or 0))*.2)) if m else 0
        qual=clamp((m.get("alignment_score",0)*.35)+(m.get("confidence",0)*.35)+(min(int(m.get("validated_signal_count") or 0),5)/5*30)) if m else 0
        return self._factor("multi_timeframe",bool(m),m.get("overall_direction"),raw,m.get("confidence",0),qual,f"Multi-Timeframe: {m.get('overall_direction','нет данных')}, alignment {m.get('alignment_score',0)}%, conflict {m.get('conflict_score',0)}%.",m.get("updated_at"))
    def _consensus(self,c):
        av=bool(c.get("opinions") or c.get("overall_direction") or c.get("direction")); raw=clamp(c.get("agreement_percent") or c.get("agreement") or c.get("strength") or 0); conf=clamp(c.get("average_confidence") or raw)
        return self._factor("consensus",av,c.get("overall_direction") or c.get("direction") or c.get("consensus"),raw,conf,raw,f"Consensus: agreement {raw}% from {len(c.get('opinions') or [])} opinions.",c.get("updated_at") or c.get("generated_at"))
    def _validation(self,s,p):
        rows=[r for r in p.get("items",[]) if self._sym(r.get("symbol"))==s and r.get("status") in {"validated","completed"}]; sym=next((r for r in p.get("symbols",[]) if self._sym(r.get("key") or r.get("symbol"))==s),{})
        count=len(rows) or int(sym.get("validated_count") or sym.get("count") or 0); av=count>0; win=clamp(sym.get("win_rate") or sym.get("accuracy") or (sum(1 for r in rows if r.get("outcome") in {"TP","WIN"})/count*100 if count else 0))
        dirn="BUY" if win>=55 else "SELL" if win<=45 and av else "NEUTRAL"
        return self._factor("signal_validation",av,dirn,win,win,clamp(min(count,10)/10*70+30 if av else 0),f"Validation: {count} real validated signals, win rate {win}%.",sym.get("updated_at"))
    def _authors(self,s,authors,cons):
        opinions=cons.get("opinions") or []; names={str(o.get("author")) for o in opinions if o.get("author")}; rows=[a for a in authors if str(a.get("name") or a.get("author")) in names]
        av=bool(rows); trust=clamp(sum(clamp(a.get("trust_score") or a.get("rating"),50) for a in rows)/max(1,len(rows))); dirn=normalize_direction(cons.get("overall_direction") or cons.get("direction")) if len(rows)>1 else "NEUTRAL"
        return self._factor("author_intelligence",av,dirn,trust,trust,clamp(min(len(rows),5)/5*50+trust*.5 if av else 0),f"Authors: {len(rows)} matched authors, trust {trust}%; single author is not decisive.",None)
    def _performance(self,s,p):
        rows=[r for r in p.get("items",[]) if self._sym(r.get("symbol"))==s]; done=[r for r in rows if r.get("result") in {"WIN","LOSS","TP","SL"}]; wins=sum(1 for r in done if r.get("result") in {"WIN","TP"}); av=bool(done); score=round(wins/len(done)*100,2) if done else 0
        return self._factor("performance",av,"BUY" if score>=55 else "SELL" if score<=45 and av else "NEUTRAL",score,score,clamp(min(len(done),20)/20*70+30 if av else 0),f"Performance: {len(done)} real stored outcomes, success {score}%.",p.get("generated_at"))
    def _reviews(self,s,ideas):
        rows=[i for i in ideas if self._sym(i.get("symbol"))==s]; dirs=[normalize_direction(i.get("direction") or i.get("signal") or i.get("action")) for i in rows]; av=bool(rows); buy=dirs.count("BUY"); sell=dirs.count("SELL"); wait=dirs.count("WAIT")+dirs.count("NEUTRAL"); top=max((buy,"BUY"),(sell,"SELL"),(wait,"WAIT"))[1] if av else "NO_DATA"; raw=clamp(max(buy,sell,wait)/max(1,len(rows))*100)
        return self._factor("structured_reviews",av,top,raw,clamp(sum(clamp(i.get("confidence"),50) for i in rows)/max(1,len(rows))),clamp(min(len(rows),10)/10*60+raw*.4 if av else 0),f"Structured Reviews: {len(rows)} ideas, dominant {top}.",max((str(i.get("published_at") or i.get("updated_at") or "") for i in rows), default=None))
    def _direction(self,t,c,q,n):
        if n<2 or q<20: return "NO_DATA"
        if c>=55 and t["BUY"]>10 and t["SELL"]>0: return "MIXED"
        if t["BUY"]>=t["SELL"]+self.config.min_margin and q>=self.config.min_quality: return "BUY"
        if t["SELL"]>=t["BUY"]+self.config.min_margin and q>=self.config.min_quality: return "SELL"
        if t["WAIT"]>=max(t["BUY"],t["SELL"]): return "WAIT"
        return "NEUTRAL" if q>=self.config.min_quality else "WAIT"
    def _recommendation(self,d,s):
        return "STRONG_BUY" if d=="BUY" and s>=self.config.strong_score else "BUY" if d=="BUY" and s>=self.config.actionable_score else "STRONG_SELL" if d=="SELL" and s>=self.config.strong_score else "SELL" if d=="SELL" and s>=self.config.actionable_score else "WAIT" if d in {"BUY","SELL","WAIT","MIXED"} else "IGNORE" if d=="NEUTRAL" else "NO_DATA"
    def _conflict(self,f,t):
        dirs={x.factor:x.direction for x in f if x.available}; c=0
        if dirs.get("market_state") in {"BUY","SELL"} and dirs.get("multi_timeframe") in {"BUY","SELL"} and dirs["market_state"]!=dirs["multi_timeframe"]: c+=35
        if dirs.get("consensus") in {"BUY","SELL"} and dirs.get("market_state") in {"BUY","SELL"} and dirs["consensus"]!=dirs["market_state"]: c+=25
        if t["BUY"] and t["SELL"]: c+=clamp(50-abs(t["BUY"]-t["SELL"]))*0.8
        mtf=next((x for x in f if x.factor=="multi_timeframe"),None)
        if mtf and mtf.available and mtf.reason:
            import re
            m=re.search(r"conflict ([0-9.]+)%", mtf.reason)
            c += (float(m.group(1)) * 0.35) if m else 0
        c+=max(0,(mtf.raw_score if mtf and mtf.direction=="MIXED" else 0))*.2
        return clamp(c)
    def _warnings(self,ms,mtf,factors,q,c):
        w=[]
        if int(ms.get("review_count") or 0)<2: w.append("low_review_count")
        if int(ms.get("author_count") or 0)<=1: w.append("single_author_dependency")
        if "signal_validation" in [x.factor for x in factors if not x.available]: w.append("validation_unavailable")
        if "performance" in [x.factor for x in factors if not x.available]: w.append("performance_unavailable")
        if mtf.get("conflict_score",0)>=50 or c>=60: w.append("high_timeframe_conflict")
        if q<35: w.append("low_data_quality")
        for x in factors:
            if x.available and x.freshness_score<25: w.append(f"stale_{x.factor}")
        return sorted(set(w))
    def _reason(self,s,d,score,sup,conf,miss,warn):
        parts=[f"{s}: {d} confluence score {score}% сформирован детерминированно."]
        if sup: parts.append("Поддержка: "+", ".join(sup[:4])+".")
        if conf: parts.append("Конфликт: "+", ".join(conf[:3])+".")
        if miss: parts.append("Недоступно: "+", ".join(miss[:4])+".")
        if warn: parts.append("Предупреждения: "+", ".join(warn[:4])+".")
        return " ".join(parts)
    def _safe(self,func,default,errors,label):
        try: return func()
        except Exception as exc: errors.append(f"{label}: {exc.__class__.__name__}: {exc}"); return default
    def _sym(self,v): return str(v or "").replace("/","").replace(" ","").upper()
