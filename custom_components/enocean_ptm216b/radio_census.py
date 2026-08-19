"""Bounded, in-memory-only broad-spectrum Bluetooth census (Phase 7).

Why this exists
----------------

The user's EnOcean PTM switches can be paired to a **Casambi** lighting
network. Once paired, this integration's own normal, manufacturer-filtered
callback (see ``__init__.py``, matcher ``{"manufacturer_id": 0x03DA,
"connectable": False}``) sees ZERO advertisements from that switch, and only
a physical EnOcean-Tool factory reset restores it. Research recorded in
docs/evidence-findings.md and docs/decoder-test-preparation.md established
that Casambi commissions PTM modules over NFC, and that PTM modules have
NFC-writable registers able to move telegrams to a different manufacturer
ID, to non-BLE "custom radio channels" (40-78), or to a different PHY/data
rate. Which of those Casambi actually does is UNKNOWN.

New evidence, captured by the user with nRF Connect on a phone standing next
to a Casambi luminaire, showed manufacturer data "Casambi Technologies Oy
<0x03C3>", Data Length 159 bytes, "Device Type: Beacon", connectable,
~535 ms interval. A 159-byte payload is far beyond the 31-byte legacy
advertising limit, so Casambi devices use BLE 5 **extended advertising**.
That matters here because the user's ESPHome Bluetooth proxies are
``board: esp32dev`` -- original ESP32, Bluetooth 4.2, which cannot receive
extended advertising (or 2M/Coded PHY) at all. So a plausible, previously
unconsidered explanation for the "silence" is not that a Casambi-paired
switch stops transmitting, but that it moves to a transmission form this
Home Assistant installation's specific receive path is physically incapable
of hearing, while a modern phone standing next to it receives it fine.

This module answers, with data: what can this installation's Bluetooth
receive path actually hear, and does anything appear in correlation with
pressing a switch? It is the closest architectural sibling to
``evidence_capture.py`` (bounded, manually started, privacy-preserving,
in-memory, driven by an injected scheduler with the same
``Callable[[float, Callable[[], None]], cancel]`` convention) but,
deliberately, much BROADER: evidence capture inspects only the
already-designated switch's own already-EnOcean-filtered telegrams, while
this census inspects every nearby advertisement, from any manufacturer,
connectable or not -- see ``runtime_data.Ptm216bRuntimeData.
start_radio_census`` for how it registers its own additional, unfiltered,
still-passive Bluetooth callback for exactly its own bounded duration,
separate from and without altering this integration's normal filtered one.

Privacy contract (stricter than evidence capture, since this is a broad
scan of *everything* nearby, not one designated device)
---------------------------------------------------------------------------

Never retained or exposed, anywhere: a BLE address, a device/local name (a
local name can be personal, e.g. "Someone's iPhone"), a raw payload byte,
RSSI, tx power, a timestamp, a pseudonymous identifier itself, or the
receiving adapter/proxy identity. Only counts, byte lengths, manufacturer
ID keys, short-form service UUIDs, and booleans are ever aggregated or
exposed. Per-device identifiers are used only transiently, inside this
module's own bounded per-window working set, to compute a
``distinct_devices`` COUNT; that working set is never exposed and is
cleared at every window end (``complete`` or ``cancel``). Aggregate state
is entirely runtime-only and is cleared on cancel and on every fresh
``start``; the owning runtime layer additionally clears it on unload.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

# A quiet window long enough to establish "nothing pressed" without asking
# the user to wait long, followed by a window long enough for several
# deliberate presses of the switch under test.
BASELINE_SECONDS = 20.0
PRESS_SECONDS = 40.0

# Bound memory against a busy 2.4 GHz environment: once either cap is hit,
# existing tracked keys keep counting, but no new key is added, and the
# exposed summary's `truncated` flag is set so a capped result is never
# silently misread as a complete picture of everything nearby.
MAX_TRACKED_MANUFACTURER_IDS = 64
MAX_TRACKED_SERVICE_UUIDS = 32

# Aggregation key for every advertisement that carries no manufacturer data
# at all -- a real, common case (many devices advertise only service UUIDs),
# and structurally distinct from "manufacturer ID 0" or an empty payload.
NO_MANUFACTURER_DATA_KEY = "no_manufacturer_data"

RadioCensusTimerCancel = Callable[[], None]
RadioCensusScheduler = Callable[[float, Callable[[], None]], RadioCensusTimerCancel]
RadioCensusStateListener = Callable[[], None]

# The Bluetooth SIG base UUID, `0000xxxx-0000-1000-8000-00805F9B34FB`, is the
# only shape a 16-bit "short form" service UUID can be recovered from
# losslessly; every other (128-bit vendor-specific) UUID is skipped rather
# than retained, per the module docstring's privacy contract.
_BASE_UUID_SUFFIX = "-0000-1000-8000-00805f9b34fb"
_SHORT_UUID_RE = re.compile(r"^0000([0-9a-f]{4})" + re.escape(_BASE_UUID_SUFFIX) + r"$")


class RadioCensusState(Enum):
    """Lifecycle states for the bounded two-phase radio census."""

    INERT = "inert"
    BASELINE = "baseline"
    PRESS = "press"
    COMPLETE = "complete"


def bucket_key_label(key: int | str) -> str:
    """Return the stable, JSON-friendly label for one aggregation bucket."""
    if isinstance(key, str):
        return key
    return f"0x{key:04x}"


def _short_service_uuid(uuid: str) -> str | None:
    """Return a 16-bit service UUID's short form, or ``None`` if it is a
    128-bit vendor-specific UUID (not recoverable from the SIG base UUID,
    and skipped rather than retained -- see the module docstring)."""
    match = _SHORT_UUID_RE.match(uuid.lower())
    if match is None:
        return None
    return f"0x{match.group(1)}"


@dataclass
class ManufacturerSummary:
    """Aggregate, privacy-safe summary for one bucket, never raw bytes."""

    baseline_count: int
    press_count: int
    distinct_devices: int
    max_value_length: int
    connectable_seen: bool
    service_uuids: list[str]


@dataclass
class RadioCensusSummary:
    """Structural summary exposed by the diagnostic sensor.

    ``entries`` is keyed by ``bucket_key_label`` (a lowercase
    ``"0x03da"``-style string for a manufacturer ID, or
    :data:`NO_MANUFACTURER_DATA_KEY`). ``truncated`` is ``True`` if the
    manufacturer-ID or service-UUID cap was ever hit -- a capped result must
    never be silently misread as a complete picture of everything nearby.
    """

    entries: dict[str, ManufacturerSummary]
    truncated: bool


@dataclass
class _ManufacturerBucket:
    """Internal per-key aggregate.

    ``_seen_identifiers`` is working state only -- fed transiently by
    :meth:`RadioCensus.record_advertisement`, read only to materialize
    ``distinct_devices`` as a plain count at window end, and cleared
    immediately after (see :meth:`RadioCensus._complete`). It is never
    exposed, and never appears in an exported summary.
    """

    baseline_count: int = 0
    press_count: int = 0
    max_value_length: int = 0
    connectable_seen: bool = False
    distinct_devices: int = 0
    service_uuids: set[str] = field(default_factory=set, repr=False)
    _seen_identifiers: set[str] = field(default_factory=set, repr=False)


@dataclass
class RadioCensus:
    """Manually started, bounded, two-phase (baseline/press) radio census.

    Fed one record per received advertisement, of ANY manufacturer,
    connectable or not, via :meth:`record_advertisement` -- but only
    inspects it while ``state`` is ``BASELINE`` or ``PRESS``. Inactive
    (``INERT``) by default; a window is armed only by :meth:`start` and
    always ends either as ``COMPLETE`` (both phase timers elapsed) or, via
    :meth:`cancel`, back at ``INERT``. There is no ``ABORTED`` state here --
    unlike ``evidence_capture.py``, this census does not inspect telegram
    structure closely enough to ever recognize a possible commissioning
    payload; it only ever aggregates coarse lengths, counts, and booleans.
    """

    state: RadioCensusState = RadioCensusState.INERT
    state_listener: RadioCensusStateListener | None = field(default=None, repr=False)
    _buckets: dict[int | str, _ManufacturerBucket] = field(
        default_factory=dict, repr=False
    )
    _truncated: bool = False
    _timer: RadioCensusTimerCancel | None = field(default=None, repr=False)
    _scheduler: RadioCensusScheduler | None = field(default=None, repr=False)

    @property
    def current_phase_count(self) -> int:
        """Return the running advertisement total for the active phase only.

        Valid in every state; ``0`` outside ``BASELINE``/``PRESS``.
        """
        if self.state is RadioCensusState.BASELINE:
            return sum(bucket.baseline_count for bucket in self._buckets.values())
        if self.state is RadioCensusState.PRESS:
            return sum(bucket.press_count for bucket in self._buckets.values())
        return 0

    @property
    def summary(self) -> RadioCensusSummary | None:
        """Return the aggregate summary once the window is ``COMPLETE``.

        Returns ``None`` in every other state -- there is nothing to expose
        while inert, and a live window exposes only :attr:`current_phase_count`.
        """
        if self.state is not RadioCensusState.COMPLETE:
            return None

        entries = {
            bucket_key_label(key): ManufacturerSummary(
                baseline_count=bucket.baseline_count,
                press_count=bucket.press_count,
                distinct_devices=bucket.distinct_devices,
                max_value_length=bucket.max_value_length,
                connectable_seen=bucket.connectable_seen,
                service_uuids=sorted(bucket.service_uuids),
            )
            for key, bucket in self._buckets.items()
        }
        return RadioCensusSummary(entries=entries, truncated=self._truncated)

    def start(self, schedule: RadioCensusScheduler) -> None:
        """Arm the bounded baseline phase; press follows automatically."""
        self._cancel_timer()
        self._reset_transient_state()
        self._scheduler = schedule
        self._timer = schedule(BASELINE_SECONDS, self._finish_baseline)
        self._set_state(RadioCensusState.BASELINE)

    def cancel(self) -> None:
        """Cancel the window and discard all aggregate/working state."""
        self._cancel_timer()
        self._reset_transient_state()
        self._scheduler = None
        self._set_state(RadioCensusState.INERT)

    def record_advertisement(
        self,
        identifier: str,
        manufacturer_data: dict[int, bytes],
        service_uuids: set[str],
        connectable: bool,
    ) -> None:
        """Aggregate one received advertisement; ignored outside baseline/press.

        ``identifier`` is a transient pseudonymous identifier computed by
        the caller (see ``runtime_data.Ptm216bRuntimeData.
        _handle_radio_census_advertisement``) -- it is used here only to
        de-duplicate a bucket's ``distinct_devices`` working set, is never
        itself retained beyond that set, and that set is cleared at every
        window end. Never inspects, retains, or exposes a raw payload byte,
        an address, RSSI, tx power, a timestamp, or a device/local name.
        """
        if self.state not in (RadioCensusState.BASELINE, RadioCensusState.PRESS):
            return

        if not manufacturer_data:
            bucket = self._record_bucket(
                NO_MANUFACTURER_DATA_KEY, identifier, 0, connectable
            )
            if bucket is not None:
                self._add_service_uuids(bucket, service_uuids)
            return

        for manufacturer_id, value in manufacturer_data.items():
            self._record_bucket(manufacturer_id, identifier, len(value), connectable)

    def _record_bucket(
        self,
        key: int | str,
        identifier: str,
        value_length: int,
        connectable: bool,
    ) -> _ManufacturerBucket | None:
        """Update one bucket's counts; return it, or ``None`` if capped out."""
        bucket = self._buckets.get(key)
        if bucket is None:
            if len(self._buckets) >= MAX_TRACKED_MANUFACTURER_IDS:
                self._truncated = True
                return None
            bucket = _ManufacturerBucket()
            self._buckets[key] = bucket

        if self.state is RadioCensusState.BASELINE:
            bucket.baseline_count += 1
        else:
            bucket.press_count += 1
        # This is the extended-advertising tell: any legacy BLE advertisement
        # payload is capped at 31 bytes total (so a manufacturer-data VALUE
        # is well under that); a value materially longer than ~27-31 bytes
        # proves the receive path is getting BLE 5 extended advertising.
        bucket.max_value_length = max(bucket.max_value_length, value_length)
        if connectable:
            bucket.connectable_seen = True
        bucket._seen_identifiers.add(identifier)
        return bucket

    def _add_service_uuids(
        self, bucket: _ManufacturerBucket, service_uuids: set[str]
    ) -> None:
        """Add recognizable 16-bit short-form service UUIDs, capped."""
        for uuid in service_uuids:
            short = _short_service_uuid(uuid)
            if short is None or short in bucket.service_uuids:
                continue
            if len(bucket.service_uuids) >= MAX_TRACKED_SERVICE_UUIDS:
                self._truncated = True
                continue
            bucket.service_uuids.add(short)

    def _finish_baseline(self) -> None:
        """Baseline timer fired: arm the press phase."""
        self._timer = None
        schedule = self._scheduler
        if schedule is None:
            self._complete()
            return
        self._timer = schedule(PRESS_SECONDS, self._finish_press)
        self._set_state(RadioCensusState.PRESS)

    def _finish_press(self) -> None:
        """Press timer fired: end the window as complete."""
        self._timer = None
        self._scheduler = None
        self._complete()

    def _complete(self) -> None:
        """Materialize each bucket's distinct-device COUNT, then discard the
        working identifier set that produced it -- never exposed as-is.
        """
        for bucket in self._buckets.values():
            bucket.distinct_devices = len(bucket._seen_identifiers)
            bucket._seen_identifiers = set()
        self._set_state(RadioCensusState.COMPLETE)

    def _reset_transient_state(self) -> None:
        """Clear every retained bucket and the truncation flag."""
        self._buckets = {}
        self._truncated = False

    def _cancel_timer(self) -> None:
        timer = self._timer
        self._timer = None
        if timer is not None:
            timer()

    def _set_state(self, state: RadioCensusState) -> None:
        """Transition state and notify the listener on every transition.

        Covers every path that changes ``state``: start, cancel, the
        baseline-to-press advance, and the timer-driven terminal ``complete``
        transition -- the same timer-driven-terminal-must-notify rule fixed
        for evidence capture in Phase 5A (see ``evidence_capture.
        EvidenceCollector._set_state``'s own docstring).
        """
        self.state = state
        if self.state_listener is not None:
            self.state_listener()
