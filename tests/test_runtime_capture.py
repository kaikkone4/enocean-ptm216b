from unittest.mock import Mock

import pytest

from custom_components.enocean_ptm216b.runtime_data import (
    DESIGNATION_BASELINE_SECONDS,
    DESIGNATION_CAPTURE_SECONDS,
    MINIMUM_DESIGNATION_OBSERVATIONS,
    CaptureState,
    DesignationOutcome,
    Ptm216bRuntimeData,
)

SECRET = b"\x01" * 32
ADDRESS = "AA:BB:CC:DD:EE:FF"
OTHER_ADDRESS = "11:22:33:44:55:66"


def _observe(runtime, address=ADDRESS, count=MINIMUM_DESIGNATION_OBSERVATIONS):
    for observed_at in range(count):
        runtime.record_designation_candidate(address, float(observed_at))


def _start_press_window(runtime, schedule):
    runtime.start_designation_capture(schedule)
    schedule.call_args_list[0].args[1]()
    assert runtime.capture_state is CaptureState.PRESS


def _start_confirmation_window(runtime, schedule, address=ADDRESS):
    _start_press_window(runtime, schedule)
    _observe(runtime, address)
    schedule.call_args_list[1].args[1]()
    assert runtime.capture_state is CaptureState.CONFIRMING


def _assert_ephemeral_state_cleared(runtime):
    assert runtime.capture_state is CaptureState.INERT
    assert runtime.designation_candidates == {}
    assert runtime.first_window_identifier is None
    assert runtime.capture_timer is None
    assert runtime.capture_scheduler is None


def test_designation_capture_is_inert_by_default():
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)

    assert runtime.capture_state is CaptureState.INERT
    assert runtime.designation_candidates == {}
    assert runtime.capture_timer is None
    assert runtime.capture_observation_count == 0
    assert runtime.designation_outcome is DesignationOutcome.NO_SELECTION


def test_manual_request_starts_bounded_quiet_baseline_before_press_window():
    schedule = Mock(return_value=Mock())
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)

    runtime.start_designation_capture(schedule)

    assert runtime.capture_state is CaptureState.BASELINE
    assert schedule.call_args.args[0] == DESIGNATION_BASELINE_SECONDS
    schedule.call_args.args[1]()
    assert runtime.capture_state is CaptureState.PRESS
    assert schedule.call_args.args[0] == DESIGNATION_CAPTURE_SECONDS


def test_baseline_activity_fails_closed_and_clears_map():
    schedule = Mock(return_value=Mock())
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    runtime.start_designation_capture(schedule)
    _observe(runtime, count=1)

    schedule.call_args_list[0].args[1]()

    _assert_ephemeral_state_cleared(runtime)
    assert runtime.designated_identifier is None
    assert runtime.designation_outcome is DesignationOutcome.NO_SELECTION


def test_single_press_window_never_designates_a_candidate():
    schedule = Mock(side_effect=[Mock(), Mock(), Mock()])
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    _start_press_window(runtime, schedule)
    _observe(runtime)

    schedule.call_args_list[1].args[1]()

    assert runtime.capture_state is CaptureState.CONFIRMING
    assert runtime.designated_identifier is None
    assert runtime.designation_candidates == {}
    assert schedule.call_args_list[2].args[0] == DESIGNATION_CAPTURE_SECONDS


def test_same_unique_candidate_must_match_press_and_confirmation_windows():
    schedule = Mock(side_effect=[Mock(), Mock(), Mock()])
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    _start_confirmation_window(runtime, schedule)
    first_window_identifier = runtime.first_window_identifier

    _observe(runtime)
    schedule.call_args_list[2].args[1]()

    _assert_ephemeral_state_cleared(runtime)
    assert runtime.designated_identifier == first_window_identifier
    assert runtime.designation_outcome is DesignationOutcome.SELECTED


@pytest.mark.parametrize("count", [0, MINIMUM_DESIGNATION_OBSERVATIONS - 1])
def test_press_window_requires_one_candidate_with_minimum_observations(count):
    schedule = Mock(side_effect=[Mock(), Mock()])
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    _start_press_window(runtime, schedule)
    _observe(runtime, count=count)

    schedule.call_args_list[1].args[1]()

    _assert_ephemeral_state_cleared(runtime)
    assert runtime.designated_identifier is None


def test_press_window_ambiguity_fails_closed():
    schedule = Mock(side_effect=[Mock(), Mock()])
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    _start_press_window(runtime, schedule)
    _observe(runtime)
    _observe(runtime, OTHER_ADDRESS, 1)

    schedule.call_args_list[1].args[1]()

    _assert_ephemeral_state_cleared(runtime)
    assert runtime.designated_identifier is None


@pytest.mark.parametrize("count", [0, MINIMUM_DESIGNATION_OBSERVATIONS - 1])
def test_confirmation_requires_minimum_observations(count):
    schedule = Mock(side_effect=[Mock(), Mock(), Mock()])
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    _start_confirmation_window(runtime, schedule)
    _observe(runtime, count=count)

    schedule.call_args_list[2].args[1]()

    _assert_ephemeral_state_cleared(runtime)
    assert runtime.designated_identifier is None


def test_confirmation_address_rotation_fails_closed():
    schedule = Mock(side_effect=[Mock(), Mock(), Mock()])
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    _start_confirmation_window(runtime, schedule)
    _observe(runtime, OTHER_ADDRESS)

    schedule.call_args_list[2].args[1]()

    _assert_ephemeral_state_cleared(runtime)
    assert runtime.designated_identifier is None


def test_confirmation_ambiguity_fails_closed():
    schedule = Mock(side_effect=[Mock(), Mock(), Mock()])
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    _start_confirmation_window(runtime, schedule)
    _observe(runtime)
    _observe(runtime, OTHER_ADDRESS, 1)

    schedule.call_args_list[2].args[1]()

    _assert_ephemeral_state_cleared(runtime)
    assert runtime.designated_identifier is None


def test_restart_cancels_timer_and_clears_every_ephemeral_field():
    cancel_baseline = Mock()
    cancel_confirmation = Mock()
    schedule = Mock(side_effect=[cancel_baseline, Mock(), cancel_confirmation, Mock()])
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    _start_confirmation_window(runtime, schedule)
    _observe(runtime, count=1)

    runtime.start_designation_capture(schedule)

    cancel_confirmation.assert_called_once_with()
    assert runtime.capture_state is CaptureState.BASELINE
    assert runtime.capture_observation_count == 0
    assert runtime.designated_identifier is None
    assert runtime.first_window_identifier is None
    assert runtime.designation_candidates == {}


def test_cancel_during_confirmation_clears_maps_identifiers_and_timer():
    cancel_confirmation = Mock()
    schedule = Mock(side_effect=[Mock(), Mock(), cancel_confirmation])
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    _start_confirmation_window(runtime, schedule)
    _observe(runtime, count=1)

    runtime.cancel_designation_capture()

    cancel_confirmation.assert_called_once_with()
    _assert_ephemeral_state_cleared(runtime)
    assert runtime.capture_observation_count == 0
    assert runtime.designated_identifier is None
    assert runtime.designation_outcome is DesignationOutcome.NO_SELECTION
