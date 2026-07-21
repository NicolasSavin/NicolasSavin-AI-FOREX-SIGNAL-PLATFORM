from __future__ import annotations
from .models import BerthPreference, SeatAllocationResult, SeatPreferencesStatus

class MonitoringEngine:
    def important_seat_changes(self, previous: SeatAllocationResult, current: SeatAllocationResult, berth_preference: BerthPreference | None = None) -> list[str]:
        notices=[]
        if previous.status in {SeatPreferencesStatus.unknown, SeatPreferencesStatus.not_matched} and current.status == SeatPreferencesStatus.confirmed:
            notices.append('Требования к местам подтверждены')
        if berth_preference == BerthPreference.lower_only and not previous.matched and current.matched:
            notices.append('Появились подходящие нижние места')
        if not any('купе' in r.lower() for r in previous.reasons) and any('купе' in r.lower() for r in current.reasons + current.selected_compartments):
            notices.append('Появилось купе для всей группы')
        if len(current.selected_carriages) == 1 and len(previous.selected_carriages) != 1:
            notices.append('Появились места в одном вагоне')
        if current.matching_combinations > previous.matching_combinations:
            notices.append('Увеличилось количество подходящих комбинаций')
        return notices
