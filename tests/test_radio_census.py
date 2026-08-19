"""Pure-logic tests for the bounded radio census (Phase 7).

All manufacturer-data byte strings and service UUIDs below are synthetic
test fixtures, never derived from a real captured advertisement.
"""

from unittest.mock import Mock

from custom_components.enocean_ptm216b.radio_census import (
    BASELINE_SECONDS,
    MAX_TRACKED_MANUFACTURER_IDS,
    MAX_TRACKED_SERVICE_UUIDS,
    NO_MANUFACTURER_DATA_KEY,
    PRESS_SECONDS,
    RadioCensus,
    RadioCensusState,
    bucket_key_label,
)

ENOCEAN_ID = 0x03DA
CASAMBI_ID = 0x03C3
SIG_BASE_UUID = "0000180a-0000-1000-8000-00805f9b34fb"
VENDOR_UUID = "12345678-1234-5678-1234-56789abcdef0"


def _start() -> tuple[RadioCensus, Mock, Mock]:
    cancel = Mock()
    schedule = Mock(return_value=cancel)
    census = RadioCensus()
    census.start(schedule)
    return census, schedule, cancel


def _advance_to_press(census: RadioCensus, schedule: Mock) -> None:
    schedule.call_args.args[1]()


def _advance_to_complete(census: RadioCensus, schedule: Mock) -> None:
    """Fire baseline then press timers to reach COMPLETE."""
    _advance_to_press(census, schedule)
    schedule.call_args.args[1]()


def test_census_is_inert_by_default():
    census = RadioCensus()

    assert census.state is RadioCensusState.INERT
    assert census.current_phase_count == 0
    assert census.summary is None


def test_start_arms_the_bounded_baseline_phase():
    census, schedule, _cancel = _start()

    assert census.state is RadioCensusState.BASELINE
    assert schedule.call_args.args[0] == BASELINE_SECONDS


def test_baseline_timer_advances_to_press_phase():
    census, schedule, _cancel = _start()

    schedule.call_args.args[1]()

    assert census.state is RadioCensusState.PRESS
    assert schedule.call_args.args[0] == PRESS_SECONDS


def test_press_timer_ends_the_window_as_complete():
    census, schedule, _cancel = _start()

    _advance_to_complete(census, schedule)

    assert census.state is RadioCensusState.COMPLETE
    assert census.summary is not None


def test_advertisements_are_ignored_before_start():
    census = RadioCensus()

    census.record_advertisement("id1", {ENOCEAN_ID: b"\x01" * 9}, set(), False)

    assert census.current_phase_count == 0


def test_advertisements_are_ignored_after_complete():
    census, schedule, _cancel = _start()
    _advance_to_complete(census, schedule)
    summary_before = census.summary

    census.record_advertisement("id1", {ENOCEAN_ID: b"\x01" * 9}, set(), False)

    assert census.summary == summary_before


def test_baseline_and_press_counts_are_tracked_independently_per_manufacturer():
    census, schedule, _cancel = _start()

    census.record_advertisement("id1", {ENOCEAN_ID: b"\x01" * 9}, set(), False)
    census.record_advertisement("id1", {ENOCEAN_ID: b"\x01" * 9}, set(), False)
    _advance_to_press(census, schedule)
    census.record_advertisement("id1", {ENOCEAN_ID: b"\x01" * 9}, set(), False)
    _advance_to_complete(census, schedule)

    entry = census.summary.entries[bucket_key_label(ENOCEAN_ID)]
    assert entry.baseline_count == 2
    assert entry.press_count == 1


def test_current_phase_count_reflects_only_the_active_phase():
    census, schedule, _cancel = _start()
    census.record_advertisement("id1", {ENOCEAN_ID: b"\x01" * 9}, set(), False)
    census.record_advertisement("id2", {CASAMBI_ID: b"\x02" * 5}, set(), False)
    assert census.current_phase_count == 2

    _advance_to_press(census, schedule)
    assert census.current_phase_count == 0
    census.record_advertisement("id1", {ENOCEAN_ID: b"\x01" * 9}, set(), False)
    assert census.current_phase_count == 1


def test_distinct_devices_counts_unique_identifiers_per_manufacturer():
    census, schedule, _cancel = _start()

    census.record_advertisement("id1", {ENOCEAN_ID: b"\x01" * 9}, set(), False)
    census.record_advertisement("id1", {ENOCEAN_ID: b"\x01" * 9}, set(), False)
    census.record_advertisement("id2", {ENOCEAN_ID: b"\x01" * 9}, set(), False)
    _advance_to_complete(census, schedule)

    entry = census.summary.entries[bucket_key_label(ENOCEAN_ID)]
    assert entry.distinct_devices == 2


def test_max_value_length_tracks_the_largest_value_seen():
    census, schedule, _cancel = _start()

    census.record_advertisement("id1", {ENOCEAN_ID: b"\x01" * 9}, set(), False)
    census.record_advertisement("id1", {ENOCEAN_ID: b"\x02" * 159}, set(), False)
    census.record_advertisement("id1", {ENOCEAN_ID: b"\x03" * 20}, set(), False)
    _advance_to_complete(census, schedule)

    entry = census.summary.entries[bucket_key_label(ENOCEAN_ID)]
    assert entry.max_value_length == 159


def test_connectable_seen_reflects_a_true_flag():
    census, schedule, _cancel = _start()

    census.record_advertisement("id1", {ENOCEAN_ID: b"\x01" * 9}, set(), False)
    census.record_advertisement("id1", {ENOCEAN_ID: b"\x01" * 9}, set(), True)
    _advance_to_complete(census, schedule)

    entry = census.summary.entries[bucket_key_label(ENOCEAN_ID)]
    assert entry.connectable_seen is True


def test_no_manufacturer_data_bucket_aggregates_separately():
    census, schedule, _cancel = _start()

    census.record_advertisement("id1", {}, set(), False)
    census.record_advertisement("id1", {ENOCEAN_ID: b"\x01" * 9}, set(), False)
    _advance_to_complete(census, schedule)

    no_mfr_entry = census.summary.entries[NO_MANUFACTURER_DATA_KEY]
    assert no_mfr_entry.baseline_count == 1
    enocean_entry = census.summary.entries[bucket_key_label(ENOCEAN_ID)]
    assert enocean_entry.baseline_count == 1


def test_no_manufacturer_data_bucket_collects_short_form_service_uuids():
    census, schedule, _cancel = _start()

    census.record_advertisement("id1", {}, {SIG_BASE_UUID}, False)
    _advance_to_complete(census, schedule)

    entry = census.summary.entries[NO_MANUFACTURER_DATA_KEY]
    assert entry.service_uuids == ["0x180a"]


def test_vendor_128_bit_service_uuids_are_skipped_not_retained():
    census, schedule, _cancel = _start()

    census.record_advertisement("id1", {}, {VENDOR_UUID}, False)
    _advance_to_complete(census, schedule)

    entry = census.summary.entries[NO_MANUFACTURER_DATA_KEY]
    assert entry.service_uuids == []


def test_a_single_advertisement_with_multiple_manufacturer_ids_updates_both_buckets():
    census, schedule, _cancel = _start()

    census.record_advertisement(
        "id1", {ENOCEAN_ID: b"\x01" * 9, CASAMBI_ID: b"\x02" * 20}, set(), False
    )
    _advance_to_complete(census, schedule)

    assert census.summary.entries[bucket_key_label(ENOCEAN_ID)].baseline_count == 1
    assert census.summary.entries[bucket_key_label(CASAMBI_ID)].baseline_count == 1


def test_manufacturer_id_cap_stops_new_keys_but_keeps_counting_existing_ones():
    census, schedule, _cancel = _start()

    for manufacturer_id in range(MAX_TRACKED_MANUFACTURER_IDS):
        census.record_advertisement("id1", {manufacturer_id: b"\x01"}, set(), False)
    # One more, brand-new manufacturer ID beyond the cap.
    census.record_advertisement(
        "id1", {MAX_TRACKED_MANUFACTURER_IDS: b"\x01"}, set(), False
    )
    # An existing tracked key must keep counting past the cap.
    census.record_advertisement("id1", {0: b"\x01"}, set(), False)
    _advance_to_complete(census, schedule)

    assert len(census.summary.entries) == MAX_TRACKED_MANUFACTURER_IDS
    assert bucket_key_label(MAX_TRACKED_MANUFACTURER_IDS) not in census.summary.entries
    assert census.summary.entries[bucket_key_label(0)].baseline_count == 2
    assert census.summary.truncated is True


def test_service_uuid_cap_stops_new_uuids_but_keeps_the_bucket():
    census, schedule, _cancel = _start()

    for index in range(MAX_TRACKED_SERVICE_UUIDS):
        uuid = f"0000{index:04x}-0000-1000-8000-00805f9b34fb"
        census.record_advertisement("id1", {}, {uuid}, False)
    overflow_uuid = f"0000{MAX_TRACKED_SERVICE_UUIDS:04x}-0000-1000-8000-00805f9b34fb"
    census.record_advertisement("id1", {}, {overflow_uuid}, False)
    _advance_to_complete(census, schedule)

    entry = census.summary.entries[NO_MANUFACTURER_DATA_KEY]
    assert len(entry.service_uuids) == MAX_TRACKED_SERVICE_UUIDS
    assert census.summary.truncated is True


def test_cancel_discards_everything_and_returns_to_inert():
    census, _schedule, cancel = _start()
    census.record_advertisement("id1", {ENOCEAN_ID: b"\x01" * 9}, set(), False)

    census.cancel()

    cancel.assert_called_once_with()
    assert census.state is RadioCensusState.INERT
    assert census.current_phase_count == 0
    assert census.summary is None


def test_starting_again_replaces_a_previous_window():
    census, schedule, cancel = _start()
    census.record_advertisement("id1", {ENOCEAN_ID: b"\x01" * 9}, set(), False)

    second_cancel = Mock()
    second_schedule = Mock(return_value=second_cancel)
    census.start(second_schedule)

    cancel.assert_called_once_with()
    assert census.state is RadioCensusState.BASELINE
    assert census.current_phase_count == 0


def test_state_listener_is_notified_on_start():
    listener = Mock()
    census = RadioCensus()
    census.state_listener = listener

    census.start(Mock(return_value=Mock()))

    listener.assert_called_once_with()


def test_state_listener_is_notified_on_baseline_to_press_transition():
    listener = Mock()
    census = RadioCensus()
    census.state_listener = listener
    schedule = Mock(return_value=Mock())
    census.start(schedule)
    listener.reset_mock()

    schedule.call_args.args[1]()

    listener.assert_called_once_with()
    assert census.state is RadioCensusState.PRESS


def test_state_listener_is_notified_on_the_timer_driven_terminal_complete_transition():
    """Regression-style guard, mirroring evidence_capture's own Phase 5A fix:
    the timer-driven terminal transition must notify, not just cancel()."""
    listener = Mock()
    census = RadioCensus()
    census.state_listener = listener
    schedule = Mock(return_value=Mock())
    census.start(schedule)
    schedule.call_args.args[1]()  # -> press
    listener.reset_mock()

    schedule.call_args.args[1]()  # -> complete

    listener.assert_called_once_with()
    assert census.state is RadioCensusState.COMPLETE


def test_state_listener_is_notified_on_cancel():
    listener = Mock()
    census = RadioCensus()
    census.state_listener = listener
    census.start(Mock(return_value=Mock()))
    listener.reset_mock()

    census.cancel()

    listener.assert_called_once_with()
    assert census.state is RadioCensusState.INERT
