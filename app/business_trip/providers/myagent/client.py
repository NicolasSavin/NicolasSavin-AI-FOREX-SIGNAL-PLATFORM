from __future__ import annotations
from typing import Protocol
from .dto import MyAgentCarriageDTO
class MyAgentClient(Protocol):
    def search_trains(self, *args, **kwargs) -> list[dict]: ...
    def get_carriages(self, *args, **kwargs) -> list[MyAgentCarriageDTO]: ...
    def get_places(self, *args, **kwargs) -> list[MyAgentCarriageDTO]: ...
    def get_place_types(self, *args, **kwargs) -> list[dict]: ...
class MockMyAgentClient:
    def __init__(self, carriages: list[MyAgentCarriageDTO] | None = None) -> None: self.carriages=carriages or []
    def search_trains(self, *args, **kwargs) -> list[dict]: return []
    def get_carriages(self, *args, **kwargs) -> list[MyAgentCarriageDTO]: return self.carriages
    def get_places(self, *args, **kwargs) -> list[MyAgentCarriageDTO]: return self.carriages
    def get_place_types(self, *args, **kwargs) -> list[dict]: return []
