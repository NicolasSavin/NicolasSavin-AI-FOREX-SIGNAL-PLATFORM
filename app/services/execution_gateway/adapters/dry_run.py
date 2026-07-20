from __future__ import annotations
import hashlib
from .base import BrokerAdapter
from ..models import ExecutionResult, ExecutionStatus

class DryRunExecutionAdapter(BrokerAdapter):
    name='dry_run'
    def health(self): return {'adapter': self.name, 'healthy': True, 'network': False, 'message': 'DRY_RUN adapter does not call broker APIs.'}
    def validate_order(self, order): return {'valid': bool(order.id and order.symbol and order.side and order.volume > 0), 'network': False}
    def place_order(self, order):
        v=self.validate_order(order)
        if not v['valid']:
            return ExecutionResult(order_id=order.id, adapter=self.name, mode=order.mode, success=False, status=ExecutionStatus.FAILED, message='invalid_order')
        bid='DRY-'+hashlib.sha256(order.idempotency_key.encode()).hexdigest()[:16].upper()
        safe={'order_id':order.id,'symbol':order.symbol,'side':order.side,'type':order.order_type,'volume':order.volume,'mode':order.mode}
        return ExecutionResult(order_id=order.id, adapter=self.name, mode=order.mode, success=True, status=ExecutionStatus.DRY_RUN_COMPLETED, broker_order_id=bid, message='Dry-run dispatch completed without broker network calls.', request_payload_safe=safe, response_payload_safe={'broker_order_id':bid,'network':False})
    def cancel_order(self, order_id): return {'success': True, 'order_id': order_id, 'status': 'dry_run_cancelled'}
    def get_order(self, order_id): return {'order_id': order_id, 'adapter': self.name}
    def get_positions(self): return []
    def get_account(self): return {'mode': 'DRY_RUN', 'network': False}
