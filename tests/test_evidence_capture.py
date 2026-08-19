"""Pure-logic tests for the bounded telegram-structure evidence collector.

All manufacturer-data byte strings below are synthetic test fixtures: they
are hand-constructed placeholder values (zero-padded counters, arbitrary
status bytes, an all-zero signature stub) and are never derived from a real
captured EnOcean telegram.
"""

from unittest.mock import Mock

from custom_components.enocean_ptm216b.evidence_capture import (
    EVIDENCE_CAPTURE_SECONDS,
    MAX_EVIDENCE_RECORDS,
    ENOCEAN_MANUFACTURER_ID,
    EvidenceCollector,
    EvidenceState,
)

IDENTIFIER = "a" * 64
OTHER_IDENTIFIER = "b" * 64
SIGNATURE_STUB = b"\x00\x00\x00\x00"


def _frame(counter: bytes, status: int, prefix: bool = False) -> bytes:
    """Build a synthetic manufacturer-data value; never real captured bytes."""
    body = counter + bytes([status]) + SIGNATURE_STUB
    if prefix:
        return b"\x0d\xff\xda\x03" + body
    return body


def _data(value: bytes, extra_keys: dict[int, bytes] | None = None) -> dict[int, bytes]:
    payload = dict(extra_keys or {})
    payload[ENOCEAN_MANUFACTURER_ID] = value
    return payload


def _start(identifier: str = IDENTIFIER) -> tuple[EvidenceCollector, Mock, Mock]:
    cancel = Mock()
    schedule = Mock(return_value=cancel)
    collector = EvidenceCollector(identifier)
    collector.start(schedule)
    return collector, schedule, cancel


def test_collector_is_inert_by_default():
    collector = EvidenceCollector(IDENTIFIER)

    assert collector.state is EvidenceState.INERT
    assert collector.callbacks_accepted == 0
    assert collector.summary is None


def test_start_arms_the_single_bounded_ninety_second_window():
    collector, schedule, _cancel = _start()

    assert collector.state is EvidenceState.COLLECTING
    assert schedule.call_args.args[0] == EVIDENCE_CAPTURE_SECONDS


def test_callbacks_are_ignored_before_start():
    collector = EvidenceCollector(IDENTIFIER)

    collector.record_callback(
        IDENTIFIER, _data(_frame(b"\x01\x00\x00\x00", 0xAA)), False
    )

    assert collector.callbacks_accepted == 0


def test_callbacks_with_a_different_identifier_are_ignored():
    collector, _schedule, _cancel = _start()

    collector.record_callback(
        OTHER_IDENTIFIER, _data(_frame(b"\x01\x00\x00\x00", 0xAA)), False
    )

    assert collector.callbacks_accepted == 0


def test_manufacturer_data_keys_are_recorded_even_when_the_target_key_is_absent():
    collector, schedule, _cancel = _start()

    collector.record_callback(IDENTIFIER, {0x1234: b"\x00"}, False)
    schedule.call_args.args[1]()

    assert collector.state is EvidenceState.NO_DATA
    summary = collector.summary
    assert summary.callbacks_accepted == 0
    assert summary.manufacturer_data_keys == [0x1234]


def test_first_callback_has_no_deltas_and_a_zero_status_xor():
    collector, _schedule, _cancel = _start()

    collector.record_callback(
        IDENTIFIER, _data(_frame(b"\x01\x00\x00\x00", 0xAA)), False
    )

    assert collector.callbacks_accepted == 1


def test_le_and_be_deltas_and_status_xor_across_two_callbacks():
    collector, schedule, _cancel = _start()

    collector.record_callback(
        IDENTIFIER, _data(_frame(b"\x01\x00\x00\x00", 0xAA)), False
    )
    collector.record_callback(
        IDENTIFIER, _data(_frame(b"\x02\x00\x00\x00", 0xAB)), False
    )
    schedule.call_args.args[1]()

    summary = collector.summary
    assert summary.callbacks_accepted == 2
    # LE(b"\x02\x00\x00\x00") - LE(b"\x01\x00\x00\x00") == 1
    assert summary.le_deltas == [1]
    # BE(b"\x02\x00\x00\x00") - BE(b"\x01\x00\x00\x00") == 0x01000000
    assert summary.be_deltas == [0x01000000]
    assert summary.status_xor_values == [0, 0xAA ^ 0xAB]
    assert summary.counter_monotonic_le is True
    assert summary.counter_monotonic_be is True


def test_prefix_detection_is_recorded_and_offsets_the_counter_and_status():
    collector, schedule, _cancel = _start()

    collector.record_callback(
        IDENTIFIER, _data(_frame(b"\x05\x00\x00\x00", 0x10, prefix=True)), False
    )
    schedule.call_args.args[1]()

    summary = collector.summary
    assert summary.prefix_detected_consistent is True
    assert summary.status_xor_values == [0]


def test_prefix_detection_mixed_across_callbacks_is_reported_as_mixed():
    collector, schedule, _cancel = _start()

    collector.record_callback(
        IDENTIFIER, _data(_frame(b"\x01\x00\x00\x00", 0x10, prefix=True)), False
    )
    collector.record_callback(
        IDENTIFIER, _data(_frame(b"\x02\x00\x00\x00", 0x10, prefix=False)), False
    )
    schedule.call_args.args[1]()

    assert collector.summary.prefix_detected_consistent == "mixed"


def test_duplicate_identical_value_is_flagged_and_counted():
    collector, schedule, _cancel = _start()
    value = _frame(b"\x07\x00\x00\x00", 0x10)

    collector.record_callback(IDENTIFIER, _data(value), False)
    collector.record_callback(IDENTIFIER, _data(value), False)
    collector.record_callback(
        IDENTIFIER, _data(_frame(b"\x08\x00\x00\x00", 0x10)), False
    )
    schedule.call_args.args[1]()

    assert collector.summary.duplicate_identical_count == 1


def test_any_connectable_seen_reflects_a_true_flag():
    collector, schedule, _cancel = _start()

    collector.record_callback(
        IDENTIFIER, _data(_frame(b"\x01\x00\x00\x00", 0x10)), True
    )
    schedule.call_args.args[1]()

    assert collector.summary.any_connectable_seen is True


def test_reaching_the_cap_stops_accepting_and_ends_the_window_early_as_complete():
    collector, schedule, cancel = _start()

    for counter in range(MAX_EVIDENCE_RECORDS):
        collector.record_callback(
            IDENTIFIER,
            _data(_frame(counter.to_bytes(4, "little"), 0x10)),
            False,
        )

    assert collector.state is EvidenceState.COMPLETE
    assert collector.callbacks_accepted == MAX_EVIDENCE_RECORDS
    cancel.assert_called_once_with()

    collector.record_callback(
        IDENTIFIER,
        _data(_frame((999).to_bytes(4, "little"), 0x10)),
        False,
    )
    assert collector.callbacks_accepted == MAX_EVIDENCE_RECORDS


def test_timer_firing_with_zero_records_ends_as_no_data():
    collector, schedule, _cancel = _start()

    schedule.call_args.args[1]()

    assert collector.state is EvidenceState.NO_DATA
    summary = collector.summary
    assert summary.callbacks_accepted == 0
    assert summary.le_deltas == []
    assert summary.counter_monotonic_le is True
    assert summary.counter_monotonic_be is True


def test_value_length_of_24_or_more_hard_aborts_and_discards_everything():
    collector, schedule, cancel = _start()
    collector.record_callback(
        IDENTIFIER, _data(_frame(b"\x01\x00\x00\x00", 0x10)), False
    )

    oversized = bytes(24)
    collector.record_callback(IDENTIFIER, _data(oversized), True)

    assert collector.state is EvidenceState.ABORTED
    assert collector.callbacks_accepted == 0
    assert collector.summary is None
    cancel.assert_called_once_with()
    assert collector._manufacturer_data_keys == set()
    assert collector._any_connectable_seen is False
    assert collector._previous_value is None


def test_value_length_under_nine_hard_aborts_and_discards_everything():
    collector, _schedule, cancel = _start()

    collector.record_callback(IDENTIFIER, _data(bytes(8)), False)

    assert collector.state is EvidenceState.ABORTED
    assert collector.callbacks_accepted == 0
    assert collector.summary is None
    cancel.assert_called_once_with()


def test_aborted_collector_ignores_further_callbacks():
    collector, _schedule, _cancel = _start()
    collector.record_callback(IDENTIFIER, _data(bytes(8)), False)
    assert collector.state is EvidenceState.ABORTED

    collector.record_callback(
        IDENTIFIER, _data(_frame(b"\x01\x00\x00\x00", 0x10)), False
    )

    assert collector.callbacks_accepted == 0
    assert collector.state is EvidenceState.ABORTED


def test_cancel_discards_everything_and_returns_to_inert():
    collector, _schedule, cancel = _start()
    collector.record_callback(
        IDENTIFIER, _data(_frame(b"\x01\x00\x00\x00", 0x10)), False
    )

    collector.cancel()

    cancel.assert_called_once_with()
    assert collector.state is EvidenceState.INERT
    assert collector.callbacks_accepted == 0
    assert collector.summary is None


def test_starting_again_replaces_a_previous_window():
    collector, schedule, cancel = _start()
    collector.record_callback(
        IDENTIFIER, _data(_frame(b"\x01\x00\x00\x00", 0x10)), False
    )

    second_cancel = Mock()
    second_schedule = Mock(return_value=second_cancel)
    collector.start(second_schedule)

    cancel.assert_called_once_with()
    assert collector.state is EvidenceState.COLLECTING
    assert collector.callbacks_accepted == 0
