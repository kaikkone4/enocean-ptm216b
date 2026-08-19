"""Privacy assertions for telegram.py, crypto.py, and replay_guard.py.

In the style of test_privacy.py and test_evidence_privacy.py: constructs
objects and raised exceptions using distinctive, synthetic-but-plausible
key/MIC/address byte markers, then asserts those markers never appear in
`repr()` or `str()` output. All material in this file is synthetic test
data, generated independently of any physical device.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from custom_components.enocean_ptm216b.crypto import KeyLengthError, verify_telegram_mic
from custom_components.enocean_ptm216b.replay_guard import evaluate_sequence_counter
from custom_components.enocean_ptm216b.telegram import (
    Ptm216bTelegram,
    StatusParseError,
    TelegramParseError,
    interpret_switch_status,
    parse_data_telegram,
)

from ccm_reference import ccm_encrypt_and_tag

KEY_MARKER = b"private-secret-marker"  # distinctive marker bytes, never a real key
SYNTHETIC_KEY = (KEY_MARKER * 2)[:16]
MIC_MARKER = b"mic-marker-bytes"[:4]
ADDRESS_MARKER = "AA:BB:CC:DD:EE:FF"


def test_telegram_repr_never_leaks_mic_or_authenticated_body_bytes():
    counter_bytes = (7).to_bytes(4, "little")
    status_byte = bytes([0b00010])
    telegram = Ptm216bTelegram(
        sequence_counter=7,
        switch_status=0b00010,
        mic=MIC_MARKER,
        authenticated_body=counter_bytes + status_byte,
    )

    serialized = repr(telegram)

    assert MIC_MARKER.hex() not in serialized
    assert repr(MIC_MARKER) not in serialized
    assert (counter_bytes + status_byte).hex() not in serialized
    assert repr(counter_bytes + status_byte) not in serialized


def test_parse_rejection_exception_never_leaks_the_rejected_bytes():
    marker_value = KEY_MARKER + KEY_MARKER  # an unsupported, marker-bearing length

    with pytest.raises(TelegramParseError) as excinfo:
        parse_data_telegram(marker_value)

    serialized = repr(excinfo.value) + str(excinfo.value)
    assert marker_value.hex() not in serialized
    assert repr(marker_value) not in serialized
    assert KEY_MARKER.decode("latin-1") not in serialized


def test_status_rejection_exception_never_leaks_the_rejected_byte():
    with pytest.raises(StatusParseError) as excinfo:
        interpret_switch_status(0xFF)

    serialized = repr(excinfo.value) + str(excinfo.value)
    assert "0xff" not in serialized.lower()


def test_key_length_error_never_leaks_key_bytes():
    bad_key = KEY_MARKER  # 22 bytes: wrong length, and a distinctive marker
    counter_bytes = (1).to_bytes(4, "little")
    telegram = Ptm216bTelegram(
        sequence_counter=1,
        switch_status=0b00010,
        mic=MIC_MARKER,
        authenticated_body=counter_bytes + bytes([0b00010]),
    )

    with pytest.raises(KeyLengthError) as excinfo:
        verify_telegram_mic(bad_key, ADDRESS_MARKER, telegram)

    serialized = repr(excinfo.value) + str(excinfo.value)
    assert bad_key.hex() not in serialized
    assert repr(bad_key) not in serialized
    assert KEY_MARKER.decode("latin-1") not in serialized


def test_verify_telegram_mic_never_raises_or_leaks_bytes_on_failure():
    """A wrong key must fail closed via `False`, never an exception carrying
    key/MIC/address/nonce bytes -- the failure path must be silent about
    byte content per the fail-closed decoder contract.
    """
    counter_bytes = (1).to_bytes(4, "little")
    telegram = Ptm216bTelegram(
        sequence_counter=1,
        switch_status=0b00010,
        mic=MIC_MARKER,
        authenticated_body=counter_bytes + bytes([0b00010]),
    )

    result = verify_telegram_mic(SYNTHETIC_KEY, ADDRESS_MARKER, telegram)

    assert result is False


def test_replay_guard_never_exposes_counters_via_repr_of_its_pure_return():
    """`ReplayOutcome` is a plain Enum; its repr is just the member name, so
    this documents (and locks in) that no counter or identifier value ever
    rides along in the outcome itself.
    """
    outcome = evaluate_sequence_counter(
        "marker-device-identifier-0123456789",
        999_999,
        lambda _identifier: None,
        lambda _identifier, _counter: None,
    )

    assert "999999" not in repr(outcome)
    assert "marker-device-identifier" not in repr(outcome)


def test_valid_mic_case_still_never_leaks_key_or_mic_material_in_repr():
    """Even a successful, valid (key, telegram) pair must not leak the key
    or MIC bytes through any object's repr along the way.
    """
    address_bytes = bytes(reversed(bytes.fromhex(ADDRESS_MARKER.replace(":", ""))))
    counter = 3
    status = 0b00010
    counter_bytes = counter.to_bytes(4, "little")
    nonce = address_bytes + counter_bytes + bytes(3)
    aad = bytes([0x0C, 0xFF, 0xDA, 0x03]) + counter_bytes + bytes([status])
    mic = ccm_encrypt_and_tag(SYNTHETIC_KEY, nonce, b"", aad, tag_length=4)
    telegram = Ptm216bTelegram(
        sequence_counter=counter,
        switch_status=status,
        mic=mic,
        authenticated_body=counter_bytes + bytes([status]),
    )

    assert verify_telegram_mic(SYNTHETIC_KEY, ADDRESS_MARKER, telegram) is True
    serialized = repr(telegram)
    assert mic.hex() not in serialized
    assert SYNTHETIC_KEY.hex() not in serialized


def test_mutated_telegram_via_replace_still_never_leaks_bytes_in_repr():
    counter_bytes = (5).to_bytes(4, "little")
    telegram = Ptm216bTelegram(
        sequence_counter=5,
        switch_status=0b00010,
        mic=MIC_MARKER,
        authenticated_body=counter_bytes + bytes([0b00010]),
    )
    mutated = replace(telegram, mic=b"\xff\xff\xff\xff")

    serialized = repr(mutated)
    assert MIC_MARKER.hex() not in serialized
    assert "ffffffff" not in serialized
