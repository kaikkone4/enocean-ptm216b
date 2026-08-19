"""Exhaustive tests for press_timing.py's hold-time short/long press state
machine, using a fake scheduler/clock -- no real timers -- following this
repo's existing designation/evidence-capture test convention (``schedule =
Mock(return_value=cancel)``; ``schedule.call_args.args[1]()`` fires the
timer).
"""

from __future__ import annotations

from unittest.mock import Mock

from custom_components.enocean_ptm216b.press_timing import (
    DEFAULT_LONG_PRESS_THRESHOLD_MS,
    MAX_LONG_PRESS_THRESHOLD_MS,
    MIN_LONG_PRESS_THRESHOLD_MS,
    PressAction,
    PressTimingTracker,
)
from custom_components.enocean_ptm216b.telegram import Button, Ptm216bButtonState


def _press(button: Button = Button.A0) -> Ptm216bButtonState:
    return Ptm216bButtonState(button=button, is_press=True)


def _release(button: Button = Button.A0) -> Ptm216bButtonState:
    return Ptm216bButtonState(button=button, is_press=False)


def _tracker(
    threshold_ms: int = DEFAULT_LONG_PRESS_THRESHOLD_MS,
) -> tuple[PressTimingTracker, Mock, list[PressAction]]:
    cancel = Mock()
    schedule = Mock(return_value=cancel)
    tracker = PressTimingTracker(threshold_ms=threshold_ms, scheduler=schedule)
    actions: list[PressAction] = []
    tracker.set_listener(Button.A0, actions.append)
    return tracker, schedule, actions


def test_default_threshold_is_500ms():
    assert DEFAULT_LONG_PRESS_THRESHOLD_MS == 500


def test_bounds_are_sane():
    assert (
        MIN_LONG_PRESS_THRESHOLD_MS
        < DEFAULT_LONG_PRESS_THRESHOLD_MS
        < MAX_LONG_PRESS_THRESHOLD_MS
    )


# ---------------------------------------------------------------------------
# Short path: press, quick release
# ---------------------------------------------------------------------------


def test_press_emits_raw_press_immediately_and_starts_a_hold_timer():
    tracker, schedule, actions = _tracker()

    tracker.handle_button_state(_press())

    assert actions == [PressAction.PRESS]
    schedule.assert_called_once()
    assert schedule.call_args.args[0] == DEFAULT_LONG_PRESS_THRESHOLD_MS / 1000.0


def test_release_before_timer_emits_short_press_then_release_and_cancels_timer():
    tracker, schedule, actions = _tracker()
    cancel = schedule.return_value

    tracker.handle_button_state(_press())
    tracker.handle_button_state(_release())

    assert actions == [
        PressAction.PRESS,
        PressAction.SHORT_PRESS,
        PressAction.RELEASE,
    ]
    cancel.assert_called_once()


# ---------------------------------------------------------------------------
# Long path: hold-time firing, while still held
# ---------------------------------------------------------------------------


def test_timer_fires_long_press_while_still_held():
    tracker, schedule, actions = _tracker()

    tracker.handle_button_state(_press())
    schedule.call_args.args[1]()  # the hold timer elapses

    assert actions == [PressAction.PRESS, PressAction.LONG_PRESS]


def test_release_after_long_press_emits_only_release_no_short_press():
    tracker, schedule, actions = _tracker()

    tracker.handle_button_state(_press())
    schedule.call_args.args[1]()
    actions.clear()

    tracker.handle_button_state(_release())

    assert actions == [PressAction.RELEASE]


# ---------------------------------------------------------------------------
# Radio-loss safety: orphan reset on a new press
# ---------------------------------------------------------------------------


def test_new_press_while_previous_press_still_open_resets_silently():
    """A lost release must never produce a spurious short_press or a
    duplicate long_press for the orphaned press.
    """
    tracker, schedule, actions = _tracker()
    cancel = schedule.return_value

    tracker.handle_button_state(_press())  # first press; its release is lost
    tracker.handle_button_state(_press())  # a second press arrives instead

    assert actions == [PressAction.PRESS, PressAction.PRESS]
    cancel.assert_called_once()  # first timer cancelled, never fired


def test_new_press_after_previous_long_press_already_fired_resets_cleanly():
    tracker, schedule, actions = _tracker()

    tracker.handle_button_state(_press())
    schedule.call_args.args[1]()  # long_press fires; its release is then lost
    actions.clear()

    tracker.handle_button_state(_press())  # new press for the same button

    assert actions == [PressAction.PRESS]  # no retroactive or duplicate action


def test_stale_timer_callback_cannot_fire_long_press_for_a_newer_press():
    """Defense in depth: even if a scheduler's cancel is not perfectly
    reliable and the old callback fires anyway after an orphan reset, it
    must never be misattributed to the newer, still-timing press.
    """
    tracker, schedule, actions = _tracker()

    tracker.handle_button_state(_press())
    stale_callback = schedule.call_args.args[1]
    tracker.handle_button_state(_press())  # orphan reset; new timer started
    actions.clear()

    stale_callback()  # the cancelled first timer fires anyway

    assert actions == []


def test_orphan_reset_then_real_long_press_of_the_new_press_still_works():
    tracker, schedule, actions = _tracker()

    tracker.handle_button_state(_press())
    tracker.handle_button_state(_press())  # orphan reset
    actions.clear()

    schedule.call_args.args[1]()  # the *new* press's own timer elapses

    assert actions == [PressAction.LONG_PRESS]


# ---------------------------------------------------------------------------
# Release without any open press
# ---------------------------------------------------------------------------


def test_release_with_no_open_press_emits_only_release():
    tracker, schedule, actions = _tracker()

    tracker.handle_button_state(_release())

    assert actions == [PressAction.RELEASE]
    schedule.assert_not_called()


# ---------------------------------------------------------------------------
# Threshold boundary / configurability
# ---------------------------------------------------------------------------


def test_custom_threshold_is_converted_to_seconds_for_the_scheduler():
    tracker, schedule, _actions = _tracker(threshold_ms=1234)

    tracker.handle_button_state(_press())

    assert schedule.call_args.args[0] == 1234 / 1000.0


def test_min_and_max_threshold_bounds_are_both_usable():
    for threshold_ms in (MIN_LONG_PRESS_THRESHOLD_MS, MAX_LONG_PRESS_THRESHOLD_MS):
        tracker, schedule, actions = _tracker(threshold_ms=threshold_ms)
        tracker.handle_button_state(_press())
        assert schedule.call_args.args[0] == threshold_ms / 1000.0
        schedule.call_args.args[1]()
        assert actions == [PressAction.PRESS, PressAction.LONG_PRESS]


# ---------------------------------------------------------------------------
# Per-button independence
# ---------------------------------------------------------------------------


def test_buttons_are_tracked_independently():
    schedule = Mock(return_value=Mock())
    tracker = PressTimingTracker(scheduler=schedule)
    a0_actions: list[PressAction] = []
    a1_actions: list[PressAction] = []
    tracker.set_listener(Button.A0, a0_actions.append)
    tracker.set_listener(Button.A1, a1_actions.append)

    tracker.handle_button_state(_press(Button.A0))
    tracker.handle_button_state(_press(Button.A1))
    tracker.handle_button_state(_release(Button.A0))

    assert a0_actions == [
        PressAction.PRESS,
        PressAction.SHORT_PRESS,
        PressAction.RELEASE,
    ]
    assert a1_actions == [PressAction.PRESS]


# ---------------------------------------------------------------------------
# Listener management
# ---------------------------------------------------------------------------


def test_set_listener_none_clears_it_and_suppresses_future_emits():
    tracker, _schedule, actions = _tracker()
    tracker.set_listener(Button.A0, None)

    tracker.handle_button_state(_press())

    assert actions == []


def test_no_listener_registered_is_a_silent_no_op():
    schedule = Mock(return_value=Mock())
    tracker = PressTimingTracker(scheduler=schedule)

    tracker.handle_button_state(_press(Button.A0))  # must not raise
    tracker.handle_button_state(_release(Button.A0))


# ---------------------------------------------------------------------------
# No scheduler configured (defensive default)
# ---------------------------------------------------------------------------


def test_no_scheduler_configured_still_emits_press_and_release_but_never_long_press():
    tracker = PressTimingTracker()  # scheduler defaults to None
    actions: list[PressAction] = []
    tracker.set_listener(Button.A0, actions.append)

    tracker.handle_button_state(_press())
    tracker.handle_button_state(_release())

    assert actions == [
        PressAction.PRESS,
        PressAction.SHORT_PRESS,
        PressAction.RELEASE,
    ]


# ---------------------------------------------------------------------------
# Unload cleanup
# ---------------------------------------------------------------------------


def test_clear_cancels_every_open_timer():
    cancel = Mock()
    schedule = Mock(return_value=cancel)
    tracker = PressTimingTracker(scheduler=schedule)
    tracker.set_listener(Button.A0, Mock())
    tracker.set_listener(Button.A1, Mock())

    tracker.handle_button_state(_press(Button.A0))
    tracker.handle_button_state(_press(Button.A1))

    tracker.clear()

    assert cancel.call_count == 2


def test_clear_leaves_the_tracker_ready_for_a_fresh_press():
    schedule = Mock(return_value=Mock())
    tracker = PressTimingTracker(scheduler=schedule)
    actions: list[PressAction] = []
    tracker.set_listener(Button.A0, actions.append)

    tracker.handle_button_state(_press())
    tracker.clear()
    tracker.handle_button_state(_press())
    tracker.handle_button_state(_release())

    assert actions == [
        PressAction.PRESS,
        PressAction.PRESS,
        PressAction.SHORT_PRESS,
        PressAction.RELEASE,
    ]


def test_clear_with_no_open_presses_is_a_no_op():
    tracker = PressTimingTracker(scheduler=Mock())
    tracker.clear()  # must not raise
