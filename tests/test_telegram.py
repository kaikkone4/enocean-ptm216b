"""Tests for the strict, fail-closed telegram.py parser.

All byte/status/length material in this file is synthetic test data; the
9-byte "value" fixtures below are structurally shaped placeholders, never
anything resembling a real captured telegram.
"""

from __future__ import annotations

import pytest

from custom_components.enocean_ptm216b.telegram import (
    Button,
    ParseRejectionReason,
    StatusRejectionReason,
    StatusParseError,
    TelegramParseError,
    interpret_switch_status,
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
# interpret_switch_status: valid single-bit cases, every button, both edges
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status_byte, expected_button, expected_is_press",
    [
        (0b00010, Button.A0, False),
        (0b00011, Button.A0, True),
        (0b00100, Button.A1, False),
        (0b00101, Button.A1, True),
        (0b01000, Button.B0, False),
        (0b01001, Button.B0, True),
        (0b10000, Button.B1, False),
        (0b10001, Button.B1, True),
    ],
)
def test_valid_single_button_status_bytes_decode_correctly(
    status_byte, expected_button, expected_is_press
):
    state = interpret_switch_status(status_byte)

    assert state.button is expected_button
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
    [0b00110, 0b01010, 0b10010, 0b11110],
)
def test_multiple_button_bits_are_rejected(status_byte):
    with pytest.raises(StatusParseError) as excinfo:
        interpret_switch_status(status_byte)

    assert excinfo.value.reason is StatusRejectionReason.MULTIPLE_BUTTON_BITS


def test_status_rejection_never_includes_the_raw_byte_in_message_or_repr():
    marker_status = 0xFF

    with pytest.raises(StatusParseError) as excinfo:
        interpret_switch_status(marker_status)

    serialized = repr(excinfo.value) + str(excinfo.value)
    assert hex(marker_status) not in serialized
    assert str(marker_status) not in serialized
