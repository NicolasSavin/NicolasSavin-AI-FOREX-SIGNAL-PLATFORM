from __future__ import annotations
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field

class DecisionAction(str, Enum):
    STRONG_BUY='STRONG_BUY'; BUY='BUY'; WAIT='WAIT'; SELL='SELL'; STRONG_SELL='STRONG_SELL'; IGNORE='IGNORE'; BLOCKED='BLOCKED'; NO_DATA='NO_DATA'
class DecisionConfidence(str, Enum):
    VERY_LOW='VERY_LOW'; LOW='LOW'; MEDIUM='MEDIUM'; HIGH='HIGH'; VERY_HIGH='VERY_HIGH'
class DecisionStability(str, Enum):
    UNSTABLE='UNSTABLE'; FRAGILE='FRAGILE'; MODERATE='MODERATE'; STABLE='STABLE'; VERY_STABLE='VERY_STABLE'
class DecisionReadiness(str, Enum):
    NOT_READY='NOT_READY'; WATCH='WATCH'; READY='READY'; READY_WITH_WARNINGS='READY_WITH_WARNINGS'; BLOCKED='BLOCKED'
class DecisionEvidence(BaseModel):
    source:str; available:bool=False; direction:str|None=None; score:float|None=None; confidence:float|None=None; weight:float=0; contribution:float=0; supporting:bool=False; conflicting:bool=False; stale:bool=False; reason:str=''; updated_at:str|None=None
class DecisionCondition(BaseModel):
    code:str; description:str; current_value:Any=None; required_value:Any=None; satisfied:bool=False; severity:str='info'
class ExecutionCandidate(BaseModel):
    symbol:str; action:DecisionAction; direction:str|None=None; readiness:DecisionReadiness; score:float; confidence:float; entry:Any=None; entry_zone:list[Any]=Field(default_factory=list); stop_loss:Any=None; take_profit:Any=None; targets:list[Any]=Field(default_factory=list); timeframe:str|None=None; expires_at:str|None=None; blockers:list[str]=Field(default_factory=list); warnings:list[str]=Field(default_factory=list); decision_id:str; generated_at:str
class ExplainableDecision(BaseModel):
    symbol:str; action:DecisionAction; direction:str|None=None; actionable:bool=False; readiness:DecisionReadiness; decision_score:float=0; confidence_score:float=0; confidence_label:DecisionConfidence; stability_score:float=0; stability_label:DecisionStability
    opportunity_score:float=0; confluence_score:float=0; agreement_score:float=0; conflict_score:float=0; data_quality_score:float=0; freshness_score:float=0; validation_score:float|None=None; author_score:float=0; performance_score:float=0; dominant_timeframe:str|None=None; urgency:str|None=None
    entry:Any=None; entry_zone:list[Any]=Field(default_factory=list); stop_loss:Any=None; take_profit:Any=None; targets:list[Any]=Field(default_factory=list); evidence:list[DecisionEvidence]=Field(default_factory=list)
    supporting_reasons:list[str]=Field(default_factory=list); conflicting_reasons:list[str]=Field(default_factory=list); blocking_reasons:list[str]=Field(default_factory=list); warnings:list[str]=Field(default_factory=list); missing_data:list[str]=Field(default_factory=list); upgrade_conditions:list[DecisionCondition]=Field(default_factory=list); downgrade_conditions:list[DecisionCondition]=Field(default_factory=list)
    primary_reason:str=''; concise_explanation:str=''; audit_explanation:str=''; source_versions:dict[str,Any]=Field(default_factory=dict); execution_candidate:ExecutionCandidate|None=None; updated_at:str
class DecisionCollection(BaseModel):
    items:list[ExplainableDecision]=Field(default_factory=list); total:int=0; actionable_count:int=0; ready_count:int=0; watch_count:int=0; blocked_count:int=0; ignored_count:int=0; no_data_count:int=0; generated_at:str; diagnostics:dict[str,Any]=Field(default_factory=dict)
