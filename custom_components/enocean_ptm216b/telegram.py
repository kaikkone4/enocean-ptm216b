"""Strict, fail-closed parsing of PTM 216B data-telegram bytes.

This module only recognizes the one telegram shape that Phase 2 evidence
capture actually observed live: an authentication-only, 9-byte
``manufacturer_data[0x03DA]`` value with no AD Length/Type/Manufacturer-ID
echo (Home Assistant/bleak strips that prefix) and no optional data. See
docs/decoder-test-preparation.md, "Documented facts" and "Unresolved items:
parser remains blocked", for why every other length is rejected rather than
guessed at: optional-data lengths (10/11/13 bytes) and encrypted mode remain
unobserved, and a 24-byte-or-longer value could be a commissioning telegram
carrying the device's 16-byte security secret.

Nothing here decodes a signature, checks a replay counter, or emits a button
action. It only turns a supported-shape byte string into typed fields; MIC
verification lives in :mod:`crypto` and replay/duplicate logic in
:mod:`replay_guard`, both gated on the parse succeeding first.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# Exactly counter(4) + status(1) + MIC(4), matching the only telegram shape
# Phase 2 evidence capture observed live: no AD Length/Type/Manufacturer-ID
# echo, no optional data.
SUPPORTED_VALUE_LENGTH = 9
# Mirrors evidence_capture.ABORT_VALUE_LENGTH: a commissioning telegram's
# payload is 30 bytes and carries the device's 16-byte security secret, so a
# value approaching that length is treated as a possible commissioning
# telegram rather than an unsupported data-telegram length.
POSSIBLE_COMMISSIONING_LENGTH = 24
# Bits 1-4 of the switch-status byte are the button bits (A0/A1/B0/B1); any
# bit at or above this value is reserved/unknown per User Manual Figure 16
# and must be rejected rather than guessed at.
_RESERVED_STATUS_BITS_FLOOR = 0b100000
_BUTTON_BIT_MASK = 0b11110
_PRESS_RELEASE_BIT = 0b1


class ParseRejectionReason(Enum):
    """Typed, non-sensitive reasons a candidate telegram value is rejected.

    Carried on :class:`TelegramParseError` instead of a bare ``ValueError``
    so callers and tests can branch/assert on *why* without ever needing the
    rejected bytes themselves.
    """

    UNSUPPORTED_LENGTH = "unsupported_length"
    POSSIBLE_COMMISSIONING = "possible_commissioning"


class TelegramParseError(Exception):
    """Fail-closed rejection of a candidate telegram value.

    The message and ``repr()`` intentionally carry only the reason and the
    value's length -- length is not sensitive per repo convention (see
    ``evidence_capture.EvidenceSummary.value_lengths``) -- and never the
    rejected bytes themselves, so this exception is always safe to log.
    """

    def __init__(self, reason: ParseRejectionReason, length: int) -> None:
        self.reason = reason
        self.length = length
        super().__init__(f"{reason.value} (length={length})")


class StatusRejectionReason(Enum):
    """Typed, non-sensitive reasons a switch-status byte is rejected."""

    RESERVED_BITS = "reserved_bits"
    NO_BUTTON_BIT = "no_button_bit"
    MULTIPLE_BUTTON_BITS = "multiple_button_bits"


class StatusParseError(Exception):
    """Fail-closed rejection of a candidate switch-status byte.

    Never carries the rejected byte itself -- only the typed reason -- so it
    is always safe to log or surface in a diagnostic without leaking status
    material the gate doc treats as sensitive-by-convention.
    """

    def __init__(self, reason: StatusRejectionReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


class Button(Enum):
    """The four PTM 216B rocker buttons, per User Manual Figure 16."""

    A0 = "A0"
    A1 = "A1"
    B0 = "B0"
    B1 = "B1"


@dataclass(frozen=True)
class Ptm216bButtonState:
    """Decoded switch-status fields; never persisted or logged as identity.

    ``is_press`` follows the manual's documented polarity (bit0 == 1 means
    press) but that absolute polarity is manual-sourced only, NOT yet
    live-proven against a real device -- see docs/evidence-findings.md. The
    relative bit0 toggle-per-actuation behavior it rests on *is* live-proven
    (Phase 2 evidence: within-button XOR == 0b00001).
    """

    button: Button
    is_press: bool


@dataclass(frozen=True)
class Ptm216bTelegram:
    """Parsed fields of one supported-shape PTM 216B data telegram.

    ``mic`` and ``authenticated_body`` are ``repr=False``: they are exactly
    the bytes a later MIC-verification step needs (see :mod:`crypto`), and
    this repo's convention is that no cryptographic or payload-derived byte
    material appears in a ``repr()``, even a synthetic one in a test.
    """

    sequence_counter: int
    switch_status: int
    mic: bytes = field(repr=False)
    # Sequence-counter bytes (4, as received/little-endian) + switch-status
    # byte (1) exactly as received: the 5 bytes a later MIC-verification step
    # needs to reconstruct the CCM authenticated input (see crypto.py).
    authenticated_body: bytes = field(repr=False)


def parse_data_telegram(value: bytes) -> Ptm216bTelegram:
    """Parse the one supported PTM 216B telegram shape, or reject it, typed.

    Accepts ONLY an exactly-9-byte ``manufacturer_data[0x03DA]`` value:
    sequence counter (4 bytes, little-endian) + switch status (1 byte) +
    security signature/MIC (4 bytes), matching what Phase 2 evidence capture
    observed live with no AD-prefix echo and no optional data. Every other
    length -- including the documented-but-unobserved 10/11/13-byte
    optional-data forms and encrypted-mode framing -- raises
    :class:`TelegramParseError` rather than being guessed at, per
    docs/decoder-test-preparation.md, "Parser implementation entry criteria".
    """
    length = len(value)
    if length >= POSSIBLE_COMMISSIONING_LENGTH:
        raise TelegramParseError(ParseRejectionReason.POSSIBLE_COMMISSIONING, length)
    if length != SUPPORTED_VALUE_LENGTH:
        raise TelegramParseError(ParseRejectionReason.UNSUPPORTED_LENGTH, length)

    counter_bytes = value[0:4]
    status_byte = value[4]
    mic = value[5:9]

    return Ptm216bTelegram(
        sequence_counter=int.from_bytes(counter_bytes, "little"),
        switch_status=status_byte,
        mic=mic,
        authenticated_body=value[0:5],
    )


def interpret_switch_status(status_byte: int) -> Ptm216bButtonState:
    """Decode a switch-status byte into its button and press/release flag.

    Per User Manual Figure 16 and Phase 2 evidence (see
    docs/evidence-findings.md): bit0 is the press/release toggle, bit1 = A0,
    bit2 = A1, bit3 = B0, bit4 = B1. Exactly one of bits 1-4 must be set;
    zero or multiple button bits, or any reserved bit at/above
    ``_RESERVED_STATUS_BITS_FLOOR``, is rejected fail-closed rather than
    guessed at.
    """
    if status_byte >= _RESERVED_STATUS_BITS_FLOOR or status_byte < 0:
        raise StatusParseError(StatusRejectionReason.RESERVED_BITS)

    button_bits = status_byte & _BUTTON_BIT_MASK
    if button_bits == 0:
        raise StatusParseError(StatusRejectionReason.NO_BUTTON_BIT)
    if button_bits & (button_bits - 1) != 0:
        raise StatusParseError(StatusRejectionReason.MULTIPLE_BUTTON_BITS)

    button = {
        0b00010: Button.A0,
        0b00100: Button.A1,
        0b01000: Button.B0,
        0b10000: Button.B1,
    }[button_bits]
    is_press = bool(status_byte & _PRESS_RELEASE_BIT)
    return Ptm216bButtonState(button=button, is_press=is_press)
