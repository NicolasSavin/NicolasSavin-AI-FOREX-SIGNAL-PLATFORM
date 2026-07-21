from datetime import date
from app.business_trip.availability import AvailabilityEngine
from app.business_trip.models import *
from app.business_trip.monitoring import MonitoringEngine
from app.business_trip.saved_searches import SavedSearchRepository
from app.business_trip.providers.myagent import MockMyAgentClient, MyAgentAvailabilityProvider, MyAgentCarriageDTO, MyAgentPlaceDTO, MyAgentMapper
from app.business_trip.providers.teletrain import MockTeletrainClient, TeletrainAvailabilityProvider, TeletrainCarriageDTO, TeletrainPlaceDTO, TeletrainMapper

def test_myagent_mock_and_mapper():
    dto=MyAgentCarriageDTO(train_number='001', carriage='7', service_class='coupe', places=[MyAgentPlaceDTO(carriage='7', compartment='2', place_number='5', berth_position='lower', available=True, service_class='coupe')])
    places=MyAgentAvailabilityProvider(client=MockMyAgentClient([dto])).get_train_places('001','MOW','LED',date.today())
    assert places[0].available_places[0].carriage_number == '7'
    assert MyAgentMapper().to_carriage(dto).provider == 'myagent'

def test_teletrain_mock_and_mapper():
    dto=TeletrainCarriageDTO(train_number='002', carriage='8', service_class='sv', places=[TeletrainPlaceDTO(carriage='8', compartment='1', place_number='1', berth_position='lower', available=True, service_class='sv')])
    places=TeletrainAvailabilityProvider(client=MockTeletrainClient([dto])).get_train_places('002','MOW','KZN',date.today())
    assert places[0].provider == 'teletrain'
    assert TeletrainMapper().to_carriage(dto).available_places_count == 1

def test_yandex_schedule_not_confirmed_and_api_backwards_compatible():
    assert RouteSearchRequest(origin='a', destination='b', departure_date='2026-08-01').seat_preferences is None
    r=AvailabilityEngine().check_seats(1, SeatPreferences(), None)
    assert r.seat_preferences_status == SeatPreferencesStatus.unknown

def test_saved_search_preserves_preferences_and_monitoring_detects_compartment():
    repo=SavedSearchRepository(); pref=SeatPreferences(berth_preference=BerthPreference.lower_only, require_same_compartment=True)
    saved=repo.save(SavedSearch(id='s1', request=RouteSearchRequest(origin='a', destination='b', departure_date='2026-08-01', passengers=2, seat_preferences=pref)))
    assert repo.get('s1').request.seat_preferences.require_same_compartment
    prev=SeatAllocationResult(matched=False, status=SeatPreferencesStatus.not_matched)
    cur=SeatAllocationResult(matched=True, status=SeatPreferencesStatus.confirmed, selected_carriages=['1'], selected_compartments=['1:1'], reasons=['Появилось купе'])
    notices=MonitoringEngine().important_seat_changes(prev, cur, BerthPreference.lower_only)
    assert 'Появились подходящие нижние места' in notices and 'Появилось купе для всей группы' in notices

def test_secrets_absent_from_safe_config(monkeypatch):
    monkeypatch.setenv('MYAGENT_PASSWORD','secret')
    from app.business_trip.providers.myagent import MyAgentConfiguration
    assert 'secret' not in str(MyAgentConfiguration.from_env().safe_dict())
