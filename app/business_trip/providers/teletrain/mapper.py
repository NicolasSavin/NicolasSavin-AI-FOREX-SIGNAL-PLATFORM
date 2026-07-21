from __future__ import annotations
from datetime import datetime
from app.business_trip.models import CompartmentGender, RailwayCarriageAvailability, RailwayPlace, SeatPreferencesStatus, TransportClass
from .dto import TeletrainCarriageDTO, TeletrainPlaceDTO

def _enum(enum, value, default):
    try: return enum(value) if value else default
    except ValueError: return default
class TeletrainMapper:
    def to_place(self, dto: TeletrainPlaceDTO) -> RailwayPlace:
        return RailwayPlace(place_number=dto.place_number, place_type=dto.place_type, berth_position=dto.berth_position, compartment_number=dto.compartment, carriage_number=dto.carriage, is_side=dto.is_side, gender_restriction=_enum(CompartmentGender, dto.gender_restriction, None), is_available=dto.available, service_class=_enum(TransportClass, dto.service_class, TransportClass.unknown), provider_metadata={'source':'teletrain'})
    def to_carriage(self, dto: TeletrainCarriageDTO) -> RailwayCarriageAvailability:
        places=[self.to_place(p) for p in dto.places if p.available]
        return RailwayCarriageAvailability(train_number=dto.train_number, carriage_number=dto.carriage, carriage_type=_enum(TransportClass,dto.carriage_type,TransportClass.unknown), service_class=_enum(TransportClass,dto.service_class,TransportClass.unknown), available_places=places, available_places_count=len(places), fetched_at=datetime.utcnow(), provider='teletrain', status=SeatPreferencesStatus.confirmed)
