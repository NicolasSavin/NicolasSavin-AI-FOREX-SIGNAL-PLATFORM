from __future__ import annotations


def infer_action(trend: str) -> str:
    return "BUY" if trend == "up" else "SELL"


def has_minimum_confluence(*, bos: bool, liquidity_sweep: bool, order_block: bool, ltf_pattern: bool) -> bool:
    confluence = [bos, liquidity_sweep, order_block, ltf_pattern]
    return sum(1 for item in confluence if item) >= 3
