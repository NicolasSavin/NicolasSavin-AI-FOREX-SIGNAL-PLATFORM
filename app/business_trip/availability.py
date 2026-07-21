from __future__ import annotations
from datetime import datetime
from .models import RailwayCarriageAvailability, RouteAvailability, SeatPreferences, SeatPreferencesStatus
from .seat_allocation import SeatAllocationService, NO_SEAT_MAP_WARNING

class AvailabilityEngine:
    def __init__(self, matcher: SeatAllocationService | None = None) -> None:
        self.matcher = matcher or SeatAllocationService()
    def check_seats(self, passengers: int, policy: SeatPreferences | None, carriages: list[RailwayCarriageAvailability] | None) -> RouteAvailability:
        if not carriages:
            return RouteAvailability(status='unknown', seat_preferences_status=SeatPreferencesStatus.unknown, seat_match_warnings=[NO_SEAT_MAP_WARNING])
        places=[p for c in carriages for p in c.available_places]
        result=self.matcher.match(passengers, policy, places)
        provider=carriages[0].provider if carriages else None
        checked=max((c.fetched_at for c in carriages if c.fetched_at), default=datetime.utcnow())
        return RouteAvailability(
            status='available' if result.status == SeatPreferencesStatus.confirmed else 'unknown',
            seat_preferences_status=result.status,
            selected_places=result.selected_places,
            selected_carriages=result.selected_carriages,
            selected_compartments=result.selected_compartments,
            seat_match_reasons=result.reasons,
            seat_match_warnings=result.warnings,
            seat_data_provider=provider,
            seat_data_checked_at=checked,
        )
