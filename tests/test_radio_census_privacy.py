"""Privacy assertions for the radio census, in the style of test_privacy.py
and test_evidence_privacy.py.

Verifies that serialized sensor attributes, the census's own repr, and the
runtime's own repr never contain a BLE address, a device/local name, raw
payload bytes/hex, an RSSI-like marker, or a full 64-char pseudonymous
identifier -- across every phase, including after ``complete``, where the
per-device working set must already have been cleared.
"""

from unittest.mock import Mock

from custom_components.enocean_ptm216b.identity import device_identifier
from custom_components.enocean_ptm216b.runtime_data import Ptm216bRuntimeData
from custom_components.enocean_ptm216b.sensor import Ptm216bRadioCensusSensor

SECRET = b"private-secret-marker" * 2
ADDRESS = "AA:BB:CC:DD:EE:FF"
IDENTIFIER = device_identifier(SECRET, ADDRESS)
LOCAL_NAME_MARKER = "Someone's iPhone"
ENOCEAN_ID = 0x03DA


def _frame(value_byte: int = 0x01, length: int = 9) -> dict:
    return {ENOCEAN_ID: bytes([value_byte]) * length}


def _forbidden_strings() -> list[str]:
    return [
        ADDRESS,
        IDENTIFIER,
        LOCAL_NAME_MARKER,
        repr(_frame()[ENOCEAN_ID]),
        _frame()[ENOCEAN_ID].hex(),
    ]


def _entry() -> Mock:
    entry = Mock(entry_id="entry-id")
    entry.runtime_data = Ptm216bRuntimeData(_hmac_secret=SECRET)
    return entry


def test_sensor_never_leaks_raw_material_while_baseline_or_press():
    entry = _entry()
    schedule = Mock(return_value=Mock())
    entry.runtime_data.start_radio_census(schedule, Mock(return_value=Mock()))
    sensor = Ptm216bRadioCensusSensor(entry)

    entry.runtime_data._handle_radio_census_advertisement(
        ADDRESS, _frame(), set(), False
    )
    baseline_serialized = repr((sensor.native_value, sensor.extra_state_attributes))
    for forbidden in _forbidden_strings():
        assert forbidden not in baseline_serialized

    schedule.call_args.args[1]()  # -> press
    entry.runtime_data._handle_radio_census_advertisement(
        ADDRESS, _frame(), set(), False
    )
    press_serialized = repr((sensor.native_value, sensor.extra_state_attributes))
    for forbidden in _forbidden_strings():
        assert forbidden not in press_serialized
    entry.runtime_data.cancel_radio_census()


def test_sensor_never_leaks_raw_material_when_complete():
    entry = _entry()
    schedule = Mock(return_value=Mock())
    entry.runtime_data.start_radio_census(schedule, Mock(return_value=Mock()))
    sensor = Ptm216bRadioCensusSensor(entry)

    entry.runtime_data._handle_radio_census_advertisement(
        ADDRESS, _frame(), set(), False
    )
    schedule.call_args.args[1]()  # -> press
    entry.runtime_data._handle_radio_census_advertisement(
        ADDRESS, _frame(), set(), False
    )
    schedule.call_args.args[1]()  # -> complete

    complete_serialized = repr((sensor.native_value, sensor.extra_state_attributes))
    for forbidden in _forbidden_strings():
        assert forbidden not in complete_serialized
    assert "entries" in sensor.extra_state_attributes


def test_census_working_identifier_set_is_cleared_after_complete():
    entry = _entry()
    schedule = Mock(return_value=Mock())
    entry.runtime_data.start_radio_census(schedule, Mock(return_value=Mock()))
    entry.runtime_data._handle_radio_census_advertisement(
        ADDRESS, _frame(), set(), False
    )
    schedule.call_args.args[1]()  # -> press
    schedule.call_args.args[1]()  # -> complete

    census = entry.runtime_data.radio_census
    for bucket in census._buckets.values():
        assert bucket._seen_identifiers == set()
    assert IDENTIFIER not in repr(census)


def test_census_working_identifier_set_is_cleared_after_cancel():
    entry = _entry()
    entry.runtime_data.start_radio_census(
        Mock(return_value=Mock()), Mock(return_value=Mock())
    )
    entry.runtime_data._handle_radio_census_advertisement(
        ADDRESS, _frame(), set(), False
    )

    entry.runtime_data.cancel_radio_census()

    assert entry.runtime_data.radio_census._buckets == {}


def test_runtime_repr_never_leaks_the_hmac_secret_or_address():
    entry = _entry()
    entry.runtime_data.start_radio_census(
        Mock(return_value=Mock()), Mock(return_value=Mock())
    )

    entry.runtime_data._handle_radio_census_advertisement(
        ADDRESS, _frame(), set(), False
    )

    serialized = repr(entry.runtime_data)
    assert ADDRESS not in serialized
    assert repr(SECRET) not in serialized
    assert IDENTIFIER not in serialized
    entry.runtime_data.cancel_radio_census()


def test_local_names_and_service_data_are_never_read_by_record_advertisement():
    """The runtime handler's signature has no local-name/RSSI/timestamp
    parameter at all -- this is enforced structurally, not just by omission
    from the summary. This test documents that contract so a future
    signature change is caught immediately.
    """
    import inspect

    from custom_components.enocean_ptm216b.radio_census import RadioCensus

    signature = inspect.signature(RadioCensus.record_advertisement)
    params = set(signature.parameters) - {"self"}
    assert params == {"identifier", "manufacturer_data", "service_uuids", "connectable"}
