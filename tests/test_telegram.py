"""Tests for the strict, fail-closed telegram.py parser.

All byte/status/length material in this file is synthetic test data; the
9-byte "value" fixtures below are structurally shaped placeholders, never
anything resembling a real captured telegram.
"""

from __future__ import annotations

import pytest

from custom_components.enocean_ptm216b.telegram import (
    ButtonPattern,
    ParseRejectionReason,
    StatusRejectionReason,
    StatusParseError,
    TelegramParseError,
    interpret_switch_status,
    normalize_button_pattern,
    parse_data_telegram,
)


def _synthetic_value(length: int) -> bytes:
    """A structurally-shaped placeholder value of a given length; never real."""
    return bytes(i % 256 for i in range(length))


# ---------------------------------------------------------------------------
# parse_data_telegram: supported shape
# ---------------------------------------------------------------------------


def test_parses_the_one_supported_nine_byte_shape():
    counter_bytes = (12345).to_bytes(4, "little")
    status_byte = bytes([0b00010])
    mic = bytes([0xAA, 0xBB, 0xCC, 0xDD])
    value = counter_bytes + status_byte + mic

    telegram = parse_data_telegram(value)

    assert telegram.sequence_counter == 12345
    assert telegram.switch_status == 0b00010
    assert telegram.mic == mic
    assert telegram.authenticated_body == counter_bytes + status_byte


# ---------------------------------------------------------------------------
# parse_data_telegram: negative vectors, every unsupported length
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("length", [8, 10, 11, 12, 13])
def test_short_or_optional_data_lengths_are_rejected_as_unsupported(length):
    with pytest.raises(TelegramParseError) as excinfo:
        parse_data_telegram(_synthetic_value(length))

    assert excinfo.value.reason is ParseRejectionReason.UNSUPPORTED_LENGTH
    assert excinfo.value.length == length


@pytest.mark.parametrize("length", [24, 30])
def test_commissioning_length_threshold_is_rejected_as_possible_commissioning(length):
    with pytest.raises(TelegramParseError) as excinfo:
        parse_data_telegram(_synthetic_value(length))

    assert excinfo.value.reason is ParseRejectionReason.POSSIBLE_COMMISSIONING
    assert excinfo.value.length == length


def test_parse_rejection_never_includes_raw_bytes_in_message_or_repr():
    marker_value = _synthetic_value(30)

    with pytest.raises(TelegramParseError) as excinfo:
        parse_data_telegram(marker_value)

    serialized = repr(excinfo.value) + str(excinfo.value)
    assert marker_value.hex() not in serialized
    assert repr(marker_value) not in serialized


# ---------------------------------------------------------------------------
# interpret_switch_status: valid single-button cases, every button, both edges
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status_byte, expected_pattern, expected_is_press",
    [
        (0b00010, ButtonPattern.A0, False),
        (0b00011, ButtonPattern.A0, True),
        (0b00100, ButtonPattern.A1, False),
        (0b00101, ButtonPattern.A1, True),
        (0b01000, ButtonPattern.B0, False),
        (0b01001, ButtonPattern.B0, True),
        (0b10000, ButtonPattern.B1, False),
        (0b10001, ButtonPattern.B1, True),
    ],
)
def test_valid_single_button_status_bytes_decode_correctly(
    status_byte, expected_pattern, expected_is_press
):
    state = interpret_switch_status(status_byte)

    assert state.pattern is expected_pattern
    assert state.is_press is expected_is_press


# ---------------------------------------------------------------------------
# interpret_switch_status: valid combo cases (Phase 5D) -- same-letter
# two-bit combinations, both press and release edges (12 accepted cases
# total across this file, matching the six patterns x two edges the phase
# spec calls for).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status_byte, expected_pattern, expected_is_press",
    [
        (0b01010, ButtonPattern.A0_B0, False),
        (0b01011, ButtonPattern.A0_B0, True),
        (0b10100, ButtonPattern.A1_B1, False),
        (0b10101, ButtonPattern.A1_B1, True),
    ],
)
def test_valid_combo_status_bytes_decode_correctly(
    status_byte, expected_pattern, expected_is_press
):
    state = interpret_switch_status(status_byte)

    assert state.pattern is expected_pattern
    assert state.is_press is expected_is_press


# ---------------------------------------------------------------------------
# interpret_switch_status: negative vectors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status_byte", [0b0100000, 0b1000000, 0xFF])
def test_reserved_bits_are_rejected(status_byte):
    with pytest.raises(StatusParseError) as excinfo:
        interpret_switch_status(status_byte)

    assert excinfo.value.reason is StatusRejectionReason.RESERVED_BITS


@pytest.mark.parametrize("status_byte", [0b00000, 0b00001])
def test_zero_button_bits_are_rejected(status_byte):
    with pytest.raises(StatusParseError) as excinfo:
        interpret_switch_status(status_byte)

    assert excinfo.value.reason is StatusRejectionReason.NO_BUTTON_BIT


@pytest.mark.parametrize(
    "status_byte",
    [
        0b10010,  # diagonal: A0 (0b00010) + B1 (0b10000)
        0b01100,  # diagonal: A1 (0b00100) + B0 (0b01000)
        0b01110,  # 3-bit combo: A0 + A1 + B0
        0b11110,  # 4-bit combo: A0 + A1 + B0 + B1
    ],
)
def test_unsupported_button_combinations_are_rejected(status_byte):
    with pytest.raises(StatusParseError) as excinfo:
        interpret_switch_status(status_byte)

    assert excinfo.value.reason is StatusRejectionReason.UNSUPPORTED_BUTTON_COMBINATION


def test_status_rejection_never_includes_the_raw_byte_in_message_or_repr():
    marker_status = 0xFF

    with pytest.raises(StatusParseError) as excinfo:
        interpret_switch_status(marker_status)

    serialized = repr(excinfo.value) + str(excinfo.value)
    assert hex(marker_status) not in serialized
    assert str(marker_status) not in serialized


# ---------------------------------------------------------------------------
# normalize_button_pattern: full aliasing table for rockers=1, identity for
# rockers=2 (and, matching this repo's "only exactly 1 means single-rocker"
# convention, any other value too).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pattern, expected",
    [
        (ButtonPattern.A0, ButtonPattern.A0),
        (ButtonPattern.B0, ButtonPattern.A0),
        (ButtonPattern.A0_B0, ButtonPattern.A0),
        (ButtonPattern.A1, ButtonPattern.A1),
        (ButtonPattern.B1, ButtonPattern.A1),
        (ButtonPattern.A1_B1, ButtonPattern.A1),
    ],
)
def test_normalize_button_pattern_aliases_everything_for_a_single_rocker_switch(
    pattern, expected
):
    assert normalize_button_pattern(pattern, rockers=1) is expected


@pytest.mark.parametrize("pattern", list(ButtonPattern))
@pytest.mark.parametrize("rockers", [2, 0, 3, 99])
def test_normalize_button_pattern_is_identity_for_non_single_rocker_values(
    pattern, rockers
):
    assert normalize_button_pattern(pattern, rockers=rockers) is pattern
