from __future__ import annotations
from enum import Enum
from typing import Any
from datetime import datetime, timezone
from pydantic import BaseModel, Field

def now_iso() -> str: return datetime.now(timezone.utc).isoformat()
class ExecutionMode(str, Enum): DISABLED='DISABLED'; DRY_RUN='DRY_RUN'; PAPER='PAPER'; LIVE='LIVE'
class ExecutionSide(str, Enum): BUY='BUY'; SELL='SELL'
class ExecutionOrderType(str, Enum): MARKET='MARKET'; LIMIT='LIMIT'; STOP='STOP'
class ExecutionStatus(str, Enum):
    CREATED='CREATED'; VALIDATING='VALIDATING'; REJECTED='REJECTED'; QUEUED='QUEUED'; SENT='SENT'; ACKNOWLEDGED='ACKNOWLEDGED'; FILLED='FILLED'; PARTIALLY_FILLED='PARTIALLY_FILLED'; CANCELLED='CANCELLED'; FAILED='FAILED'; EXPIRED='EXPIRED'; DRY_RUN_COMPLETED='DRY_RUN_COMPLETED'
class PositionSizingMode(str, Enum): FIXED_VOLUME='FIXED_VOLUME'; FIXED_RISK_PERCENT='FIXED_RISK_PERCENT'; PORTFOLIO_RISK_WEIGHTED='PORTFOLIO_RISK_WEIGHTED'
class RiskSeverity(str, Enum): INFO='info'; WARNING='warning'; BLOCKER='blocker'; CRITICAL='critical'
class ExecutionRiskCheck(BaseModel):
    code:str; passed:bool; severity:str='blocker'; current_value:Any=None; limit_value:Any=None; reason:str=''
class InstrumentMetadata(BaseModel):
    symbol:str; broker_symbol:str|None=None; asset_class:str='forex'; digits:int|None=None; tick_size:float|None=None; tick_value:float|None=None; contract_size:float|None=None; minimum_volume:float=0.01; maximum_volume:float=100.0; volume_step:float=0.01
class KillSwitchState(BaseModel):
    enabled:bool=True; reason:str='default_safe_start'; activated_at:str|None=Field(default_factory=now_iso); activated_by:str|None='system'
class ExecutionOrder(BaseModel):
    id:str; idempotency_key:str; approved_signal_id:str; decision_id:str|None=None; strategy_id:str|None=None; symbol:str; side:ExecutionSide; order_type:ExecutionOrderType; volume:float=0; risk_percent:float=0; entry:Any=None; entry_zone:list[Any]=Field(default_factory=list); stop_loss:Any=None; take_profit:Any=None; targets:list[Any]=Field(default_factory=list); timeframe:str|None=None; expires_at:str|None=None; mode:ExecutionMode=ExecutionMode.DRY_RUN; status:ExecutionStatus=ExecutionStatus.CREATED; risk_checks:list[ExecutionRiskCheck]=Field(default_factory=list); blockers:list[str]=Field(default_factory=list); warnings:list[str]=Field(default_factory=list); created_at:str=Field(default_factory=now_iso); updated_at:str=Field(default_factory=now_iso)
class ExecutionResult(BaseModel):
    order_id:str; adapter:str; mode:ExecutionMode; success:bool; status:ExecutionStatus; broker_order_id:str|None=None; message:str=''; request_payload_safe:dict[str,Any]=Field(default_factory=dict); response_payload_safe:dict[str,Any]=Field(default_factory=dict); created_at:str=Field(default_factory=now_iso)
class ExecutionGatewayState(BaseModel):
    enabled:bool=True; mode:ExecutionMode=ExecutionMode.DRY_RUN; queued:int=0; completed:int=0; rejected:int=0; failed:int=0; last_dispatch_at:str|None=None; last_error:str|None=None; kill_switch:KillSwitchState=Field(default_factory=KillSwitchState)
