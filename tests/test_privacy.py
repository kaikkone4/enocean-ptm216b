from unittest.mock import Mock

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.enocean_ptm216b.runtime_data import (
    MINIMUM_DESIGNATION_OBSERVATIONS,
    Ptm216bRuntimeData,
)
from custom_components.enocean_ptm216b.sensor import Ptm216bDesignationCaptureSensor

ADDRESS = "AA:BB:CC:DD:EE:FF"
SECRET = b"private-secret-marker" * 2


def test_successful_designation_never_persists_or_exposes_identity_material():
    entry = MockConfigEntry(data={})
    entry.runtime_data = Ptm216bRuntimeData(_hmac_secret=SECRET)
    schedule = Mock(side_effect=[Mock(), Mock(), Mock()])
    entry.runtime_data.start_designation_capture(schedule)
    schedule.call_args_list[0].args[1]()
    for observed_at in range(MINIMUM_DESIGNATION_OBSERVATIONS):
        entry.runtime_data.record_designation_candidate(ADDRESS, float(observed_at))
    schedule.call_args_list[1].args[1]()
    for observed_at in range(MINIMUM_DESIGNATION_OBSERVATIONS):
        entry.runtime_data.record_designation_candidate(ADDRESS, float(observed_at))
    schedule.call_args_list[2].args[1]()

    identifier = entry.runtime_data.designated_identifier
    sensor = Ptm216bDesignationCaptureSensor(entry)
    exposed = repr((sensor.native_value, sensor.extra_state_attributes, entry.data))

    assert identifier is not None
    assert entry.data == {}
    assert ADDRESS not in exposed
    assert repr(SECRET) not in exposed
    assert identifier not in exposed
    assert entry.runtime_data.designation_candidates == {}
    assert entry.runtime_data.first_window_identifier is None


def test_candidate_records_never_retain_address_payload_or_timing():
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    runtime.start_designation_capture(Mock(return_value=Mock()))

    runtime.record_designation_candidate(ADDRESS, 1234.5)

    candidate = next(iter(runtime.designation_candidates.values()))
    assert vars(candidate) == {"observation_count": 1}
    assert ADDRESS not in repr(runtime)
    assert repr(SECRET) not in repr(runtime)
    assert "1234.5" not in repr(runtime)
