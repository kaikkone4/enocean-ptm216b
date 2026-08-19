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
from custom_components.enocean_ptm216b.telegram import ButtonPattern, Ptm216bButtonState


def _press(pattern: ButtonPattern = ButtonPattern.A0) -> Ptm216bButtonState:
    return Ptm216bButtonState(pattern=pattern, is_press=True)


def _release(pattern: ButtonPattern = ButtonPattern.A0) -> Ptm216bButtonState:
    return Ptm216bButtonState(pattern=pattern, is_press=False)


def _tracker(
    threshold_ms: int = DEFAULT_LONG_PRESS_THRESHOLD_MS,
) -> tuple[PressTimingTracker, Mock, list[PressAction]]:
    cancel = Mock()
    schedule = Mock(return_value=cancel)
    tracker = PressTimingTracker(threshold_ms=threshold_ms, scheduler=schedule)
    actions: list[PressAction] = []
    tracker.set_listener(ButtonPattern.A0, actions.append)
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

    tracker.handle_button_state(_press())  # new press for the same pattern

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
# Per-pattern independence
# ---------------------------------------------------------------------------


def test_patterns_are_tracked_independently():
    schedule = Mock(return_value=Mock())
    tracker = PressTimingTracker(scheduler=schedule)
    a0_actions: list[PressAction] = []
    a1_actions: list[PressAction] = []
    tracker.set_listener(ButtonPattern.A0, a0_actions.append)
    tracker.set_listener(ButtonPattern.A1, a1_actions.append)

    tracker.handle_button_state(_press(ButtonPattern.A0))
    tracker.handle_button_state(_press(ButtonPattern.A1))
    tracker.handle_button_state(_release(ButtonPattern.A0))

    assert a0_actions == [
        PressAction.PRESS,
        PressAction.SHORT_PRESS,
        PressAction.RELEASE,
    ]
    assert a1_actions == [PressAction.PRESS]


# ---------------------------------------------------------------------------
# Combo patterns (Phase 5D): a combo pattern is just another dict key here --
# no special-case code anywhere in this module.
# ---------------------------------------------------------------------------


def test_combo_pattern_short_press():
    schedule = Mock(return_value=Mock())
    tracker = PressTimingTracker(scheduler=schedule)
    actions: list[PressAction] = []
    tracker.set_listener(ButtonPattern.A0_B0, actions.append)

    tracker.handle_button_state(_press(ButtonPattern.A0_B0))
    tracker.handle_button_state(_release(ButtonPattern.A0_B0))

    assert actions == [
        PressAction.PRESS,
        PressAction.SHORT_PRESS,
        PressAction.RELEASE,
    ]


def test_combo_pattern_long_press():
    schedule = Mock(return_value=Mock())
    tracker = PressTimingTracker(scheduler=schedule)
    actions: list[PressAction] = []
    tracker.set_listener(ButtonPattern.A1_B1, actions.append)

    tracker.handle_button_state(_press(ButtonPattern.A1_B1))
    schedule.call_args.args[1]()  # the hold timer elapses

    assert actions == [PressAction.PRESS, PressAction.LONG_PRESS]


def test_combo_press_then_partial_single_button_release_orphans_the_combo():
    """A press decoded as A0_B0 (both rockers actuated in one telegram)
    followed by a release that instead decodes to plain A0 (e.g. the
    user's finger lifted off one rocker microseconds before the other, so
    the release telegram only carries A0) must fire only a raw ``release``
    for A0 -- no ``short_press`` for A0, and A0_B0's own open press is left
    silently orphaned (no further event for it) until/unless another
    A0_B0 press arrives. This is the SAME generic orphan mechanism as any
    lost-release case -- see press_timing.py's module docstring -- proven
    here explicitly for the combo/partial-release case the phase spec
    calls out by name.
    """
    schedule = Mock(return_value=Mock())
    tracker = PressTimingTracker(scheduler=schedule)
    combo_actions: list[PressAction] = []
    a0_actions: list[PressAction] = []
    tracker.set_listener(ButtonPattern.A0_B0, combo_actions.append)
    tracker.set_listener(ButtonPattern.A0, a0_actions.append)

    tracker.handle_button_state(_press(ButtonPattern.A0_B0))
    tracker.handle_button_state(_release(ButtonPattern.A0))

    assert combo_actions == [PressAction.PRESS]  # no short_press, no release
    assert a0_actions == [PressAction.RELEASE]  # only the raw release, no short_press

    # A0_B0's open press is still there, untouched, until a real A0_B0
    # press resets it.
    assert ButtonPattern.A0_B0 in tracker._open

    combo_actions.clear()
    tracker.handle_button_state(_press(ButtonPattern.A0_B0))

    assert combo_actions == [PressAction.PRESS]  # fresh press, orphan cleared


# ---------------------------------------------------------------------------
# Listener management
# ---------------------------------------------------------------------------


def test_set_listener_none_clears_it_and_suppresses_future_emits():
    tracker, _schedule, actions = _tracker()
    tracker.set_listener(ButtonPattern.A0, None)

    tracker.handle_button_state(_press())

    assert actions == []


def test_no_listener_registered_is_a_silent_no_op():
    schedule = Mock(return_value=Mock())
    tracker = PressTimingTracker(scheduler=schedule)

    tracker.handle_button_state(_press(ButtonPattern.A0))  # must not raise
    tracker.handle_button_state(_release(ButtonPattern.A0))


# ---------------------------------------------------------------------------
# No scheduler configured (defensive default)
# ---------------------------------------------------------------------------


def test_no_scheduler_configured_still_emits_press_and_release_but_never_long_press():
    tracker = PressTimingTracker()  # scheduler defaults to None
    actions: list[PressAction] = []
    tracker.set_listener(ButtonPattern.A0, actions.append)

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
    tracker.set_listener(ButtonPattern.A0, Mock())
    tracker.set_listener(ButtonPattern.A1, Mock())

    tracker.handle_button_state(_press(ButtonPattern.A0))
    tracker.handle_button_state(_press(ButtonPattern.A1))

    tracker.clear()

    assert cancel.call_count == 2


def test_clear_leaves_the_tracker_ready_for_a_fresh_press():
    schedule = Mock(return_value=Mock())
    tracker = PressTimingTracker(scheduler=schedule)
    actions: list[PressAction] = []
    tracker.set_listener(ButtonPattern.A0, actions.append)

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
