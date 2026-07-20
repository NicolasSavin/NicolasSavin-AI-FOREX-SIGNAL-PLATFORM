from __future__ import annotations
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field

class OpportunityStatus(str, Enum):
    ACTIONABLE="ACTIONABLE"; WATCH="WATCH"; BLOCKED="BLOCKED"; IGNORE="IGNORE"; NO_DATA="NO_DATA"; EXPIRED="EXPIRED"
class OpportunityUrgency(str, Enum):
    LOW="LOW"; NORMAL="NORMAL"; HIGH="HIGH"; IMMEDIATE="IMMEDIATE"
class OpportunityRiskContext(BaseModel):
    stop_loss_available: bool=False; take_profit_available: bool=False; targets_available: bool=False; entry_available: bool=False; entry_zone_available: bool=False; validation_available: bool=False
    average_rr: float|None=None; historical_win_rate: float|None=None; max_adverse_excursion: float|None=None; warning_flags: list[str]=Field(default_factory=list)
class OpportunityState(BaseModel):
    rank:int=0; symbol:str; direction:str="NO_DATA"; recommendation:str="NO_DATA"; status:OpportunityStatus=OpportunityStatus.NO_DATA; urgency:OpportunityUrgency=OpportunityUrgency.LOW; actionable:bool=False
    opportunity_score:float=Field(default=0,ge=0,le=100); confluence_score:float=Field(default=0,ge=0,le=100); confidence:float=Field(default=0,ge=0,le=100); agreement_score:float=Field(default=0,ge=0,le=100); conflict_score:float=Field(default=0,ge=0,le=100); data_quality_score:float=Field(default=0,ge=0,le=100); freshness_score:float=Field(default=0,ge=0,le=100)
    validation_score:float=0; author_score:float=0; performance_score:float=0; dominant_timeframe:str|None=None; review_count:int=0; author_count:int=0; validated_signal_count:int=0; latest_review_at:str|None=None
    entry:Any=None; entry_zone:list[Any]=Field(default_factory=list); stop_loss:Any=None; take_profit:Any=None; targets:list[Any]=Field(default_factory=list); risk_context:OpportunityRiskContext=Field(default_factory=OpportunityRiskContext)
    supporting_factors:list[str]=Field(default_factory=list); conflicting_factors:list[str]=Field(default_factory=list); blocking_reasons:list[str]=Field(default_factory=list); warnings:list[str]=Field(default_factory=list); primary_reason:str=""; updated_at:str
class OpportunityCollection(BaseModel):
    items:list[OpportunityState]=Field(default_factory=list); total:int=0; actionable_count:int=0; watch_count:int=0; blocked_count:int=0; ignored_count:int=0; no_data_count:int=0; generated_at:str; diagnostics:dict[str,Any]=Field(default_factory=dict)
