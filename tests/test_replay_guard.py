"""Tests for the pure, unwired replay_guard.py outcome matrix.

All device identifiers and counters here are synthetic test data.
"""

from __future__ import annotations

from unittest.mock import Mock

from custom_components.enocean_ptm216b.replay_guard import (
    ReplayOutcome,
    evaluate_sequence_counter,
)

DEVICE = "test-device-identifier"


def _getter(persisted: int | None) -> Mock:
    return Mock(return_value=persisted)


def test_no_persisted_state_is_rejected_and_setter_never_called():
    getter = _getter(None)
    setter = Mock()

    outcome = evaluate_sequence_counter(DEVICE, 1, getter, setter)

    assert outcome is ReplayOutcome.NO_PERSISTED_STATE_REJECTED
    setter.assert_not_called()


def test_higher_counter_is_accepted_and_setter_called_before_return():
    """Prove the setter (persist) happens BEFORE acceptance is observable.

    The setter's side effect appends to `call_order` first; this test then
    appends "outcome_observed" only after `evaluate_sequence_counter` has
    already returned. If the implementation ever returned ACCEPTED without
    calling the setter first, `call_order` would not contain
    "setter_called" as its first entry.
    """
    call_order: list[str] = []
    getter = Mock(return_value=5)
    setter = Mock(side_effect=lambda *_args: call_order.append("setter_called"))

    outcome = evaluate_sequence_counter(DEVICE, 6, getter, setter)
    call_order.append("outcome_observed")

    assert outcome is ReplayOutcome.ACCEPTED
    setter.assert_called_once_with(DEVICE, 6)
    assert call_order == ["setter_called", "outcome_observed"]


def test_equal_counter_is_duplicate_and_setter_not_called():
    getter = _getter(5)
    setter = Mock()

    outcome = evaluate_sequence_counter(DEVICE, 5, getter, setter)

    assert outcome is ReplayOutcome.DUPLICATE
    setter.assert_not_called()


def test_lower_counter_is_replay_rejected_and_setter_not_called():
    getter = _getter(5)
    setter = Mock()

    outcome = evaluate_sequence_counter(DEVICE, 4, getter, setter)

    assert outcome is ReplayOutcome.REPLAY_REJECTED
    setter.assert_not_called()


def test_replay_reject_never_auto_resets_persisted_state():
    """After a replay reject, a subsequent getter call still returns the old
    value -- i.e. the setter was never invoked to advance/reset it. This is
    equivalent to (and reinforces) the "setter not called" assertions above,
    verified via a stateful fake store rather than a bare Mock.
    """
    store = {DEVICE: 5}

    def get(identifier: str) -> int | None:
        return store.get(identifier)

    def set_(identifier: str, counter: int) -> None:
        store[identifier] = counter

    outcome = evaluate_sequence_counter(DEVICE, 3, get, set_)

    assert outcome is ReplayOutcome.REPLAY_REJECTED
    assert store[DEVICE] == 5


def test_duplicate_never_auto_resets_persisted_state():
    store = {DEVICE: 5}

    def get(identifier: str) -> int | None:
        return store.get(identifier)

    def set_(identifier: str, counter: int) -> None:
        store[identifier] = counter

    outcome = evaluate_sequence_counter(DEVICE, 5, get, set_)

    assert outcome is ReplayOutcome.DUPLICATE
    assert store[DEVICE] == 5


def test_accepted_counter_persists_before_a_subsequent_call_observes_it():
    store: dict[str, int] = {DEVICE: 5}

    def get(identifier: str) -> int | None:
        return store.get(identifier)

    def set_(identifier: str, counter: int) -> None:
        store[identifier] = counter

    outcome = evaluate_sequence_counter(DEVICE, 6, get, set_)

    assert outcome is ReplayOutcome.ACCEPTED
    assert store[DEVICE] == 6
