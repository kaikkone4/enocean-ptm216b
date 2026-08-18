"""Per-config-entry runtime state for the passive observer."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

DESIGNATION_CAPTURE_SECONDS = 30.0
CaptureTimerCancel = Callable[[], None]
CaptureScheduler = Callable[[float, Callable[[], None]], CaptureTimerCancel]


class CaptureState(Enum):
    """Lifecycle states for the bounded manual designation capture."""

    INERT = "inert"
    CAPTURING = "capturing"


@dataclass
class Ptm216bRuntimeData:
    """Ephemeral passive-observation and designation-capture state."""

    _hmac_secret: bytes = field(repr=False)
    advertisement_count: int = 0
    capture_state: CaptureState = CaptureState.INERT
    designation_candidates: set[str] = field(default_factory=set, repr=False)
    capture_timer: CaptureTimerCancel | None = field(default=None, repr=False)

    def start_designation_capture(self, schedule: CaptureScheduler) -> None:
        """Start a bounded capture only in response to a manual request."""
        self.cancel_designation_capture()
        self.capture_state = CaptureState.CAPTURING
        self.capture_timer = schedule(
            DESIGNATION_CAPTURE_SECONDS, self._finish_designation_capture
        )

    def cancel_designation_capture(self) -> None:
        """Cancel the timer and discard all ephemeral candidates."""
        cancel_timer = self.capture_timer
        self.capture_timer = None
        if cancel_timer is not None:
            cancel_timer()
        self.capture_state = CaptureState.INERT
        self.designation_candidates.clear()

    def _finish_designation_capture(self) -> None:
        """Return to inert state when the bounded timer expires."""
        self.capture_state = CaptureState.INERT
        self.designation_candidates.clear()
        self.capture_timer = None
