"""Bounded, in-memory-only telegram-structure evidence capture (Phase 2).

This module never decodes a telegram, authenticates anything, or emits a
button action. It inspects only structural facts about what Home Assistant's
Bluetooth callback delivers for the already-designated switch: byte lengths,
an AD-prefix heuristic, sequence-counter *deltas* (never absolute counter
values), a switch-status XOR fingerprint (never the absolute status byte),
and duplicate-frame structure. See docs/decoder-test-preparation.md, "Exact
evidence required before parser code" and "Capture abort rules", for the
evidence contract this class implements.

Raw manufacturer-data bytes are held only transiently, inside this collector,
to compute the next callback's duplicate/XOR facts, and are discarded on
every abort, cancel, and window end. No raw payload byte, BLE address, or
full identifier is ever stored in an exposed field, appears in ``repr()``, or
is logged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from .const import ENOCEAN_MANUFACTURER_ID

EVIDENCE_CAPTURE_SECONDS = 90.0
# 64 structural records is ample evidence for the actuation/duplicate matrix
# in docs/decoder-test-preparation.md without an unbounded in-memory window.
MAX_EVIDENCE_RECORDS = 64
# A value shorter than counter(4) + status(1) cannot contain the minimum
# fields this evidence needs and must fail closed rather than be guessed at.
MIN_VALUE_LENGTH = 9
# A commissioning telegram's payload is 30 bytes and carries the device's
# 16-byte security secret; any value approaching that length is treated as
# a possible commissioning telegram and aborts the entire window.
ABORT_VALUE_LENGTH = 24

EvidenceTimerCancel = Callable[[], None]
EvidenceScheduler = Callable[[float, Callable[[], None]], EvidenceTimerCancel]
EvidenceStateListener = Callable[[], None]

_MANUFACTURER_ID_ECHO = (b"\xda\x03", b"\x03\xda")


class EvidenceState(Enum):
    """Lifecycle states for the bounded evidence-capture window."""

    INERT = "inert"
    COLLECTING = "collecting"
    COMPLETE = "complete"
    NO_DATA = "no_data"
    ABORTED = "aborted"


@dataclass(frozen=True)
class _CallbackRecord:
    """Structural facts for one accepted callback; no raw bytes retained."""

    value_length: int
    prefix_detected: bool
    le_delta: int | None
    be_delta: int | None
    status_xor_first: int
    identical_to_previous: bool


@dataclass
class EvidenceSummary:
    """Structural summary exposed by the diagnostic sensor, never raw bytes."""

    callbacks_accepted: int
    manufacturer_data_keys: list[int]
    value_lengths: list[int]
    prefix_detected_consistent: bool | str
    le_deltas: list[int]
    be_deltas: list[int]
    counter_monotonic_le: bool
    counter_monotonic_be: bool
    status_xor_values: list[int]
    duplicate_identical_count: int
    any_connectable_seen: bool


def _has_manufacturer_id_echo(value: bytes) -> bool:
    """Heuristically detect an un-stripped AD Length/Type/Manufacturer-ID echo."""
    return value[1] == 0xFF and value[2:4] in _MANUFACTURER_ID_ECHO


@dataclass
class EvidenceCollector:
    """Manually started, bounded, single-window structural evidence capture.

    Fed every matching Bluetooth callback via :meth:`record_callback`, but
    only inspects it while ``state`` is ``COLLECTING`` and the callback's
    identifier equals ``designated_identifier``. Inactive (``INERT``) by
    default; a single 90-second window is armed only by :meth:`start`.
    """

    designated_identifier: str = field(repr=False)
    state: EvidenceState = EvidenceState.INERT
    state_listener: EvidenceStateListener | None = field(default=None, repr=False)
    _records: list[_CallbackRecord] = field(default_factory=list, repr=False)
    _manufacturer_data_keys: set[int] = field(default_factory=set, repr=False)
    _any_connectable_seen: bool = False
    _previous_value: bytes | None = field(default=None, repr=False)
    _first_status_byte: int | None = field(default=None, repr=False)
    _previous_counter_le: int | None = field(default=None, repr=False)
    _previous_counter_be: int | None = field(default=None, repr=False)
    _timer: EvidenceTimerCancel | None = field(default=None, repr=False)
    _scheduler: EvidenceScheduler | None = field(default=None, repr=False)

    @property
    def callbacks_accepted(self) -> int:
        """Return the live accepted-callback count, valid in every state."""
        return len(self._records)

    @property
    def summary(self) -> EvidenceSummary | None:
        """Return the structural summary after a non-aborted terminal state.

        Returns ``None`` while collecting, while inert, and after ``aborted``
        — an aborted window retains and exposes nothing but its state.
        """
        if self.state not in (EvidenceState.COMPLETE, EvidenceState.NO_DATA):
            return None

        prefix_flags = {record.prefix_detected for record in self._records}
        prefix_detected_consistent: bool | str
        if len(prefix_flags) <= 1:
            prefix_detected_consistent = next(iter(prefix_flags), True)
        else:
            prefix_detected_consistent = "mixed"

        le_deltas = [r.le_delta for r in self._records if r.le_delta is not None]
        be_deltas = [r.be_delta for r in self._records if r.be_delta is not None]

        return EvidenceSummary(
            callbacks_accepted=len(self._records),
            manufacturer_data_keys=sorted(self._manufacturer_data_keys),
            value_lengths=sorted({r.value_length for r in self._records}),
            prefix_detected_consistent=prefix_detected_consistent,
            le_deltas=le_deltas,
            be_deltas=be_deltas,
            counter_monotonic_le=all(delta >= 0 for delta in le_deltas),
            counter_monotonic_be=all(delta >= 0 for delta in be_deltas),
            status_xor_values=[r.status_xor_first for r in self._records],
            duplicate_identical_count=sum(
                1 for r in self._records if r.identical_to_previous
            ),
            any_connectable_seen=self._any_connectable_seen,
        )

    def start(self, schedule: EvidenceScheduler) -> None:
        """Arm the single bounded 90-second collecting window."""
        self._cancel_timer()
        self._reset_transient_state()
        self._scheduler = schedule
        self._timer = schedule(EVIDENCE_CAPTURE_SECONDS, self._finish_window)
        self._set_state(EvidenceState.COLLECTING)

    def cancel(self) -> None:
        """Cancel the window and discard all evidence state; return to inert."""
        self._cancel_timer()
        self._reset_transient_state()
        self._scheduler = None
        self._set_state(EvidenceState.INERT)

    def record_callback(
        self,
        identifier: str,
        manufacturer_data: dict[int, bytes],
        connectable: bool,
    ) -> None:
        """Inspect one callback's structure; ignored outside the active window.

        Never retains or exposes the raw ``manufacturer_data`` value, the
        address it was derived from, or an absolute counter/status value.
        """
        if self.state is not EvidenceState.COLLECTING:
            return
        if identifier != self.designated_identifier:
            return

        self._manufacturer_data_keys.update(manufacturer_data.keys())
        if connectable:
            self._any_connectable_seen = True

        value = manufacturer_data.get(ENOCEAN_MANUFACTURER_ID)
        if value is None:
            return

        length = len(value)
        if length >= ABORT_VALUE_LENGTH or length < MIN_VALUE_LENGTH:
            self._abort()
            return

        offset = 4 if _has_manufacturer_id_echo(value) else 0
        counter_bytes = value[offset : offset + 4]
        status_byte = value[offset + 4]

        counter_le = int.from_bytes(counter_bytes, "little")
        counter_be = int.from_bytes(counter_bytes, "big")

        if self._first_status_byte is None:
            self._first_status_byte = status_byte
        status_xor_first = status_byte ^ self._first_status_byte

        identical_to_previous = (
            self._previous_value is not None and value == self._previous_value
        )
        le_delta = (
            None
            if self._previous_counter_le is None
            else counter_le - self._previous_counter_le
        )
        be_delta = (
            None
            if self._previous_counter_be is None
            else counter_be - self._previous_counter_be
        )

        self._records.append(
            _CallbackRecord(
                value_length=length,
                prefix_detected=offset == 4,
                le_delta=le_delta,
                be_delta=be_delta,
                status_xor_first=status_xor_first,
                identical_to_previous=identical_to_previous,
            )
        )
        self._previous_value = value
        self._previous_counter_le = counter_le
        self._previous_counter_be = counter_be

        if len(self._records) >= MAX_EVIDENCE_RECORDS:
            self._complete_early()

    def _complete_early(self) -> None:
        """Cap reached: stop accepting and end the window early as complete."""
        self._cancel_timer()
        self._scheduler = None
        self._discard_raw_material()
        self._set_state(EvidenceState.COMPLETE)

    def _finish_window(self) -> None:
        """Timer fired: end the window as complete or no_data."""
        self._timer = None
        self._scheduler = None
        self._discard_raw_material()
        self._set_state(
            EvidenceState.COMPLETE if self._records else EvidenceState.NO_DATA
        )

    def _abort(self) -> None:
        """Fail closed: discard everything and enter the aborted terminal state."""
        self._cancel_timer()
        self._scheduler = None
        self._reset_transient_state()
        self._set_state(EvidenceState.ABORTED)

    def _reset_transient_state(self) -> None:
        """Clear every retained record and transient raw-material field."""
        self._records = []
        self._manufacturer_data_keys = set()
        self._any_connectable_seen = False
        self._discard_raw_material()

    def _discard_raw_material(self) -> None:
        """Overwrite the transient raw value/counter scalars used per-callback."""
        self._previous_value = None
        self._first_status_byte = None
        self._previous_counter_le = None
        self._previous_counter_be = None

    def _cancel_timer(self) -> None:
        timer = self._timer
        self._timer = None
        if timer is not None:
            timer()

    def _set_state(self, state: EvidenceState) -> None:
        """Transition state and notify the listener on every transition.

        Covers every path that changes ``state``: start, cancel, abort,
        the early cap-reached completion, and the timer-driven window end.
        """
        self.state = state
        if self.state_listener is not None:
            self.state_listener()
