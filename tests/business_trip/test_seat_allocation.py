from app.business_trip.models import *
from app.business_trip.seat_allocation import SeatAllocationService

def p(n, car='1', comp='1', berth='lower', side=False, gender=None, cls=TransportClass.coupe):
    return RailwayPlace(place_number=str(n), carriage_number=car, compartment_number=comp, berth_position=berth, is_side=side, gender_restriction=gender, service_class=cls)

def test_lower_only_and_upper_only():
    s=SeatAllocationService()
    assert s.match(2, SeatPreferences(berth_preference=BerthPreference.lower_only), [p(1), p(2, berth='upper'), p(3)]).matched
    assert not s.match(2, SeatPreferences(berth_preference=BerthPreference.upper_only), [p(1), p(2, berth='upper')]).matched

def test_lower_preferred_partial():
    r=SeatAllocationService().match(2, SeatPreferences(berth_preference=BerthPreference.lower_preferred), [p(1), p(2, berth='upper')])
    assert r.matched and r.status == SeatPreferencesStatus.partially_confirmed

def test_same_compartment_two_and_four_and_not_enough():
    s=SeatAllocationService()
    assert s.match(2, SeatPreferences(require_same_compartment=True), [p(1),p(2),p(5,comp='2')]).matched
    assert s.match(4, SeatPreferences(require_same_compartment=True), [p(1),p(2),p(3, berth='upper'),p(4, berth='upper')]).matched
    assert not s.match(4, SeatPreferences(require_same_compartment=True), [p(1),p(2),p(5,comp='2'),p(6,comp='2')]).matched

def test_private_compartment_same_carriage_adjacent_side_split_gender():
    s=SeatAllocationService()
    assert s.match(2, SeatPreferences(require_private_compartment=True), [p(1),p(2),p(3, berth='upper'),p(4, berth='upper')]).matched
    assert not s.match(2, SeatPreferences(require_same_carriage=True), [p(1,car='1'),p(2,car='2')]).matched
    assert s.match(2, SeatPreferences(require_adjacent_seats=True), [p(1),p(2)]).matched
    assert not s.match(1, SeatPreferences(exclude_side_berths=True), [p(37, side=True)]).matched
    assert not s.match(2, SeatPreferences(allow_split_group=False), [p(1,car='1'),p(2,car='2')]).matched
    assert s.match(1, SeatPreferences(compartment_gender=CompartmentGender.female), [p(1, gender=CompartmentGender.female)]).matched
    assert not s.match(1, SeatPreferences(compartment_gender=CompartmentGender.male), [p(1, gender=CompartmentGender.female)]).matched

def test_unknown_no_seat_map():
    r=SeatAllocationService().match(1, SeatPreferences(), None)
    assert r.status == SeatPreferencesStatus.unknown
    assert 'Источник расписания не предоставляет схему мест' in r.warnings
