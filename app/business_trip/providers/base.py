from __future__ import annotations
from datetime import date
from typing import Protocol
from app.business_trip.models import RailwayCarriageAvailability, SeatPreferences

class RailwayAvailabilityProvider(Protocol):
    provider_id: str
    def get_train_places(self, train_reference: str, origin: str, destination: str, departure_date: date, policy: SeatPreferences | None = None) -> list[RailwayCarriageAvailability]: ...
