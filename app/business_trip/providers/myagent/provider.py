from __future__ import annotations
from datetime import date
from app.business_trip.models import RailwayCarriageAvailability, SeatPreferences
from .client import MyAgentClient, MockMyAgentClient
from .config import MyAgentConfiguration
from .mapper import MyAgentMapper
class MyAgentAvailabilityProvider:
    provider_id='myagent'
    def __init__(self, config: MyAgentConfiguration | None = None, client: MyAgentClient | None = None, mapper: MyAgentMapper | None = None) -> None:
        self.config=config or MyAgentConfiguration.from_env(); self.client=client or MockMyAgentClient(); self.mapper=mapper or MyAgentMapper()
    def get_train_places(self, train_reference: str, origin: str, destination: str, departure_date: date, policy: SeatPreferences | None = None) -> list[RailwayCarriageAvailability]:
        return [self.mapper.to_carriage(c) for c in self.client.get_places(train_reference=train_reference, origin=origin, destination=destination, departure_date=departure_date, policy=policy)]
