"""Per-config-entry runtime state for the passive observer."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from .evidence_capture import EvidenceCollector, EvidenceScheduler, EvidenceState
from .identity import device_identifier

DESIGNATION_BASELINE_SECONDS = 10.0
DESIGNATION_CAPTURE_SECONDS = 30.0
# Three observations reject one-off ambient noise without interpreting timing or payload.
MINIMUM_DESIGNATION_OBSERVATIONS = 3
CaptureTimerCancel = Callable[[], None]
CaptureScheduler = Callable[[float, Callable[[], None]], CaptureTimerCancel]
CaptureStateListener = Callable[[], None]


class CaptureState(Enum):
    """Lifecycle states for the bounded manual designation capture."""

    INERT = "inert"
    BASELINE = "baseline"
    PRESS = "press"
    CONFIRMING = "confirmation"


class DesignationOutcome(Enum):
    """Privacy-safe result values exposed by the diagnostic entity."""

    SELECTED = "selected"
    NO_SELECTION = "no_selection"


@dataclass
class DesignationCandidate:
    """Aggregate count only for one ephemeral pseudonymous candidate."""

    observation_count: int


@dataclass
class Ptm216bRuntimeData:
    """Ephemeral passive-observation and designation-capture state."""

    _hmac_secret: bytes = field(repr=False)
    advertisement_count: int = 0
    capture_state: CaptureState = CaptureState.INERT
    capture_observation_count: int = 0
    designation_outcome: DesignationOutcome = DesignationOutcome.NO_SELECTION
    designated_identifier: str | None = field(default=None, repr=False)
    first_window_identifier: str | None = field(default=None, repr=False)
    designation_candidates: dict[str, DesignationCandidate] = field(
        default_factory=dict, repr=False
    )
    capture_timer: CaptureTimerCancel | None = field(default=None, repr=False)
    capture_scheduler: CaptureScheduler | None = field(default=None, repr=False)
    capture_state_listener: CaptureStateListener | None = field(
        default=None, repr=False
    )
    evidence_collector: EvidenceCollector | None = field(default=None, repr=False)
    evidence_state_listener: CaptureStateListener | None = field(
        default=None, repr=False
    )

    def start_designation_capture(self, schedule: CaptureScheduler) -> None:
        """Start a bounded baseline only in response to a manual request."""
        self.cancel_evidence_capture()
        cancel_timer = self.capture_timer
        if cancel_timer is not None:
            cancel_timer()
        self.capture_timer = None
        self.capture_observation_count = 0
        self.designation_outcome = DesignationOutcome.NO_SELECTION
        self.designated_identifier = None
        self.first_window_identifier = None
        self.designation_candidates.clear()
        self.capture_scheduler = schedule
        self.capture_state = CaptureState.BASELINE
        self.capture_timer = schedule(
            DESIGNATION_BASELINE_SECONDS, self._finish_designation_baseline
        )
        self._notify_capture_state()

    def cancel_designation_capture(self) -> None:
        """Cancel the timer and discard all ephemeral capture state."""
        cancel_timer = self.capture_timer
        self.capture_timer = None
        if cancel_timer is not None:
            cancel_timer()
        self.capture_state = CaptureState.INERT
        self.capture_observation_count = 0
        self.designation_outcome = DesignationOutcome.NO_SELECTION
        self.designated_identifier = None
        self.first_window_identifier = None
        self.designation_candidates.clear()
        self.capture_scheduler = None
        self._notify_capture_state()

    def record_designation_candidate(
        self, address: str, _monotonic_time: float | None = None
    ) -> None:
        """Aggregate a transient address only during a bounded capture phase."""
        if self.capture_state not in (
            CaptureState.BASELINE,
            CaptureState.PRESS,
            CaptureState.CONFIRMING,
        ):
            return
        try:
            identifier = device_identifier(self._hmac_secret, address)
        except ValueError:
            return

        self.capture_observation_count += 1
        candidate = self.designation_candidates.get(identifier)
        if candidate is None:
            self.designation_candidates[identifier] = DesignationCandidate(
                observation_count=1
            )
        else:
            candidate.observation_count += 1
        self._notify_capture_state()

    def record_advertisement_observation(
        self,
        address: str,
        manufacturer_data: dict[int, bytes],
        connectable: bool,
    ) -> None:
        """Feed one observed advertisement to designation and evidence capture.

        Keeps designation-candidate recording unchanged and, exactly like
        designation does, computes a transient HMAC identifier from the
        address only to route the callback to the evidence collector. The
        address itself and the identifier are never retained here.
        """
        self.record_designation_candidate(address)
        try:
            identifier = device_identifier(self._hmac_secret, address)
        except ValueError:
            return
        self.record_evidence_callback(identifier, manufacturer_data, connectable)

    def _finish_designation_baseline(self) -> None:
        """Require a quiet baseline before arming the first press window."""
        if self.designation_candidates:
            self._finish_without_selection()
            return
        schedule = self.capture_scheduler
        if schedule is None:
            self._finish_without_selection()
            return
        self.capture_observation_count = 0
        self.capture_state = CaptureState.PRESS
        self.capture_timer = schedule(
            DESIGNATION_CAPTURE_SECONDS, self._finish_designation_capture
        )
        self._notify_capture_state()

    def _finish_designation_capture(self) -> None:
        """Arm confirmation only for one unique first-window candidate."""
        candidates = tuple(self.designation_candidates.items())
        if not (
            len(candidates) == 1
            and candidates[0][1].observation_count >= MINIMUM_DESIGNATION_OBSERVATIONS
        ):
            self._finish_without_selection()
            return
        schedule = self.capture_scheduler
        if schedule is None:
            self._finish_without_selection()
            return
        self.first_window_identifier = candidates[0][0]
        self.designation_candidates.clear()
        self.capture_observation_count = 0
        self.capture_state = CaptureState.CONFIRMING
        self.capture_timer = schedule(
            DESIGNATION_CAPTURE_SECONDS, self._finish_designation_confirmation
        )
        self._notify_capture_state()

    def _finish_designation_confirmation(self) -> None:
        """Select only the same sole candidate from both independent windows."""
        candidates = tuple(self.designation_candidates.items())
        if not (
            len(candidates) == 1
            and candidates[0][0] == self.first_window_identifier
            and candidates[0][1].observation_count >= MINIMUM_DESIGNATION_OBSERVATIONS
        ):
            self._finish_without_selection()
            return
        self.capture_state = CaptureState.INERT
        self.designated_identifier = candidates[0][0]
        self.first_window_identifier = None
        self.designation_outcome = DesignationOutcome.SELECTED
        self.designation_candidates.clear()
        self.capture_timer = None
        self.capture_scheduler = None
        self._notify_capture_state()

    def _finish_without_selection(self) -> None:
        """Fail closed and discard all ephemeral capture data."""
        self.capture_state = CaptureState.INERT
        self.designated_identifier = None
        self.first_window_identifier = None
        self.designation_outcome = DesignationOutcome.NO_SELECTION
        self.designation_candidates.clear()
        self.capture_timer = None
        self.capture_scheduler = None
        self._notify_capture_state()

    def _notify_capture_state(self) -> None:
        """Notify the privacy-safe diagnostic entity of aggregate changes."""
        if self.capture_state_listener is not None:
            self.capture_state_listener()

    def start_evidence_capture(self, schedule: EvidenceScheduler) -> bool:
        """Start bounded structural evidence capture for the designated switch.

        Refuses as a no-op and returns ``False`` when no device has been
        designated yet in this runtime session; the config flow maps that to
        the ``no_designated_device`` abort reason. Starting evidence capture
        cancels a running designation capture, mirroring the reverse case in
        :meth:`start_designation_capture`.
        """
        if self.designated_identifier is None:
            return False
        if self.capture_state is not CaptureState.INERT:
            self.cancel_designation_capture()
        self.cancel_evidence_capture()
        collector = EvidenceCollector(self.designated_identifier)
        collector.start(schedule)
        self.evidence_collector = collector
        self._notify_evidence_state()
        return True

    def cancel_evidence_capture(self) -> None:
        """Cancel the evidence window and discard all ephemeral evidence state."""
        collector = self.evidence_collector
        if collector is None:
            return
        collector.cancel()
        self._notify_evidence_state()

    def record_evidence_callback(
        self,
        identifier: str,
        manufacturer_data: dict[int, bytes],
        connectable: bool,
    ) -> None:
        """Feed one matching callback into the active evidence collector, if any."""
        collector = self.evidence_collector
        if collector is None or collector.state is not EvidenceState.COLLECTING:
            return
        collector.record_callback(identifier, manufacturer_data, connectable)
        self._notify_evidence_state()

    def _notify_evidence_state(self) -> None:
        """Notify the privacy-safe evidence diagnostic entity of changes."""
        if self.evidence_state_listener is not None:
            self.evidence_state_listener()
