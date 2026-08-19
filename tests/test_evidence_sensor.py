from unittest.mock import Mock

from homeassistant.helpers.entity import EntityCategory

from custom_components.enocean_ptm216b.evidence_capture import ENOCEAN_MANUFACTURER_ID
from custom_components.enocean_ptm216b.identity import device_identifier
from custom_components.enocean_ptm216b.runtime_data import Ptm216bRuntimeData
from custom_components.enocean_ptm216b.sensor import Ptm216bEvidenceCaptureSensor

SECRET = b"\x01" * 32
ADDRESS = "AA:BB:CC:DD:EE:FF"
IDENTIFIER = device_identifier(SECRET, ADDRESS)
SIGNATURE_STUB = b"\x00\x00\x00\x00"


def _frame(counter: bytes = b"\x01\x00\x00\x00", status: int = 0x10) -> dict:
    return {ENOCEAN_MANUFACTURER_ID: counter + bytes([status]) + SIGNATURE_STUB}


def test_evidence_sensor_is_inert_with_no_attributes_before_any_capture():
    entry = Mock(entry_id="entry-id")
    entry.runtime_data = Ptm216bRuntimeData(_hmac_secret=SECRET)
    sensor = Ptm216bEvidenceCaptureSensor(entry)

    assert sensor.native_value == "inert"
    assert sensor.extra_state_attributes == {}
    assert sensor.entity_category is EntityCategory.DIAGNOSTIC


def test_evidence_sensor_reports_only_live_count_while_collecting():
    entry = Mock(entry_id="entry-id")
    entry.runtime_data = Ptm216bRuntimeData(_hmac_secret=SECRET)
    entry.runtime_data.designated_identifier = IDENTIFIER
    entry.runtime_data.start_evidence_capture(Mock(return_value=Mock()))
    sensor = Ptm216bEvidenceCaptureSensor(entry)

    entry.runtime_data.record_advertisement_observation(ADDRESS, _frame(), False)

    assert sensor.native_value == "collecting"
    assert sensor.extra_state_attributes == {"callbacks_accepted": 1}
    entry.runtime_data.cancel_evidence_capture()


def test_evidence_sensor_reports_full_structural_summary_when_complete():
    entry = Mock(entry_id="entry-id")
    entry.runtime_data = Ptm216bRuntimeData(_hmac_secret=SECRET)
    entry.runtime_data.designated_identifier = IDENTIFIER
    schedule = Mock(return_value=Mock())
    entry.runtime_data.start_evidence_capture(schedule)
    sensor = Ptm216bEvidenceCaptureSensor(entry)

    entry.runtime_data.evidence_collector.record_callback(IDENTIFIER, _frame(), False)
    schedule.call_args.args[1]()

    assert sensor.native_value == "complete"
    attrs = sensor.extra_state_attributes
    assert attrs["callbacks_accepted"] == 1
    assert attrs["manufacturer_data_keys"] == [ENOCEAN_MANUFACTURER_ID]
    assert attrs["status_xor_values"] == [0]
    assert attrs["duplicate_identical_count"] == 0
    assert attrs["any_connectable_seen"] is False


def test_evidence_sensor_reports_no_data_with_no_attributes():
    entry = Mock(entry_id="entry-id")
    entry.runtime_data = Ptm216bRuntimeData(_hmac_secret=SECRET)
    entry.runtime_data.designated_identifier = IDENTIFIER
    schedule = Mock(return_value=Mock())
    entry.runtime_data.start_evidence_capture(schedule)
    sensor = Ptm216bEvidenceCaptureSensor(entry)

    schedule.call_args.args[1]()

    assert sensor.native_value == "no_data"
    assert sensor.extra_state_attributes == {}


def test_evidence_sensor_reports_aborted_with_no_attributes():
    entry = Mock(entry_id="entry-id")
    entry.runtime_data = Ptm216bRuntimeData(_hmac_secret=SECRET)
    entry.runtime_data.designated_identifier = IDENTIFIER
    entry.runtime_data.start_evidence_capture(Mock(return_value=Mock()))
    sensor = Ptm216bEvidenceCaptureSensor(entry)

    entry.runtime_data.evidence_collector.record_callback(
        IDENTIFIER, {ENOCEAN_MANUFACTURER_ID: bytes(24)}, False
    )

    assert sensor.native_value == "aborted"
    assert sensor.extra_state_attributes == {}


async def test_evidence_sensor_subscribes_and_unsubscribes_the_runtime_listener():
    entry = Mock(entry_id="entry-id")
    entry.runtime_data = Ptm216bRuntimeData(_hmac_secret=SECRET)
    sensor = Ptm216bEvidenceCaptureSensor(entry)
    sensor.async_write_ha_state = Mock()

    await sensor.async_added_to_hass()
    assert entry.runtime_data.evidence_state_listener is sensor.async_write_ha_state

    await sensor.async_will_remove_from_hass()
    assert entry.runtime_data.evidence_state_listener is None
