from unittest.mock import Mock

from custom_components.enocean_ptm216b.runtime_data import (
    DESIGNATION_CAPTURE_SECONDS,
    MINIMUM_DESIGNATION_OBSERVATIONS,
    CaptureState,
    DesignationOutcome,
    Ptm216bRuntimeData,
)

SECRET = b"\x01" * 32


def test_designation_capture_is_inert_by_default():
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)

    assert runtime.capture_state is CaptureState.INERT
    assert runtime.designation_candidates == {}
    assert runtime.capture_timer is None
    assert runtime.capture_observation_count == 0
    assert runtime.designation_outcome is DesignationOutcome.NO_SELECTION


def test_designation_capture_starts_only_after_manual_request():
    cancel_timer = Mock()
    schedule = Mock(return_value=cancel_timer)
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)

    runtime.start_designation_capture(schedule)

    assert runtime.capture_state is CaptureState.CAPTURING
    assert runtime.capture_state.value == "active"
    schedule.assert_called_once()
    assert schedule.call_args.args[0] == DESIGNATION_CAPTURE_SECONDS
    assert runtime.capture_timer is cancel_timer

    schedule.call_args.args[1]()

    assert runtime.capture_state is CaptureState.INERT
    assert runtime.designation_candidates == {}
    assert runtime.capture_timer is None


def test_restarting_designation_capture_cancels_prior_timer_and_resets_result():
    first_cancel = Mock()
    second_cancel = Mock()
    schedule = Mock(side_effect=[first_cancel, second_cancel])
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)

    runtime.start_designation_capture(schedule)
    runtime.record_designation_candidate("AA:BB:CC:DD:EE:FF", 1.0)
    runtime.start_designation_capture(schedule)

    first_cancel.assert_called_once_with()
    assert runtime.capture_timer is second_cancel
    assert runtime.capture_observation_count == 0
    assert runtime.designated_identifier is None
    assert runtime.designation_outcome is DesignationOutcome.NO_SELECTION


def test_cancellation_clears_all_capture_state():
    cancel_timer = Mock()
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    runtime.start_designation_capture(Mock(return_value=cancel_timer))
    runtime.record_designation_candidate("AA:BB:CC:DD:EE:FF", 1.0)

    runtime.cancel_designation_capture()

    cancel_timer.assert_called_once_with()
    assert runtime.capture_state is CaptureState.INERT
    assert runtime.designation_candidates == {}
    assert runtime.capture_timer is None
    assert runtime.capture_observation_count == 0
    assert runtime.designated_identifier is None
    assert runtime.designation_outcome is DesignationOutcome.NO_SELECTION


def test_expiry_selects_only_candidate_after_three_passive_observations():
    schedule = Mock(return_value=Mock())
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    runtime.start_designation_capture(schedule)

    for observed_at in range(MINIMUM_DESIGNATION_OBSERVATIONS):
        runtime.record_designation_candidate("AA:BB:CC:DD:EE:FF", float(observed_at))
    expected_identifier = next(iter(runtime.designation_candidates))
    schedule.call_args.args[1]()

    assert runtime.capture_state is CaptureState.INERT
    assert runtime.designated_identifier == expected_identifier
    assert runtime.designation_outcome is DesignationOutcome.SELECTED
    assert runtime.capture_observation_count == MINIMUM_DESIGNATION_OBSERVATIONS
    assert runtime.designation_candidates == {}
    assert runtime.capture_timer is None


def test_expiry_fails_closed_when_no_candidate_is_observed():
    schedule = Mock(return_value=Mock())
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    runtime.start_designation_capture(schedule)

    schedule.call_args.args[1]()

    assert runtime.designated_identifier is None
    assert runtime.designation_outcome is DesignationOutcome.NO_SELECTION
    assert runtime.capture_observation_count == 0
    assert runtime.designation_candidates == {}


def test_expiry_fails_closed_when_only_candidate_has_too_few_observations():
    schedule = Mock(return_value=Mock())
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    runtime.start_designation_capture(schedule)

    for observed_at in range(MINIMUM_DESIGNATION_OBSERVATIONS - 1):
        runtime.record_designation_candidate("AA:BB:CC:DD:EE:FF", float(observed_at))
    schedule.call_args.args[1]()

    assert runtime.designated_identifier is None
    assert runtime.designation_outcome is DesignationOutcome.NO_SELECTION
    assert runtime.designation_candidates == {}


def test_expiry_fails_closed_when_multiple_candidates_are_observed():
    schedule = Mock(return_value=Mock())
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    runtime.start_designation_capture(schedule)

    for observed_at in range(MINIMUM_DESIGNATION_OBSERVATIONS):
        runtime.record_designation_candidate("AA:BB:CC:DD:EE:FF", float(observed_at))
    runtime.record_designation_candidate("11:22:33:44:55:66", 10.0)
    schedule.call_args.args[1]()

    assert runtime.designated_identifier is None
    assert runtime.designation_outcome is DesignationOutcome.NO_SELECTION
    assert runtime.capture_observation_count == MINIMUM_DESIGNATION_OBSERVATIONS + 1
    assert runtime.designation_candidates == {}
