from __future__ import annotations
from enum import Enum
from typing import Any
from datetime import datetime, timezone
from pydantic import BaseModel, Field, field_validator, model_validator

MAX_DEPTH=4; MAX_RULES=50
def now_iso(): return datetime.now(timezone.utc).isoformat()
def clean(s:str)->str: return ''.join(ch for ch in str(s or '') if ch.isprintable())[:500]
class StrategyStatus(str,Enum): DRAFT='DRAFT'; ACTIVE='ACTIVE'; PAUSED='PAUSED'; ARCHIVED='ARCHIVED'; INVALID='INVALID'
class StrategyMode(str,Enum): SIGNAL_ONLY='SIGNAL_ONLY'; PAPER='PAPER'; EXECUTION_READY='EXECUTION_READY'; OBSERVATION='OBSERVATION'
class RuleOperator(str,Enum): EQ='EQ'; NE='NE'; GT='GT'; GTE='GTE'; LT='LT'; LTE='LTE'; IN='IN'; NOT_IN='NOT_IN'; EXISTS='EXISTS'; NOT_EXISTS='NOT_EXISTS'; CONTAINS='CONTAINS'; NOT_CONTAINS='NOT_CONTAINS'; BETWEEN='BETWEEN'
class RuleField(str,Enum):
    action='action'; direction='direction'; readiness='readiness'; decision_score='decision_score'; confidence_score='confidence_score'; stability_score='stability_score'; opportunity_score='opportunity_score'; confluence_score='confluence_score'; agreement_score='agreement_score'; conflict_score='conflict_score'; data_quality_score='data_quality_score'; freshness_score='freshness_score'; validation_score='validation_score'; author_score='author_score'; performance_score='performance_score'; dominant_timeframe='dominant_timeframe'; urgency='urgency'; entry='entry'; entry_zone='entry_zone'; stop_loss='stop_loss'; take_profit='take_profit'; targets='targets'; actionable='actionable'; blocker_count='blocker_count'; warning_count='warning_count'; missing_data='missing_data'; symbol='symbol'; timeframe='timeframe'
class Combinator(str,Enum): ALL='ALL'; ANY='ANY'; NONE='NONE'
class StrategyApprovalStatus(str,Enum): APPROVED='APPROVED'; APPROVED_WITH_WARNINGS='APPROVED_WITH_WARNINGS'; REJECTED='REJECTED'; WATCH_ONLY='WATCH_ONLY'; INVALID_STRATEGY='INVALID_STRATEGY'; NO_DECISION='NO_DECISION'; EXPIRED='EXPIRED'
class StrategyRiskPolicy(BaseModel):
    require_entry:bool=False; require_stop_loss:bool=False; require_take_profit:bool=False; require_targets:bool=False; require_validation:bool=False
    minimum_rr:float|None=None; maximum_conflict:float|None=None; minimum_data_quality:float|None=None; minimum_freshness:float|None=None
    allowed_readiness:list[str]=Field(default_factory=lambda:['READY']); blocked_warning_codes:list[str]=Field(default_factory=list); blocked_missing_data:list[str]=Field(default_factory=list)
    maximum_signal_age_minutes:int|None=None; require_unique_symbol:bool=False; allow_ready_with_warnings:bool=False; allow_watch_mode:bool=False
class StrategyRule(BaseModel):
    id:str; field:RuleField; operator:RuleOperator; value:Any=None; enabled:bool=True; required:bool=False; weight:float=1.0; description:str=''
    @field_validator('description')
    @classmethod
    def _clean_desc(cls,v): return clean(v)
class StrategyRuleGroup(BaseModel):
    id:str; combinator:Combinator=Combinator.ALL; rules:list[StrategyRule]=Field(default_factory=list); groups:list['StrategyRuleGroup']=Field(default_factory=list)
class StrategyDefinition(BaseModel):
    id:str; name:str; description:str=''; status:StrategyStatus=StrategyStatus.DRAFT; mode:StrategyMode=StrategyMode.SIGNAL_ONLY; priority:int=100; enabled:bool=True
    symbols:list[str]=Field(default_factory=list); excluded_symbols:list[str]=Field(default_factory=list); timeframes:list[str]=Field(default_factory=list); directions:list[str]=Field(default_factory=list)
    rules:StrategyRuleGroup=Field(default_factory=lambda:StrategyRuleGroup(id='root')); minimum_pass_score:float=0; require_all_required_rules:bool=True; risk_policy:StrategyRiskPolicy=Field(default_factory=StrategyRiskPolicy)
    created_at:str=Field(default_factory=now_iso); updated_at:str=Field(default_factory=now_iso); version:int=1; tags:list[str]=Field(default_factory=list); metadata:dict[str,Any]=Field(default_factory=dict)
    @field_validator('name','description')
    @classmethod
    def _clean(cls,v): return clean(v)
    @field_validator('symbols','excluded_symbols','timeframes','directions')
    @classmethod
    def _upper(cls,v): return [str(x).strip().upper() for x in (v or []) if str(x).strip()][:100]
    @model_validator(mode='after')
    def _limits(self):
        def walk(g,d=1):
            if d>MAX_DEPTH: raise ValueError('rule group nesting depth exceeded')
            return len(g.rules)+sum(walk(x,d+1) for x in g.groups)
        if walk(self.rules)>MAX_RULES: raise ValueError('too many strategy rules')
        return self
class StrategyRuleResult(BaseModel):
    rule_id:str; field:str; operator:str; passed:bool; current_value:Any=None; expected_value:Any=None; required:bool=False; contribution:float=0; reason:str=''; severity:str='info'
class StrategyEvaluation(BaseModel):
    strategy_id:str; strategy_name:str; strategy_version:int; symbol:str|None=None; decision_id:str|None=None; passed:bool=False; approval_status:StrategyApprovalStatus=StrategyApprovalStatus.REJECTED; pass_score:float=0; required_rules_passed:int=0; required_rules_failed:int=0; optional_rules_passed:int=0; optional_rules_failed:int=0; rule_results:list[StrategyRuleResult]=Field(default_factory=list); risk_policy_passed:bool=False; risk_blockers:list[str]=Field(default_factory=list); warnings:list[str]=Field(default_factory=list); primary_reason:str=''; evaluated_at:str=Field(default_factory=now_iso)
class ApprovedSignal(BaseModel):
    id:str; symbol:str; direction:str|None=None; action:str; decision_id:str; strategy_id:str; strategy_name:str; strategy_version:int; approval_status:StrategyApprovalStatus; approval_score:float; readiness:str; confidence:float; stability:float; entry:Any=None; entry_zone:list[Any]=Field(default_factory=list); stop_loss:Any=None; take_profit:Any=None; targets:list[Any]=Field(default_factory=list); timeframe:str|None=None; expires_at:str|None=None; blockers:list[str]=Field(default_factory=list); warnings:list[str]=Field(default_factory=list); approval_reason:str=''; created_at:str=Field(default_factory=now_iso)
