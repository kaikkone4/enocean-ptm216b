"""Per-config-entry runtime state for the passive observer."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Callable

from .commissioning_store import CommissioningStore
from .evidence_capture import EvidenceCollector, EvidenceScheduler, EvidenceState
from .identity import device_handle, device_identifier
from .press_timing import PressAction, PressTimingTracker
from .radio_census import RadioCensus, RadioCensusScheduler, RadioCensusState
from .telegram import ButtonPattern, Ptm216bButtonState, normalize_button_pattern

DESIGNATION_BASELINE_SECONDS = 10.0
DESIGNATION_CAPTURE_SECONDS = 30.0
# Three observations reject one-off ambient noise without interpreting timing or payload.
MINIMUM_DESIGNATION_OBSERVATIONS = 3
CaptureTimerCancel = Callable[[], None]
CaptureScheduler = Callable[[float, Callable[[], None]], CaptureTimerCancel]
CaptureStateListener = Callable[[], None]
# Extended in Phase 5B from Callable[[bool], None] (press/release only) to
# carry every press_timing.PressAction (press/release/short_press/
# long_press) -- see press_timing.py's module docstring for the state
# machine that decides when each one fires.
ButtonEventListener = Callable[[PressAction], None]
DiagnosticsListener = Callable[[], None]
# The injection point letting config_flow.py (the one place with a `hass`
# reference) register this runtime's own broad, unfiltered, still-passive
# Bluetooth callback for exactly the radio census's own bounded duration --
# see radio_census.py's module docstring for why the normal
# manufacturer-filtered callback in __init__.py cannot be reused for this.
# The handler signature mirrors record_advertisement_observation's own
# (address, manufacturer_data, connectable) shape, plus the service-UUID set
# the census also needs.
RadioCensusCallbackCancel = Callable[[], None]
RadioCensusAdvertisementHandler = Callable[
    [str, dict[int, bytes], set[str], bool], None
]
RadioCensusRegisterUnfiltered = Callable[
    [RadioCensusAdvertisementHandler], RadioCensusCallbackCancel
]
RadioCensusDiagnosticsListener = Callable[[], None]


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
class CommissionedSwitchRuntime:
    """Runtime-only counters, entity listeners, and the per-switch replay lock
    for one commissioned switch (see ``commissioning_store.CommissioningStore``
    for the durable key/name/counter record this complements).

    ``lock`` is the ONE mechanism serializing concurrent Bluetooth callbacks
    for the SAME switch, per docs/decoder-test-preparation.md's "Fail-closed
    decoder contract" item 6 ("Concurrent callbacks must not both accept the
    same counter"). It is created once, lazily, by
    :meth:`Ptm216bRuntimeData.commissioned_switch_runtime` and kept for the
    runtime's lifetime -- never recreated per-callback.

    ``verified_count`` and ``rejected_count`` are plain, restart-resetting
    runtime counters (the durable, restart-surviving state is the sequence
    counter in ``commissioning_store.CommissionedSwitch``, not these). Exactly
    one of "verified", "rejected", or neither increments per processed
    telegram -- see :meth:`record_verified_and_fire` and
    :meth:`record_rejected` for the single, precise rule: "verified" is a
    telegram that passed shape, MIC, and counter (``ACCEPTED``) verification
    AND decoded to one of the six accepted button patterns, and therefore
    fired an event; "rejected" is every other outcome that reached a
    decision (parse failure, MIC failure, ``DUPLICATE``, ``REPLAY_
    REJECTED``, or a status decode failure on an already-``ACCEPTED``
    counter); first-trust counter initialization (see ``button_pipeline.py``)
    increments neither.

    ``rockers`` (Phase 5D) is this switch's configured rocker count -- ``1``
    for a single full-width rocker plate, ``2`` (the default here, matching
    ``config_flow.py``'s own subentry-schema default) for the normal
    two-rocker-pair module. Set once, exactly like ``press_tracker.
    threshold_ms``/``.scheduler`` already are, by ``event.py``'s
    ``async_setup_entry`` from the switch's subentry data. It is what
    :meth:`record_verified_and_fire` passes to
    ``telegram.normalize_button_pattern`` -- a 2-rocker switch (or the
    default before ``event.py`` has run once) exposes all six
    :class:`~telegram.ButtonPattern` values as distinct event entities/
    device triggers; a 1-rocker switch silently aliases A0/B0/A0+B0 to one
    logical "A0" and A1/B1/A1+B1 to one logical "A1", so only two entities
    ever exist for it and it never matters exactly where on the wide plate
    the user pressed.

    ``press_tracker`` (Phase 5B) is the one instance of
    ``press_timing.PressTimingTracker`` for this switch: every verified,
    normalized button state is routed through it rather than fired
    directly, so a single button-pattern-entity listener transparently
    receives raw ``press``, raw ``release``, and the derived ``short_
    press``/``long_press`` it decides. Its ``threshold_ms``/``scheduler``
    are configured once by ``event.py``'s ``async_setup_entry`` from the
    switch's subentry data -- this dataclass just owns and clears it.
    """

    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    verified_count: int = 0
    rejected_count: int = 0
    rockers: int = 2
    press_tracker: PressTimingTracker = field(
        default_factory=PressTimingTracker, repr=False
    )
    _diagnostics_listeners: list[DiagnosticsListener] = field(
        default_factory=list, repr=False
    )

    def record_rejected(self) -> None:
        """Count one rejected telegram; never fires a button event."""
        self.rejected_count += 1
        self._notify_diagnostics()

    def record_verified_and_fire(self, button_state: Ptm216bButtonState) -> None:
        """Count one fully-verified telegram, normalize it, and route it to
        ``press_tracker``.

        Normalizes ``button_state.pattern`` via ``telegram.
        normalize_button_pattern(..., self.rockers)`` before handing it to
        the tracker, so a 1-rocker switch's A0/B0/A0+B0 (or A1/B1/A1+B1)
        raw patterns all resolve to the one logical A0 (or A1) listener --
        only that pattern's registered listener, if any, ever fires. On a
        2-rocker switch normalization is the identity function, so this is
        unobservable there: still only the exact decoded pattern's listener
        fires, exactly as before Phase 5D.
        """
        self.verified_count += 1
        self._notify_diagnostics()
        normalized_pattern = normalize_button_pattern(
            button_state.pattern, self.rockers
        )
        normalized_state = replace(button_state, pattern=normalized_pattern)
        self.press_tracker.handle_button_state(normalized_state)

    def add_diagnostics_listener(self, listener: DiagnosticsListener) -> None:
        """Subscribe one diagnostic sensor (Verified or Rejected telegrams)."""
        self._diagnostics_listeners.append(listener)

    def remove_diagnostics_listener(self, listener: DiagnosticsListener) -> None:
        """Unsubscribe a diagnostic sensor without retaining a stale reference."""
        if listener in self._diagnostics_listeners:
            self._diagnostics_listeners.remove(listener)

    def set_event_listener(
        self, pattern: ButtonPattern, listener: ButtonEventListener | None
    ) -> None:
        """Register (or, with ``None``, clear) one pattern's event-entity listener.

        Delegates to ``press_tracker.set_listener`` -- there is only ever
        one registered listener per pattern, shared by every action
        (press/release/short_press/long_press) that pattern emits.
        """
        self.press_tracker.set_listener(pattern, listener)

    def _notify_diagnostics(self) -> None:
        for listener in self._diagnostics_listeners:
            listener()


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
    radio_census: RadioCensus | None = field(default=None, repr=False)
    radio_census_state_listener: RadioCensusDiagnosticsListener | None = field(
        default=None, repr=False
    )
    _radio_census_unregister: RadioCensusCallbackCancel | None = field(
        default=None, repr=False
    )
    commissioning_store: CommissioningStore | None = field(default=None, repr=False)
    commissioned_switches: dict[str, CommissionedSwitchRuntime] = field(
        default_factory=dict, repr=False
    )

    def start_designation_capture(self, schedule: CaptureScheduler) -> None:
        """Start a bounded baseline only in response to a manual request.

        Cancels a *running* evidence capture and a *running* radio census
        only. A completed evidence window (``complete``/``no_data``/
        ``aborted``) or a completed radio census (``complete``) is left
        untouched so its structural summary survives until the user starts
        a new capture of that same kind or the entry is unloaded.
        """
        if (
            self.evidence_collector is not None
            and self.evidence_collector.state is EvidenceState.COLLECTING
        ):
            self.cancel_evidence_capture()
        if self.radio_census is not None and self.radio_census.state in (
            RadioCensusState.BASELINE,
            RadioCensusState.PRESS,
        ):
            self.cancel_radio_census()
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
        cancels a running designation capture and a running radio census,
        mirroring the reverse case in :meth:`start_designation_capture`.
        """
        if self.designated_identifier is None:
            return False
        if self.capture_state is not CaptureState.INERT:
            self.cancel_designation_capture()
        if self.radio_census is not None and self.radio_census.state in (
            RadioCensusState.BASELINE,
            RadioCensusState.PRESS,
        ):
            self.cancel_radio_census()
        self.cancel_evidence_capture()
        collector = EvidenceCollector(self.designated_identifier)
        collector.state_listener = self._notify_evidence_state
        # Assign before start() so the listener's first (COLLECTING) call
        # already sees this collector on the runtime data it reads from.
        self.evidence_collector = collector
        collector.start(schedule)
        return True

    def cancel_evidence_capture(self) -> None:
        """Cancel the evidence window and discard all ephemeral evidence state."""
        collector = self.evidence_collector
        if collector is None:
            return
        collector.cancel()

    def record_evidence_callback(
        self,
        identifier: str,
        manufacturer_data: dict[int, bytes],
        connectable: bool,
    ) -> None:
        """Feed one matching callback into the active evidence collector, if any.

        Notifies explicitly so a live ``callbacks_accepted`` increment is
        redrawn even when the callback did not itself trigger a state
        transition (a state transition already self-notifies via the
        collector's own ``state_listener``, making this a harmless
        no-op double notification in that case).
        """
        collector = self.evidence_collector
        if collector is None or collector.state is not EvidenceState.COLLECTING:
            return
        collector.record_callback(identifier, manufacturer_data, connectable)
        self._notify_evidence_state()

    def _notify_evidence_state(self) -> None:
        """Notify the privacy-safe evidence diagnostic entity of changes."""
        if self.evidence_state_listener is not None:
            self.evidence_state_listener()

    def start_radio_census(
        self,
        schedule: RadioCensusScheduler,
        register_unfiltered: RadioCensusRegisterUnfiltered,
    ) -> None:
        """Start the bounded baseline/press radio census.

        Cancels a *running* designation capture and a *collecting* evidence
        capture, mirroring :meth:`start_evidence_capture`'s own cross-cancel
        convention -- a completed designation/evidence result is left
        untouched. Does not require a designated device, unlike evidence
        capture: the census is a broad diagnostic, not a per-switch one.

        Registers this runtime's own unfiltered Bluetooth callback (via
        ``register_unfiltered``, injected by config_flow.py -- the one place
        with a ``hass`` reference) for exactly this census's duration; see
        :meth:`_notify_radio_census` for where it is unregistered again.
        Always replaces a previous census of its own, same as evidence
        capture replacing itself on a fresh :meth:`start_evidence_capture`.
        """
        if self.capture_state is not CaptureState.INERT:
            self.cancel_designation_capture()
        if (
            self.evidence_collector is not None
            and self.evidence_collector.state is EvidenceState.COLLECTING
        ):
            self.cancel_evidence_capture()
        self.cancel_radio_census()
        census = RadioCensus()
        census.state_listener = self._notify_radio_census
        self.radio_census = census
        census.start(schedule)
        self._radio_census_unregister = register_unfiltered(
            self._handle_radio_census_advertisement
        )

    def cancel_radio_census(self) -> None:
        """Cancel the census window and discard all ephemeral census state.

        A no-op if nothing is running -- ``census.cancel()`` still notifies
        :meth:`_notify_radio_census`, which unregisters this runtime's
        unfiltered callback if it is still registered (harmless if it was
        already unregistered by a prior ``complete`` transition).
        """
        census = self.radio_census
        if census is None:
            return
        census.cancel()

    def _handle_radio_census_advertisement(
        self,
        address: str,
        manufacturer_data: dict[int, bytes],
        service_uuids: set[str],
        connectable: bool,
    ) -> None:
        """Feed one broadly-matched callback into the active census, if any.

        Computes a transient pseudonymous identifier from the address only
        to route the callback -- the address itself is never retained here
        or passed any further; see ``radio_census.RadioCensus.
        record_advertisement`` for the identifier's own bounded, cleared
        lifetime once inside the census.
        """
        census = self.radio_census
        if census is None:
            return
        try:
            identifier = device_identifier(self._hmac_secret, address)
        except ValueError:
            return
        census.record_advertisement(
            identifier, manufacturer_data, service_uuids, connectable
        )
        self._notify_radio_census()

    def _notify_radio_census(self) -> None:
        """Unregister this runtime's unfiltered callback once the census
        leaves its listening phases, then notify the diagnostic entity.

        Covers every path that ends ``baseline``/``press``: the timer-driven
        ``complete`` transition and :meth:`cancel_radio_census` -- the same
        "a timer-driven terminal transition must still notify" rule fixed
        for evidence capture in Phase 5A. Also fires, harmlessly, on every
        accepted advertisement while still ``baseline``/``press`` (mirroring
        :meth:`record_evidence_callback`'s own double-notification note), so
        the sensor's live ``phase_advertisement_count`` stays current too.
        """
        census = self.radio_census
        if census is not None and census.state not in (
            RadioCensusState.BASELINE,
            RadioCensusState.PRESS,
        ):
            self._unregister_radio_census_callback()
        if self.radio_census_state_listener is not None:
            self.radio_census_state_listener()

    def _unregister_radio_census_callback(self) -> None:
        """Cancel this runtime's own unfiltered Bluetooth callback, if any."""
        unregister = self._radio_census_unregister
        self._radio_census_unregister = None
        if unregister is not None:
            unregister()

    def commissioned_switch_runtime(
        self, canonical_address: str
    ) -> CommissionedSwitchRuntime:
        """Return (lazily creating) the runtime record for one commissioned switch.

        Created once per canonical address and kept for the runtime's
        lifetime -- in particular, its :attr:`CommissionedSwitchRuntime.lock`
        must never be recreated per-callback, or concurrent callbacks for the
        same switch would no longer serialize against each other.
        """
        runtime = self.commissioned_switches.get(canonical_address)
        if runtime is None:
            runtime = CommissionedSwitchRuntime()
            self.commissioned_switches[canonical_address] = runtime
        return runtime

    def clear_press_timers(self) -> None:
        """Cancel every commissioned switch's open press-hold timers.

        Called on unload (see ``__init__.py``'s ``async_unload_entry``) so
        no ``press_timing.PressTimingTracker`` timer ever outlives this
        entry. A reload always rebuilds ``Ptm216bRuntimeData`` from
        scratch, so this is the only cleanup point needed -- there is no
        separate per-switch decommission hook, matching
        ``cancel_designation_capture``/``cancel_evidence_capture``'s own
        unload-only cleanup convention.
        """
        for switch_runtime in self.commissioned_switches.values():
            switch_runtime.press_tracker.clear()

    def compute_device_identifier(self, address: str) -> str:
        """Return the local HMAC identifier for an address, using this entry's secret.

        The one sanctioned way for code outside this module (in particular
        ``config_flow.py``) to derive an identifier from an address -- callers
        must never reach into ``_hmac_secret`` directly.
        """
        return device_identifier(self._hmac_secret, address)

    def commissioned_device_handle(self, canonical_address: str) -> str:
        """Return the non-reversible device-registry handle for one switch.

        Reuses the existing HMAC + truncation convention exactly (see
        ``identity.device_identifier``/``identity.device_handle``) -- the
        canonical address itself is never used as a device-registry
        identifier.
        """
        return device_handle(self.compute_device_identifier(canonical_address))
