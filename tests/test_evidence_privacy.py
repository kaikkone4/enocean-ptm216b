"""Privacy assertions for evidence capture, in the style of test_privacy.py.

Verifies that serialized sensor attributes and the collector's own repr never
contain a BLE address, raw payload bytes/hex, an absolute counter value, an
absolute switch-status value, or a full 64-char identifier.
"""

from dataclasses import asdict

from custom_components.enocean_ptm216b.evidence_capture import (
    ENOCEAN_MANUFACTURER_ID,
    EvidenceCollector,
)
from custom_components.enocean_ptm216b.identity import device_identifier
from custom_components.enocean_ptm216b.runtime_data import Ptm216bRuntimeData
from custom_components.enocean_ptm216b.sensor import Ptm216bEvidenceCaptureSensor

SECRET = b"private-secret-marker" * 2
ADDRESS = "AA:BB:CC:DD:EE:FF"
IDENTIFIER = device_identifier(SECRET, ADDRESS)
SIGNATURE_STUB = b"\x00\x00\x00\x00"
# Distinctive absolute counter values that must never appear verbatim in any
# exposed representation; only their delta (4) may be exposed.
FIRST_COUNTER = 1_000_003
SECOND_COUNTER = 1_000_007
FIRST_STATUS = 0x37


def _value(counter: int, status: int) -> bytes:
    """Synthetic manufacturer-data value; never a real captured telegram."""
    return counter.to_bytes(4, "little") + bytes([status]) + SIGNATURE_STUB


def _forbidden_strings() -> list[str]:
    return [
        ADDRESS,
        IDENTIFIER,
        str(FIRST_COUNTER),
        str(SECOND_COUNTER),
        repr(_value(FIRST_COUNTER, FIRST_STATUS)),
        repr(_value(SECOND_COUNTER, FIRST_STATUS + 1)),
        _value(FIRST_COUNTER, FIRST_STATUS).hex(),
        _value(SECOND_COUNTER, FIRST_STATUS + 1).hex(),
    ]


def test_collector_repr_never_leaks_raw_material_while_collecting():
    collector = EvidenceCollector(IDENTIFIER)
    collector.start(lambda delay, finish: lambda: None)
    collector.record_callback(
        IDENTIFIER,
        {ENOCEAN_MANUFACTURER_ID: _value(FIRST_COUNTER, FIRST_STATUS)},
        False,
    )
    collector.record_callback(
        IDENTIFIER,
        {ENOCEAN_MANUFACTURER_ID: _value(SECOND_COUNTER, FIRST_STATUS + 1)},
        False,
    )

    serialized = repr(collector)
    for forbidden in _forbidden_strings():
        assert forbidden not in serialized


def test_completed_summary_never_leaks_raw_material():
    collector = EvidenceCollector(IDENTIFIER)
    schedule_calls = []
    collector.start(
        lambda delay, finish: schedule_calls.append(finish) or (lambda: None)
    )
    collector.record_callback(
        IDENTIFIER,
        {ENOCEAN_MANUFACTURER_ID: _value(FIRST_COUNTER, FIRST_STATUS)},
        False,
    )
    collector.record_callback(
        IDENTIFIER,
        {ENOCEAN_MANUFACTURER_ID: _value(SECOND_COUNTER, FIRST_STATUS + 1)},
        False,
    )
    schedule_calls[0]()

    summary = collector.summary
    assert summary.le_deltas == [SECOND_COUNTER - FIRST_COUNTER]
    serialized = repr(asdict(summary))
    for forbidden in _forbidden_strings():
        assert forbidden not in serialized
    assert IDENTIFIER not in repr(collector)


def test_evidence_sensor_never_leaks_raw_material_at_any_state():
    entry_runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    entry_runtime.designated_identifier = IDENTIFIER
    schedule_calls = []
    entry_runtime.start_evidence_capture(
        lambda delay, finish: schedule_calls.append(finish) or (lambda: None)
    )
    entry = type("Entry", (), {"entry_id": "entry-id", "runtime_data": entry_runtime})()
    sensor = Ptm216bEvidenceCaptureSensor(entry)

    entry_runtime.record_advertisement_observation(
        ADDRESS, {ENOCEAN_MANUFACTURER_ID: _value(FIRST_COUNTER, FIRST_STATUS)}, False
    )
    collecting_serialized = repr((sensor.native_value, sensor.extra_state_attributes))
    for forbidden in _forbidden_strings():
        assert forbidden not in collecting_serialized

    entry_runtime.record_advertisement_observation(
        ADDRESS,
        {ENOCEAN_MANUFACTURER_ID: _value(SECOND_COUNTER, FIRST_STATUS + 1)},
        False,
    )
    schedule_calls[0]()
    complete_serialized = repr((sensor.native_value, sensor.extra_state_attributes))
    for forbidden in _forbidden_strings():
        assert forbidden not in complete_serialized
    assert "le_deltas" in sensor.extra_state_attributes


def test_aborted_evidence_never_leaks_the_would_be_commissioning_payload():
    entry_runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    entry_runtime.designated_identifier = IDENTIFIER
    entry_runtime.start_evidence_capture(lambda delay, finish: lambda: None)
    entry = type("Entry", (), {"entry_id": "entry-id", "runtime_data": entry_runtime})()
    sensor = Ptm216bEvidenceCaptureSensor(entry)

    commissioning_like = bytes(30)
    entry_runtime.record_advertisement_observation(
        ADDRESS, {ENOCEAN_MANUFACTURER_ID: commissioning_like}, False
    )

    assert sensor.native_value == "aborted"
    assert sensor.extra_state_attributes == {}
    serialized = repr((sensor.native_value, sensor.extra_state_attributes))
    assert commissioning_like.hex() not in serialized
    assert repr(commissioning_like) not in serialized
