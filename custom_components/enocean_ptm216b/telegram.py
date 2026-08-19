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

As of Phase 5D, this module also accepts and normalizes the six real
switch-status patterns a PTM 216B can report, not just a single button bit.
A module with a single full-width rocker plate (1-rocker switches, see
``config_flow.py``'s Add-device wizard "Number of rocker buttons" field)
physically actuates BOTH channels on one press -- one energy bow drives one
telegram that carries A0+B0 or A1+B1 simultaneously. A 2-rocker switch can
also produce a genuine, distinct simultaneous-press-of-both-rockers
combination. See :class:`ButtonPattern` for the six accepted patterns and
:func:`normalize_button_pattern` for how a 1-rocker switch's three
per-logical-button raw patterns collapse into one alias, silently, before
anything downstream (``press_timing.py``, ``event.py``,
``device_trigger.py``) ever sees a pattern.
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
    # Phase 5D: replaces the old "MULTIPLE_BUTTON_BITS" reason, which
    # rejected every multi-bit status byte outright. Two multi-bit
    # combinations are now accepted (see _BUTTON_BIT_PATTERNS below); this
    # reason now covers only what remains unsupported after that: the two
    # diagonal two-bit combinations (A0+B1, A1+B0 -- physically impossible
    # on a real rocker plate, since the two halves of one energy bow always
    # actuate the SAME letter's pair, never opposite letters), any 3-bit
    # combo, and the all-4-bits combo.
    UNSUPPORTED_BUTTON_COMBINATION = "unsupported_button_combination"


class StatusParseError(Exception):
    """Fail-closed rejection of a candidate switch-status byte.

    Never carries the rejected byte itself -- only the typed reason -- so it
    is always safe to log or surface in a diagnostic without leaking status
    material the gate doc treats as sensitive-by-convention.
    """

    def __init__(self, reason: StatusRejectionReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


class ButtonPattern(Enum):
    """The six real switch-status patterns a PTM 216B can report.

    Four are the familiar single-button patterns (A0, A1, B0, B1). The
    other two -- ``A0_B0`` and ``A1_B1`` -- are genuine, distinct,
    bindable patterns, not decode errors:

    * On a 1-rocker switch (a single full-width rocker plate), pressing
      the plate always actuates BOTH channels of the SAME letter at once
      (one energy bow, one telegram carrying A0+B0 or A1+B1) --
      :func:`normalize_button_pattern` is what makes this transparent to
      the rest of the integration.
    * On a 2-rocker switch, a genuinely simultaneous press of both
      rockers -- both energy bows released in the same telegram -- is a
      real, distinct action a user can deliberately bind an automation
      to; see ``event.py`` and ``device_trigger.py``, which expose it as
      its own event entity / device-trigger subtype (``"A0+B0"`` /
      ``"A1+B1"``) alongside the four single-button ones.

    The ``.value`` strings for A0/A1/B0/B1 are unchanged from the old
    ``Button`` enum this type replaces, so every already-registered
    single-button entity's ``unique_id`` (``f"{entry_id}_{handle}_
    {pattern.value}"`` in ``event.py``) stays byte-for-byte stable across
    this upgrade -- see ``event.py``'s own unique_id-stability test.
    """

    A0 = "A0"
    A1 = "A1"
    B0 = "B0"
    B1 = "B1"
    A0_B0 = "A0+B0"
    A1_B1 = "A1+B1"


# Maps every accepted button-bit combination (bits 1-4 of the switch-status
# byte, per User Manual Figure 16) to its ButtonPattern. Any nonzero,
# non-reserved combination not listed here -- the two "diagonal" two-bit
# combinations (0b10010 = A0+B1, 0b01100 = A1+B0), any 3-bit combo, or the
# 4-bit combo -- is rejected as StatusRejectionReason.UNSUPPORTED_BUTTON_
# COMBINATION: the manual documents each of the four button bits
# independently (Figure 16), but only same-letter combinations correspond
# to a real, single-energy-bow actuation; the diagonals and wider combos
# have no physical rocker-plate action that could produce them and are
# rejected fail-closed rather than guessed at, matching this module's
# convention throughout.
_BUTTON_BIT_PATTERNS: dict[int, ButtonPattern] = {
    0b00010: ButtonPattern.A0,
    0b00100: ButtonPattern.A1,
    0b01000: ButtonPattern.B0,
    0b10000: ButtonPattern.B1,
    0b01010: ButtonPattern.A0_B0,  # A0 (0b00010) | B0 (0b01000)
    0b10100: ButtonPattern.A1_B1,  # A1 (0b00100) | B1 (0b10000)
}

# The three raw patterns a 1-rocker switch's single full-width plate can
# report for its "A" logical button, and the three for its "B" logical
# button -- see normalize_button_pattern's docstring for why B0/B1 alias to
# A0/A1 rather than being their own logical buttons on a 1-rocker switch.
_SINGLE_ROCKER_ALIASES_TO_A0 = frozenset(
    {ButtonPattern.A0, ButtonPattern.B0, ButtonPattern.A0_B0}
)
_SINGLE_ROCKER_ALIASES_TO_A1 = frozenset(
    {ButtonPattern.A1, ButtonPattern.B1, ButtonPattern.A1_B1}
)


def normalize_button_pattern(pattern: ButtonPattern, rockers: int) -> ButtonPattern:
    """Alias a 1-rocker switch's three raw press-side patterns to one logical button.

    Pure and Home-Assistant-agnostic, like the rest of this module (see
    ``press_timing.py``'s own module docstring for why this repo's pipeline
    modules stay pure) -- directly unit-testable with no HA imports and no
    side effects.

    A 1-rocker switch (``rockers == 1``) has a single full-width rocker
    plate: whichever half a user presses, the module's one energy bow
    always drives BOTH of that half's channels in the same telegram, so
    the raw switch-status byte can decode to any of A0, B0, or A0+B0 for a
    press on the "A" side (and, symmetrically, A1/B1/A1+B1 for the "B"
    side) depending on exactly how the plate seats -- never anything the
    user could distinguish or would want to. This collapses all three into
    the single logical ``A0``/``A1`` pattern silently, before the result
    ever reaches ``press_timing.py``'s state machine or an event entity, so
    it never matters where on the wide plate the user actually pressed.

    For ``rockers == 2`` -- and, matching this repo's existing
    ``subentry.data.get("rockers") == 1`` convention throughout
    ``event.py``/``device_trigger.py`` ("only exactly 1 means single-rocker,
    everything else is the two-rocker default"), for any other value too --
    this is the identity function: a 2-rocker switch's six patterns are all
    distinct, real, bindable actions and none of them are aliased.
    """
    if rockers == 1:
        if pattern in _SINGLE_ROCKER_ALIASES_TO_A0:
            return ButtonPattern.A0
        if pattern in _SINGLE_ROCKER_ALIASES_TO_A1:
            return ButtonPattern.A1
    return pattern


@dataclass(frozen=True)
class Ptm216bButtonState:
    """Decoded switch-status fields; never persisted or logged as identity.

    ``is_press`` follows the manual's documented polarity (bit0 == 1 means
    press) but that absolute polarity is manual-sourced only, NOT yet
    live-proven against a real device -- see docs/evidence-findings.md. The
    relative bit0 toggle-per-actuation behavior it rests on *is* live-proven
    (Phase 2 evidence: within-button XOR == 0b00001).

    ``pattern`` is the RAW decoded :class:`ButtonPattern` -- one of all six
    accepted values, straight from :func:`interpret_switch_status`. It is
    NOT yet normalized for a 1-rocker switch's aliasing; that normalization
    happens one layer up, in
    ``runtime_data.CommissionedSwitchRuntime.record_verified_and_fire``,
    which is the one place that knows a given switch's configured
    ``rockers`` count. Every caller downstream of that point (
    ``press_timing.py``, ``event.py``) only ever sees an already-normalized
    pattern.
    """

    pattern: ButtonPattern
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

    This function only extracts the raw ``switch_status`` byte; it does not
    interpret it -- see :func:`interpret_switch_status` for the six real
    button patterns (not "exactly one button") that byte can decode to.
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
    """Decode a switch-status byte into its button pattern and press/release flag.

    Per User Manual Figure 16 and Phase 2 evidence (see
    docs/evidence-findings.md): bit0 is the press/release toggle, bit1 = A0,
    bit2 = A1, bit3 = B0, bit4 = B1 -- each button bit documented
    independently, and each of bits 1-4 is meaningful on its own. As of
    Phase 5D this decodes to one of the six :class:`ButtonPattern` values in
    ``_BUTTON_BIT_PATTERNS`` (the four single-button patterns, plus the two
    same-letter combinations A0+B0 and A1+B1 that a single energy bow -- a
    1-rocker switch's full-width plate, or a genuinely simultaneous 2-rocker
    press -- can produce in one telegram). Zero button bits, any reserved
    bit at/above ``_RESERVED_STATUS_BITS_FLOOR``, or any button-bit
    combination not in that table (the diagonals, 3-bit, or 4-bit combos)
    is rejected fail-closed rather than guessed at -- see
    docs/evidence-findings.md's "Phase 5D" section for the manual-sourced
    basis of accepting exactly these six patterns.
    """
    if status_byte >= _RESERVED_STATUS_BITS_FLOOR or status_byte < 0:
        raise StatusParseError(StatusRejectionReason.RESERVED_BITS)

    button_bits = status_byte & _BUTTON_BIT_MASK
    if button_bits == 0:
        raise StatusParseError(StatusRejectionReason.NO_BUTTON_BIT)

    pattern = _BUTTON_BIT_PATTERNS.get(button_bits)
    if pattern is None:
        raise StatusParseError(StatusRejectionReason.UNSUPPORTED_BUTTON_COMBINATION)

    is_press = bool(status_byte & _PRESS_RELEASE_BIT)
    return Ptm216bButtonState(pattern=pattern, is_press=is_press)
