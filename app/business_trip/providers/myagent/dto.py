from __future__ import annotations
from pydantic import BaseModel

class MyAgentPlaceDTO(BaseModel):
    carriage: str
    compartment: str | None = None
    place_number: str
    place_type: str | None = None
    berth_position: str | None = None
    gender_restriction: str | None = None
    available: bool = True
    service_class: str | None = None
    is_side: bool = False

class MyAgentCarriageDTO(BaseModel):
    train_number: str
    carriage: str
    carriage_type: str | None = None
    service_class: str | None = None
    places: list[MyAgentPlaceDTO] = []
