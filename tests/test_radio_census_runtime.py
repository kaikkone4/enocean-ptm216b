"""Runtime wiring tests for the radio census (Phase 7): its own unfiltered
Bluetooth callback is registered on start and unregistered on every exit
path, and it mutually cancels with designation/evidence capture only while
actually running.
"""

from unittest.mock import Mock

from custom_components.enocean_ptm216b.evidence_capture import EvidenceState
from custom_components.enocean_ptm216b.radio_census import RadioCensusState
from custom_components.enocean_ptm216b.runtime_data import (
    CaptureState,
    Ptm216bRuntimeData,
)

SECRET = b"\x01" * 32
ADDRESS = "AA:BB:CC:DD:EE:FF"
ENOCEAN_ID = 0x03DA


def _frame() -> dict:
    return {ENOCEAN_ID: b"\x01" * 9}


def _register(cancel: Mock | None = None) -> Mock:
    return Mock(return_value=cancel if cancel is not None else Mock())


def test_starting_radio_census_registers_the_unfiltered_callback():
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    schedule = Mock(return_value=Mock())
    register = _register()

    runtime.start_radio_census(schedule, register)

    assert runtime.radio_census is not None
    assert runtime.radio_census.state is RadioCensusState.BASELINE
    register.assert_called_once()
    assert register.call_args.args[0] == runtime._handle_radio_census_advertisement


def test_the_registered_handler_feeds_the_active_census():
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    schedule = Mock(return_value=Mock())
    register = _register()
    runtime.start_radio_census(schedule, register)
    handler = register.call_args.args[0]

    handler(ADDRESS, _frame(), set(), False)

    assert runtime.radio_census.current_phase_count == 1


def test_cancel_radio_census_unregisters_the_callback_and_returns_to_inert():
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    cancel = Mock()
    runtime.start_radio_census(Mock(return_value=Mock()), _register(cancel))

    runtime.cancel_radio_census()

    cancel.assert_called_once_with()
    assert runtime.radio_census.state is RadioCensusState.INERT


def test_cancel_radio_census_is_a_no_op_when_nothing_is_running():
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)

    runtime.cancel_radio_census()

    assert runtime.radio_census is None


def test_the_unfiltered_callback_is_unregistered_when_the_baseline_timer_advances_to_press():
    """Registration must survive the baseline-to-press transition -- only
    the terminal complete/cancel transitions unregister it."""
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    cancel = Mock()
    schedule = Mock(return_value=Mock())
    runtime.start_radio_census(schedule, _register(cancel))

    finish_baseline = schedule.call_args.args[1]
    finish_baseline()

    assert runtime.radio_census.state is RadioCensusState.PRESS
    cancel.assert_not_called()


def test_the_unfiltered_callback_is_unregistered_when_the_press_timer_completes_the_window():
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    cancel = Mock()
    schedule = Mock(return_value=Mock())
    runtime.start_radio_census(schedule, _register(cancel))
    schedule.call_args.args[1]()  # -> press

    schedule.call_args.args[1]()  # -> complete

    assert runtime.radio_census.state is RadioCensusState.COMPLETE
    cancel.assert_called_once_with()


def test_starting_a_new_radio_census_replaces_and_unregisters_the_previous_one():
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    first_cancel = Mock()
    runtime.start_radio_census(Mock(return_value=Mock()), _register(first_cancel))

    second_cancel = Mock()
    runtime.start_radio_census(Mock(return_value=Mock()), _register(second_cancel))

    first_cancel.assert_called_once_with()
    second_cancel.assert_not_called()
    assert runtime.radio_census.state is RadioCensusState.BASELINE


def test_starting_designation_capture_cancels_a_running_radio_census():
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    cancel = Mock()
    runtime.start_radio_census(Mock(return_value=Mock()), _register(cancel))

    runtime.start_designation_capture(Mock(return_value=Mock()))

    cancel.assert_called_once_with()
    assert runtime.radio_census.state is RadioCensusState.INERT
    assert runtime.capture_state is CaptureState.BASELINE
    runtime.cancel_designation_capture()


def test_starting_designation_capture_leaves_a_completed_radio_census_intact():
    """A finished census is not 'running'; it must survive untouched --
    mirrors the same Phase 5A rule already covering evidence capture."""
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    cancel = Mock()
    schedule = Mock(return_value=Mock())
    runtime.start_radio_census(schedule, _register(cancel))
    schedule.call_args.args[1]()  # -> press
    schedule.call_args.args[1]()  # -> complete
    assert runtime.radio_census.state is RadioCensusState.COMPLETE
    summary_before = runtime.radio_census.summary

    runtime.start_designation_capture(Mock(return_value=Mock()))

    assert runtime.radio_census.state is RadioCensusState.COMPLETE
    assert runtime.radio_census.summary == summary_before
    assert runtime.capture_state is CaptureState.BASELINE
    runtime.cancel_designation_capture()


def test_starting_evidence_capture_cancels_a_running_radio_census():
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    runtime.designated_identifier = "a" * 64
    cancel = Mock()
    runtime.start_radio_census(Mock(return_value=Mock()), _register(cancel))

    started = runtime.start_evidence_capture(Mock(return_value=Mock()))

    assert started is True
    cancel.assert_called_once_with()
    assert runtime.radio_census.state is RadioCensusState.INERT
    runtime.cancel_evidence_capture()


def test_starting_evidence_capture_leaves_a_completed_radio_census_intact():
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    runtime.designated_identifier = "a" * 64
    schedule = Mock(return_value=Mock())
    runtime.start_radio_census(schedule, _register())
    schedule.call_args.args[1]()  # -> press
    schedule.call_args.args[1]()  # -> complete
    assert runtime.radio_census.state is RadioCensusState.COMPLETE

    runtime.start_evidence_capture(Mock(return_value=Mock()))

    assert runtime.radio_census.state is RadioCensusState.COMPLETE
    runtime.cancel_evidence_capture()


def test_starting_radio_census_cancels_a_running_designation_capture():
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    designation_cancel = Mock()
    runtime.start_designation_capture(Mock(return_value=designation_cancel))
    assert runtime.capture_state is CaptureState.BASELINE

    runtime.start_radio_census(Mock(return_value=Mock()), _register())

    designation_cancel.assert_called_once_with()
    assert runtime.capture_state is CaptureState.INERT
    runtime.cancel_radio_census()


def test_starting_radio_census_cancels_a_collecting_evidence_capture():
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    runtime.designated_identifier = "a" * 64
    evidence_cancel = Mock()
    runtime.start_evidence_capture(Mock(return_value=evidence_cancel))
    assert runtime.evidence_collector.state is EvidenceState.COLLECTING

    runtime.start_radio_census(Mock(return_value=Mock()), _register())

    evidence_cancel.assert_called_once_with()
    assert runtime.evidence_collector.state is EvidenceState.INERT
    runtime.cancel_radio_census()


def test_starting_radio_census_does_not_require_a_designated_device():
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)

    runtime.start_radio_census(Mock(return_value=Mock()), _register())

    assert runtime.radio_census.state is RadioCensusState.BASELINE
    runtime.cancel_radio_census()


def test_radio_census_state_listener_is_notified_on_accepted_callbacks():
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    listener = Mock()
    runtime.radio_census_state_listener = listener
    runtime.start_radio_census(Mock(return_value=Mock()), _register())
    listener.reset_mock()

    runtime._handle_radio_census_advertisement(ADDRESS, _frame(), set(), False)

    listener.assert_called_once_with()
    runtime.cancel_radio_census()


def test_advertisement_with_an_invalid_address_is_ignored():
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    runtime.start_radio_census(Mock(return_value=Mock()), _register())

    runtime._handle_radio_census_advertisement("not-an-address", _frame(), set(), False)

    assert runtime.radio_census.current_phase_count == 0
    runtime.cancel_radio_census()
