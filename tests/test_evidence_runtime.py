"""Runtime wiring tests for evidence capture: designation stays untouched,
evidence requires a designated identifier, and either flow cancels the other.
"""

from unittest.mock import Mock

from custom_components.enocean_ptm216b.evidence_capture import (
    ENOCEAN_MANUFACTURER_ID,
    EvidenceState,
)
from custom_components.enocean_ptm216b.runtime_data import (
    CaptureState,
    Ptm216bRuntimeData,
)

SECRET = b"\x01" * 32
ADDRESS = "AA:BB:CC:DD:EE:FF"
SIGNATURE_STUB = b"\x00\x00\x00\x00"


def _frame(counter: bytes = b"\x01\x00\x00\x00", status: int = 0x10) -> dict:
    return {ENOCEAN_MANUFACTURER_ID: counter + bytes([status]) + SIGNATURE_STUB}


def test_evidence_capture_refuses_without_a_designated_device():
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    schedule = Mock(return_value=Mock())

    started = runtime.start_evidence_capture(schedule)

    assert started is False
    assert runtime.evidence_collector is None
    schedule.assert_not_called()


def test_evidence_capture_starts_once_a_device_is_designated():
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    runtime.designated_identifier = "a" * 64
    schedule = Mock(return_value=Mock())

    started = runtime.start_evidence_capture(schedule)

    assert started is True
    assert runtime.evidence_collector is not None
    assert runtime.evidence_collector.state is EvidenceState.COLLECTING


def test_starting_designation_capture_cancels_a_running_evidence_capture():
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    runtime.designated_identifier = "a" * 64
    evidence_cancel = Mock()
    runtime.start_evidence_capture(Mock(return_value=evidence_cancel))
    assert runtime.evidence_collector.state is EvidenceState.COLLECTING

    runtime.start_designation_capture(Mock(return_value=Mock()))

    evidence_cancel.assert_called_once_with()
    assert runtime.evidence_collector.state is EvidenceState.INERT
    assert runtime.capture_state is CaptureState.BASELINE
    runtime.cancel_designation_capture()


def test_starting_evidence_capture_cancels_a_running_designation_capture():
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    designation_cancel = Mock()
    runtime.start_designation_capture(Mock(return_value=designation_cancel))
    assert runtime.capture_state is CaptureState.BASELINE
    # Simulate a prior successful designation coexisting is impossible while a
    # capture runs, but exercise the defensive cross-cancel path directly.
    runtime.designated_identifier = "a" * 64

    started = runtime.start_evidence_capture(Mock(return_value=Mock()))

    assert started is True
    designation_cancel.assert_called_once_with()
    assert runtime.capture_state is CaptureState.INERT
    runtime.cancel_evidence_capture()


def test_designation_capture_behaviour_is_unchanged():
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    schedule = Mock(return_value=Mock())

    runtime.start_designation_capture(schedule)

    assert runtime.capture_state is CaptureState.BASELINE
    assert runtime.designated_identifier is None
    runtime.cancel_designation_capture()


def test_record_advertisement_observation_feeds_both_designation_and_evidence():
    from custom_components.enocean_ptm216b.identity import device_identifier

    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    runtime.designated_identifier = device_identifier(SECRET, ADDRESS)
    runtime.start_evidence_capture(Mock(return_value=Mock()))
    # Designation candidate recording is untouched: it still aggregates while
    # a designation-capture phase is active, and stays empty here since none is.
    runtime.record_advertisement_observation(ADDRESS, _frame(), False)

    assert runtime.evidence_collector.callbacks_accepted == 1
    assert runtime.designation_candidates == {}
    runtime.cancel_evidence_capture()


def test_record_advertisement_observation_ignores_evidence_for_other_devices():
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    runtime.designated_identifier = "a" * 64
    runtime.start_evidence_capture(Mock(return_value=Mock()))

    runtime.record_advertisement_observation("11:22:33:44:55:66", _frame(), False)

    assert runtime.evidence_collector.callbacks_accepted == 0
    runtime.cancel_evidence_capture()


def test_record_advertisement_observation_ignores_invalid_addresses():
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    runtime.designated_identifier = "a" * 64
    runtime.start_evidence_capture(Mock(return_value=Mock()))

    runtime.record_advertisement_observation("not-an-address", _frame(), False)

    assert runtime.evidence_collector.callbacks_accepted == 0
    runtime.cancel_evidence_capture()


def test_evidence_state_listener_is_notified_on_accepted_callbacks():
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    runtime.designated_identifier = "a" * 64
    listener = Mock()
    runtime.evidence_state_listener = listener
    runtime.start_evidence_capture(Mock(return_value=Mock()))
    listener.reset_mock()

    runtime.record_advertisement_observation(ADDRESS, _frame(), False)

    listener.assert_called_once_with()
    runtime.cancel_evidence_capture()


def test_cancel_evidence_capture_is_a_no_op_when_nothing_is_running():
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)

    runtime.cancel_evidence_capture()

    assert runtime.evidence_collector is None


def test_evidence_state_listener_is_notified_when_the_timer_ends_the_window():
    """Regression: the sensor must not go stale on the common timer-expiry path.

    Previously only record_callback's own state transitions (abort,
    cap-reached) notified the runtime listener; the timer-driven
    _finish_window transition (the common case: nothing pressed, or the
    window simply expires) never did, leaving the sensor stuck on
    "collecting" until an unrelated event redrew it.
    """
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    runtime.designated_identifier = "a" * 64
    listener = Mock()
    runtime.evidence_state_listener = listener
    schedule = Mock(return_value=Mock())
    runtime.start_evidence_capture(schedule)
    listener.reset_mock()
    finish_window = schedule.call_args.args[1]

    finish_window()

    listener.assert_called_once_with()
    assert runtime.evidence_collector.state is EvidenceState.NO_DATA


def test_starting_designation_capture_leaves_a_completed_evidence_summary_intact():
    """A finished evidence window is not 'running'; it must survive untouched."""
    from custom_components.enocean_ptm216b.identity import device_identifier

    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    identifier = device_identifier(SECRET, ADDRESS)
    runtime.designated_identifier = identifier
    schedule = Mock(return_value=Mock())
    runtime.start_evidence_capture(schedule)
    runtime.record_advertisement_observation(ADDRESS, _frame(), False)
    finish_window = schedule.call_args.args[1]
    finish_window()
    assert runtime.evidence_collector.state is EvidenceState.COMPLETE
    summary_before = runtime.evidence_collector.summary

    runtime.start_designation_capture(Mock(return_value=Mock()))

    assert runtime.evidence_collector.state is EvidenceState.COMPLETE
    assert runtime.evidence_collector.summary == summary_before
    assert runtime.capture_state is CaptureState.BASELINE
    runtime.cancel_designation_capture()
    runtime.cancel_evidence_capture()


def test_starting_designation_capture_leaves_a_no_data_evidence_result_intact():
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    runtime.designated_identifier = "a" * 64
    schedule = Mock(return_value=Mock())
    runtime.start_evidence_capture(schedule)
    schedule.call_args.args[1]()
    assert runtime.evidence_collector.state is EvidenceState.NO_DATA

    runtime.start_designation_capture(Mock(return_value=Mock()))

    assert runtime.evidence_collector.state is EvidenceState.NO_DATA
    runtime.cancel_designation_capture()
    runtime.cancel_evidence_capture()


def test_starting_designation_capture_leaves_an_aborted_evidence_result_intact():
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    runtime.designated_identifier = "a" * 64
    runtime.start_evidence_capture(Mock(return_value=Mock()))
    runtime.evidence_collector.record_callback("a" * 64, {0x03DA: bytes(8)}, False)
    assert runtime.evidence_collector.state is EvidenceState.ABORTED

    runtime.start_designation_capture(Mock(return_value=Mock()))

    assert runtime.evidence_collector.state is EvidenceState.ABORTED
    runtime.cancel_designation_capture()
    runtime.cancel_evidence_capture()
