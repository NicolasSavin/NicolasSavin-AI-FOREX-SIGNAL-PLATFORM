from __future__ import annotations
from typing import Protocol
from .dto import TeletrainCarriageDTO
class TeletrainClient(Protocol):
    def search_trains(self, *args, **kwargs) -> list[dict]: ...
    def get_carriages(self, *args, **kwargs) -> list[TeletrainCarriageDTO]: ...
    def get_places(self, *args, **kwargs) -> list[TeletrainCarriageDTO]: ...
    def get_place_types(self, *args, **kwargs) -> list[dict]: ...
class MockTeletrainClient:
    def __init__(self, carriages: list[TeletrainCarriageDTO] | None = None) -> None: self.carriages=carriages or []
    def search_trains(self, *args, **kwargs) -> list[dict]: return []
    def get_carriages(self, *args, **kwargs) -> list[TeletrainCarriageDTO]: return self.carriages
    def get_places(self, *args, **kwargs) -> list[TeletrainCarriageDTO]: return self.carriages
    def get_place_types(self, *args, **kwargs) -> list[dict]: return []
