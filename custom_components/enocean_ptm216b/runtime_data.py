"""Per-config-entry runtime state for the passive observer."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from .identity import device_identifier

DESIGNATION_CAPTURE_SECONDS = 30.0
CaptureTimerCancel = Callable[[], None]
CaptureScheduler = Callable[[float, Callable[[], None]], CaptureTimerCancel]


class CaptureState(Enum):
    """Lifecycle states for the bounded manual designation capture."""

    INERT = "inert"
    CAPTURING = "capturing"


@dataclass
class DesignationCandidate:
    """Aggregate timing only for one ephemeral pseudonymous candidate."""

    observation_count: int
    first_seen_monotonic: float
    last_seen_monotonic: float


@dataclass
class Ptm216bRuntimeData:
    """Ephemeral passive-observation and designation-capture state."""

    _hmac_secret: bytes = field(repr=False)
    advertisement_count: int = 0
    capture_state: CaptureState = CaptureState.INERT
    designated_identifier: str | None = field(default=None, repr=False)
    designation_candidates: dict[str, DesignationCandidate] = field(
        default_factory=dict, repr=False
    )
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
        self.designated_identifier = None
        self.designation_candidates.clear()

    def record_designation_candidate(self, address: str, monotonic_time: float) -> None:
        """Aggregate a transient address only while manual capture is active."""
        if self.capture_state is not CaptureState.CAPTURING:
            return
        try:
            identifier = device_identifier(self._hmac_secret, address)
        except ValueError:
            return

        candidate = self.designation_candidates.get(identifier)
        if candidate is None:
            self.designation_candidates[identifier] = DesignationCandidate(
                observation_count=1,
                first_seen_monotonic=monotonic_time,
                last_seen_monotonic=monotonic_time,
            )
            return
        candidate.observation_count += 1
        candidate.last_seen_monotonic = monotonic_time

    def _finish_designation_capture(self) -> None:
        """Fail closed and discard all candidates when the timer expires."""
        self.capture_state = CaptureState.INERT
        self.designated_identifier = None
        self.designation_candidates.clear()
        self.capture_timer = None
