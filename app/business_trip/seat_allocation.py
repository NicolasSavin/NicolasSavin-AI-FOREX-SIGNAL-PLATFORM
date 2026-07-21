from __future__ import annotations
from collections import defaultdict
from .models import BerthPreference, CompartmentGender, RailwayPlace, SeatAllocationResult, SeatPreferences, SeatPreferencesStatus, TransportClass

NO_SEAT_MAP_WARNING = 'Источник расписания не предоставляет схему мест'

def _num(p: RailwayPlace) -> int:
    try: return int(p.place_number)
    except (TypeError, ValueError): return 10**9

class SeatAllocationService:
    def match(self, passengers: int, policy: SeatPreferences | None, places: list[RailwayPlace] | None) -> SeatAllocationResult:
        policy = policy or SeatPreferences()
        if places is None:
            return SeatAllocationResult(matched=False, status=SeatPreferencesStatus.unknown, warnings=[NO_SEAT_MAP_WARNING])
        available = [p for p in places if p.is_available]
        filtered = [p for p in available if self._place_allowed(p, policy)]
        warnings: list[str] = []
        if len(filtered) < passengers:
            return SeatAllocationResult(matched=False, status=SeatPreferencesStatus.not_matched, reasons=['Недостаточно подходящих мест'], warnings=warnings)
        groups = self._candidate_groups(passengers, policy, filtered)
        if not groups:
            status = SeatPreferencesStatus.not_matched if policy.strict_preferences else SeatPreferencesStatus.partially_confirmed
            return SeatAllocationResult(matched=not policy.strict_preferences, status=status, reasons=['Требования к размещению не выполнены'], warnings=['Маршрут можно показать с предупреждением'] if not policy.strict_preferences else [])
        selected = sorted(groups[0], key=lambda p: (p.carriage_number, p.compartment_number or '', _num(p)))[:passengers]
        if policy.berth_preference in {BerthPreference.lower_preferred, BerthPreference.upper_preferred}:
            selected = self._preferred_order(groups[0], policy)[:passengers]
            if not all(self._berth_matches(p, policy.berth_preference.value.replace('_preferred','_only')) for p in selected):
                warnings.append('Предпочтение по расположению выполнено частично')
        return SeatAllocationResult(
            matched=True, selected_places=selected,
            selected_carriages=sorted({p.carriage_number for p in selected}),
            selected_compartments=sorted({f'{p.carriage_number}:{p.compartment_number}' for p in selected if p.compartment_number}),
            reasons=['Требования к местам подтверждены'], warnings=warnings,
            status=SeatPreferencesStatus.confirmed if not warnings else SeatPreferencesStatus.partially_confirmed,
            confidence=1.0, matching_combinations=len(groups))

    def _place_allowed(self, p: RailwayPlace, policy: SeatPreferences) -> bool:
        if policy.preferred_classes and p.service_class not in policy.preferred_classes: return False
        if policy.exclude_side_berths and p.is_side: return False
        if policy.compartment_gender != CompartmentGender.any and p.gender_restriction not in (None, policy.compartment_gender, CompartmentGender.any): return False
        if policy.berth_preference == BerthPreference.lower_only and not self._berth_matches(p, 'lower_only'): return False
        if policy.berth_preference == BerthPreference.upper_only and not self._berth_matches(p, 'upper_only'): return False
        return True

    def _berth_matches(self, p: RailwayPlace, pref: str) -> bool:
        value = (p.berth_position or p.place_type or '').lower()
        return ('lower' in value or 'ниж' in value) if pref == 'lower_only' else ('upper' in value or 'верх' in value)

    def _candidate_groups(self, passengers: int, policy: SeatPreferences, places: list[RailwayPlace]) -> list[list[RailwayPlace]]:
        groups = [places]
        if policy.require_same_carriage:
            by_car = defaultdict(list)
            for p in places: by_car[p.carriage_number].append(p)
            groups = list(by_car.values())
        if policy.require_same_compartment or policy.require_private_compartment:
            by_comp = defaultdict(list)
            for p in places:
                if p.compartment_number is not None: by_comp[(p.carriage_number, p.compartment_number)].append(p)
            groups = list(by_comp.values())
        candidates=[]
        for group in groups:
            ordered=sorted(group, key=_num)
            if len(ordered) < passengers: continue
            selected=ordered[:passengers]
            if policy.require_private_compartment and len(group) < self._compartment_capacity(group): continue
            comps={f'{p.carriage_number}:{p.compartment_number}' for p in selected if p.compartment_number}
            if policy.maximum_compartments and len(comps) > policy.maximum_compartments: continue
            if not policy.allow_split_group and len({p.carriage_number for p in selected}) > 1: continue
            if policy.require_adjacent_seats and not self._adjacent(selected): continue
            candidates.append(ordered)
        return candidates

    def _adjacent(self, selected: list[RailwayPlace]) -> bool:
        nums=sorted(_num(p) for p in selected)
        return nums == list(range(nums[0], nums[0]+len(nums)))

    def _compartment_capacity(self, group: list[RailwayPlace]) -> int:
        classes={p.service_class for p in group}
        if TransportClass.sv in classes: return 2
        return 4

    def _preferred_order(self, group: list[RailwayPlace], policy: SeatPreferences) -> list[RailwayPlace]:
        target='lower_only' if policy.berth_preference == BerthPreference.lower_preferred else 'upper_only'
        return sorted(group, key=lambda p: (not self._berth_matches(p, target), _num(p)))
