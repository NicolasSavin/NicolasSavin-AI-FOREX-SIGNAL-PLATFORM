from __future__ import annotations
from datetime import date
from app.business_trip.models import RailwayCarriageAvailability, SeatPreferences
from .client import TeletrainClient, MockTeletrainClient
from .config import TeletrainConfiguration
from .mapper import TeletrainMapper
class TeletrainAvailabilityProvider:
    provider_id='teletrain'
    def __init__(self, config: TeletrainConfiguration | None = None, client: TeletrainClient | None = None, mapper: TeletrainMapper | None = None) -> None:
        self.config=config or TeletrainConfiguration.from_env(); self.client=client or MockTeletrainClient(); self.mapper=mapper or TeletrainMapper()
    def get_train_places(self, train_reference: str, origin: str, destination: str, departure_date: date, policy: SeatPreferences | None = None) -> list[RailwayCarriageAvailability]:
        return [self.mapper.to_carriage(c) for c in self.client.get_places(train_reference=train_reference, origin=origin, destination=destination, departure_date=departure_date, policy=policy)]
