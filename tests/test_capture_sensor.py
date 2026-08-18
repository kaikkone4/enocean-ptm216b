from unittest.mock import Mock

from homeassistant.helpers.entity import EntityCategory

from custom_components.enocean_ptm216b.runtime_data import (
    MINIMUM_DESIGNATION_OBSERVATIONS,
    Ptm216bRuntimeData,
)
from custom_components.enocean_ptm216b.sensor import Ptm216bDesignationCaptureSensor

ADDRESS = "AA:BB:CC:DD:EE:FF"
SECRET = b"private-secret-material"


def test_capture_sensor_exposes_only_safe_aggregate_state():
    entry = Mock(entry_id="entry-id")
    entry.runtime_data = Ptm216bRuntimeData(_hmac_secret=SECRET)
    sensor = Ptm216bDesignationCaptureSensor(entry)

    assert sensor.native_value == "inert"
    assert sensor.extra_state_attributes == {
        "observation_count": 0,
        "designation_outcome": "no_selection",
    }
    assert sensor.entity_category is EntityCategory.DIAGNOSTIC

    serialized = repr((sensor.native_value, sensor.extra_state_attributes))
    assert ADDRESS not in serialized
    assert repr(SECRET) not in serialized
    assert "candidate" not in serialized
    assert "identifier" not in serialized


def test_capture_sensor_reports_active_count_and_generic_selected_outcome():
    schedule = Mock(return_value=Mock())
    entry = Mock(entry_id="entry-id")
    entry.runtime_data = Ptm216bRuntimeData(_hmac_secret=SECRET)
    sensor = Ptm216bDesignationCaptureSensor(entry)

    entry.runtime_data.start_designation_capture(schedule)
    for observed_at in range(MINIMUM_DESIGNATION_OBSERVATIONS):
        entry.runtime_data.record_designation_candidate(ADDRESS, float(observed_at))

    assert sensor.native_value == "active"
    assert sensor.extra_state_attributes == {
        "observation_count": MINIMUM_DESIGNATION_OBSERVATIONS,
        "designation_outcome": "no_selection",
    }

    schedule.call_args.args[1]()

    assert sensor.native_value == "inert"
    assert sensor.extra_state_attributes == {
        "observation_count": MINIMUM_DESIGNATION_OBSERVATIONS,
        "designation_outcome": "selected",
    }
    assert entry.runtime_data.designated_identifier not in repr(
        sensor.extra_state_attributes
    )


def test_capture_changes_notify_diagnostic_sensor_without_identifiers():
    schedule = Mock(return_value=Mock())
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    listener = Mock()
    runtime.capture_state_listener = listener

    runtime.start_designation_capture(schedule)
    runtime.record_designation_candidate(ADDRESS, 1.0)
    schedule.call_args.args[1]()

    assert listener.call_count == 3
    assert all(call.args == () for call in listener.call_args_list)
