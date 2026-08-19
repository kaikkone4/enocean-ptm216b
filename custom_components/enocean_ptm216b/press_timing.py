"""Hold-time short/long press derivation, downstream of the verified pipeline.

Per (switch, button) pure state machine, fed only already-verified
``telegram.Ptm216bButtonState`` outputs of
``runtime_data.CommissionedSwitchRuntime.record_verified_and_fire`` -- i.e.
telegrams that already passed every gate in ``button_pipeline.py``'s
fail-closed pipeline (shape, MIC, counter/replay, status decode). This
module never sees, verifies, or reasons about a telegram, key, address, or
counter; it only turns a stream of verified press/release
:class:`~telegram.Ptm216bButtonState` values into four observable actions
-- raw ``press``, raw ``release``, derived ``short_press``, and derived
``long_press`` -- per button, using an injected scheduler with the exact
same ``Callable[[float, Callable[[], None]], cancel]`` convention as
``runtime_data.CaptureScheduler`` and ``evidence_capture.EvidenceScheduler``.

Long press is hold-time, not release-time: it fires the moment the
switch's configured threshold elapses while the button is still held, not
when it is eventually released (the user explicitly chose this over the
more common "fires on release after a long hold" convention).

Radio-loss safety (see docs/evidence-findings.md: a release is the
most-likely-to-be-lost PTM 216B telegram of the pair): a new verified press
for a button that already has an "open" (unreleased) press first resets
that button's state -- cancelling any running hold timer and emitting
nothing retroactively for the orphaned press -- before starting fresh. This
guarantees a lost release can never produce a spurious ``short_press``, a
duplicate ``long_press``, or any other inconsistency; the only cost is that
the orphaned press's own short/long resolution is silently abandoned, which
is strictly safer than guessing at it.

All state here is runtime-only (never persisted) and must be cleared -- via
:meth:`PressTimingTracker.clear` -- on unload and on decommissioning a
switch, exactly like every other ephemeral capture/session state in this
integration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from .telegram import Button, Ptm216bButtonState

# Sane bounds for the per-switch configurable hold threshold: short enough to
# feel responsive, long enough that an ordinary press never accidentally
# reads as a hold. See config_flow.py's key-entry/reconfigure schemas, which
# enforce these as the NumberSelector's min/max.
DEFAULT_LONG_PRESS_THRESHOLD_MS = 500
MIN_LONG_PRESS_THRESHOLD_MS = 200
MAX_LONG_PRESS_THRESHOLD_MS = 5000

PressTimerCancel = Callable[[], None]
PressScheduler = Callable[[float, Callable[[], None]], PressTimerCancel]


class PressAction(Enum):
    """The four observable actions this module ever emits, per button."""

    PRESS = "press"
    RELEASE = "release"
    SHORT_PRESS = "short_press"
    LONG_PRESS = "long_press"


PressActionListener = Callable[[PressAction], None]


@dataclass
class _OpenPress:
    """One button's in-flight, not-yet-released press; runtime-only."""

    cancel_timer: PressTimerCancel
    long_fired: bool = False


@dataclass
class PressTimingTracker:
    """Per-switch short/long press state machine; one instance per switch.

    ``threshold_ms`` and ``scheduler`` are set once by the caller (see
    ``event.py``'s ``async_setup_entry``, which reads the switch's
    ``long_press_threshold_ms`` subentry field and injects a
    ``homeassistant.helpers.event.async_call_later``-backed scheduler) --
    never read from config-entry/subentry data directly by this module,
    which stays pure and Home-Assistant-agnostic like the rest of this
    repo's pipeline modules.
    """

    threshold_ms: int = DEFAULT_LONG_PRESS_THRESHOLD_MS
    scheduler: PressScheduler | None = None
    _listeners: dict[Button, PressActionListener] = field(
        default_factory=dict, repr=False
    )
    _open: dict[Button, _OpenPress] = field(default_factory=dict, repr=False)

    def set_listener(
        self, button: Button, listener: PressActionListener | None
    ) -> None:
        """Register (or, with ``None``, clear) one button's action listener."""
        if listener is None:
            self._listeners.pop(button, None)
        else:
            self._listeners[button] = listener

    def handle_button_state(self, button_state: Ptm216bButtonState) -> None:
        """Feed one verified button state; the only entry point from the pipeline."""
        if button_state.is_press:
            self._handle_press(button_state.button)
        else:
            self._handle_release(button_state.button)

    def clear(self) -> None:
        """Cancel every open hold timer and discard all state.

        Call on unload and on decommissioning a switch -- never leaves a
        timer running past either point.
        """
        for open_press in self._open.values():
            open_press.cancel_timer()
        self._open.clear()

    def _handle_press(self, button: Button) -> None:
        # Radio-loss safety: a still-open previous press for this button
        # means its release was lost. Reset silently -- no retroactive
        # short_press/long_press for the orphan -- before starting fresh.
        self._reset_open(button)
        self._emit(button, PressAction.PRESS)
        # Created before scheduling and captured by identity (not looked up
        # by button) in the timer callback below: defense in depth so that
        # even a scheduler whose cancel is not perfectly reliable can never
        # resurrect long_press state for a *different*, newer press on the
        # same button after an orphan reset -- the identity check in
        # _fire_long_press is what actually enforces this, not just the
        # cancel call. Registered as "open" even with no scheduler
        # configured (defensive default -- production always injects one
        # via event.py): short_press/release resolution still works, only
        # long_press can never fire because no timer is ever scheduled.
        open_press = _OpenPress(cancel_timer=lambda: None)
        self._open[button] = open_press
        if self.scheduler is not None:
            open_press.cancel_timer = self.scheduler(
                self.threshold_ms / 1000.0,
                lambda: self._fire_long_press(button, open_press),
            )

    def _handle_release(self, button: Button) -> None:
        open_press = self._open.pop(button, None)
        if open_press is not None:
            # Safe even if the hold timer already fired: HA's own
            # async_call_later cancel handle (and every fake scheduler in
            # this repo's tests) is a no-op when called after firing.
            open_press.cancel_timer()
            if not open_press.long_fired:
                self._emit(button, PressAction.SHORT_PRESS)
        # A release with no open press (lost/duplicate press, or a release
        # for a button that was never pressed) still emits only the raw
        # release -- never a short_press.
        self._emit(button, PressAction.RELEASE)

    def _fire_long_press(self, button: Button, open_press: _OpenPress) -> None:
        if self._open.get(button) is not open_press:
            # Already reset (orphaned by a new press) or already released
            # between scheduling and firing -- including a stale callback
            # that fires despite cancellation -- nothing to do.
            return
        open_press.long_fired = True
        self._emit(button, PressAction.LONG_PRESS)

    def _reset_open(self, button: Button) -> None:
        open_press = self._open.pop(button, None)
        if open_press is not None:
            open_press.cancel_timer()

    def _emit(self, button: Button, action: PressAction) -> None:
        listener = self._listeners.get(button)
        if listener is not None:
            listener(action)
