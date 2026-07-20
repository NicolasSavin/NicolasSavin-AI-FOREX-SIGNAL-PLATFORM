from __future__ import annotations
class PortfolioCache:
    def __init__(self) -> None: self.payload=None
    def get(self): return self.payload
    def set(self, payload): self.payload=payload; return payload
    def clear(self): self.payload=None
