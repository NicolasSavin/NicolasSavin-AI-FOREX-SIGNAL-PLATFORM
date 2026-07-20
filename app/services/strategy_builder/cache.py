class StrategyCache:
    def __init__(self): self.payload=None
    def invalidate(self): self.payload=None
    def get(self): return self.payload
    def set(self,p): self.payload=p; return p
