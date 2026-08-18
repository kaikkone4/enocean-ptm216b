from unittest.mock import Mock

from custom_components.enocean_ptm216b.runtime_data import (
    DESIGNATION_CAPTURE_SECONDS,
    CaptureState,
    Ptm216bRuntimeData,
)

SECRET = b"\x01" * 32


def test_designation_capture_is_inert_by_default():
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)

    assert runtime.capture_state is CaptureState.INERT
    assert runtime.designation_candidates == set()
    assert runtime.capture_timer is None


def test_designation_capture_starts_only_after_manual_request():
    cancel_timer = Mock()
    schedule = Mock(return_value=cancel_timer)
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)

    runtime.start_designation_capture(schedule)

    assert runtime.capture_state is CaptureState.CAPTURING
    schedule.assert_called_once()
    assert schedule.call_args.args[0] == DESIGNATION_CAPTURE_SECONDS
    assert runtime.capture_timer is cancel_timer

    schedule.call_args.args[1]()

    assert runtime.capture_state is CaptureState.INERT
    assert runtime.designation_candidates == set()
    assert runtime.capture_timer is None


def test_restarting_designation_capture_cancels_prior_timer():
    first_cancel = Mock()
    second_cancel = Mock()
    schedule = Mock(side_effect=[first_cancel, second_cancel])
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)

    runtime.start_designation_capture(schedule)
    runtime.start_designation_capture(schedule)

    first_cancel.assert_called_once_with()
    assert runtime.capture_timer is second_cancel


def test_cancellation_clears_timer_and_candidate_container():
    cancel_timer = Mock()
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    runtime.start_designation_capture(Mock(return_value=cancel_timer))
    runtime.designation_candidates.add("local-pseudonym")

    runtime.cancel_designation_capture()

    cancel_timer.assert_called_once_with()
    assert runtime.capture_state is CaptureState.INERT
    assert runtime.designation_candidates == set()
    assert runtime.capture_timer is None
