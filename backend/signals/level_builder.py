from __future__ import annotations


def build_trade_levels(*, action: str, price: float, atr_percent: float) -> dict[str, float]:
    stop_distance = price * (atr_percent / 100) * 0.8
    take_distance = stop_distance * 1.8

    stop = price - stop_distance if action == "BUY" else price + stop_distance
    take = price + take_distance if action == "BUY" else price - take_distance
    reward_distance = abs(take - price)
    risk_distance = abs(price - stop)
    rr = reward_distance / max(risk_distance, 1e-9)
    return {
        "stop": stop,
        "take": take,
        "risk_reward": rr,
    }
