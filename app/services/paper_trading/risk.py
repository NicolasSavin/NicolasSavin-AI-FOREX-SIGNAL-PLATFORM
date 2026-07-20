from __future__ import annotations
class PaperRiskEngine:
    def size(self, balance: float, risk_percent: float, entry: float, stop_loss: float) -> tuple[float,float]:
        risk_amount = max(0.0, balance) * max(0.0, risk_percent) / 100.0
        risk_per_unit = abs(entry - stop_loss)
        return risk_amount, (risk_amount / risk_per_unit if risk_per_unit else 0.0)
