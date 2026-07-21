from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field

class TransportClass(str, Enum):
    seated='seated'; platzkart='platzkart'; coupe='coupe'; sv='sv'; luxury='luxury'; unknown='unknown'
class BerthPreference(str, Enum):
    any='any'; lower_only='lower_only'; upper_only='upper_only'; lower_preferred='lower_preferred'; upper_preferred='upper_preferred'
class CompartmentGender(str, Enum):
    any='any'; male='male'; female='female'; mixed='mixed'
class SeatPreferencesStatus(str, Enum):
    confirmed='confirmed'; partially_confirmed='partially_confirmed'; not_matched='not_matched'; unknown='unknown'; stale='stale'

class SeatPreferences(BaseModel):
    preferred_classes: list[TransportClass] = Field(default_factory=list)
    berth_preference: BerthPreference = BerthPreference.any
    require_same_compartment: bool = False
    require_private_compartment: bool = False
    require_adjacent_seats: bool = False
    require_same_carriage: bool = False
    exclude_side_berths: bool = False
    allow_split_group: bool = True
    compartment_gender: CompartmentGender = CompartmentGender.any
    maximum_compartments: int | None = Field(default=None, ge=1)
    strict_preferences: bool = True

class RailwayPlace(BaseModel):
    place_number: str
    place_type: str | None = None
    berth_position: str | None = None
    compartment_number: str | None = None
    carriage_number: str
    is_side: bool = False
    gender_restriction: CompartmentGender | None = None
    is_available: bool = True
    service_class: TransportClass = TransportClass.unknown
    provider_metadata: dict[str, Any] = Field(default_factory=dict)

class RailwayCarriageAvailability(BaseModel):
    train_number: str
    carriage_number: str
    carriage_type: TransportClass = TransportClass.unknown
    service_class: TransportClass = TransportClass.unknown
    available_places: list[RailwayPlace] = Field(default_factory=list)
    available_places_count: int = 0
    fetched_at: datetime | None = None
    provider: str
    status: SeatPreferencesStatus = SeatPreferencesStatus.unknown
    warnings: list[str] = Field(default_factory=list)

class SeatAllocationResult(BaseModel):
    matched: bool
    selected_places: list[RailwayPlace] = Field(default_factory=list)
    selected_carriages: list[str] = Field(default_factory=list)
    selected_compartments: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    status: SeatPreferencesStatus = SeatPreferencesStatus.unknown
    confidence: float = 0.0
    matching_combinations: int = 0

class RouteSearchRequest(BaseModel):
    origin: str
    destination: str
    departure_date: str
    passengers: int = Field(default=1, ge=1)
    seat_preferences: SeatPreferences | None = None

class RouteAvailability(BaseModel):
    status: str = 'unknown'
    seat_preferences_status: SeatPreferencesStatus = SeatPreferencesStatus.unknown
    selected_places: list[RailwayPlace] = Field(default_factory=list)
    selected_carriages: list[str] = Field(default_factory=list)
    selected_compartments: list[str] = Field(default_factory=list)
    seat_match_reasons: list[str] = Field(default_factory=list)
    seat_match_warnings: list[str] = Field(default_factory=list)
    seat_data_provider: str | None = None
    seat_data_checked_at: datetime | None = None

class SavedSearch(BaseModel):
    id: str
    request: RouteSearchRequest
    created_at: datetime = Field(default_factory=datetime.utcnow)
