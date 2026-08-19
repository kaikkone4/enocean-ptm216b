"""MIC verification tests for crypto.py.

All key/address/counter/status material in this file is synthetic test
data, generated independently of any physical device.

Two independent oracles are used, per docs/decoder-test-preparation.md's
"Keep two independent synthetic oracles" requirement:

- Oracle A: the official RFC 3610 test vectors, run directly through
  `cryptography`'s `AESCCM` at the RFC's own (8-byte) tag length. This
  proves our AESCCM *usage* matches the RFC baseline. It intentionally does
  not go through crypto.py's PTM-specific nonce/AAD framing -- the RFC
  vectors use a different AAD/nonce layout than PTM 216B -- so this is a
  library-correctness check, not a PTM-framing check.
- Oracle B: `tests/ccm_reference.py`, a from-scratch CCM implementation
  composed from raw AES-ECB block encryption (not from `AESCCM`), applied
  to PTM-style framing. Agreement between `crypto.verify_telegram_mic` and
  Oracle B on the same synthetic material is the load-bearing proof that
  crypto.py's nonce/AAD construction is self-consistent and RFC-correct,
  independent of `cryptography`'s own CCM implementation.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESCCM

from custom_components.enocean_ptm216b.crypto import KeyLengthError, verify_telegram_mic
from custom_components.enocean_ptm216b.telegram import Ptm216bTelegram

from ccm_reference import ccm_encrypt_and_tag

SYNTHETIC_KEY = bytes(
    range(16)
)  # 00 01 02 ... 0F -- obviously synthetic, not a device key
SYNTHETIC_ADDRESS = "AA:BB:CC:DD:EE:FF"
_AAD_LENGTH_BYTE = 0x0C
_AAD_TYPE_BYTE = 0xFF
_AAD_MANUFACTURER_ID_BYTES = bytes([0xDA, 0x03])


def _build_telegram(counter: int, status: int, mic: bytes) -> Ptm216bTelegram:
    authenticated_body = counter.to_bytes(4, "little") + bytes([status])
    return Ptm216bTelegram(
        sequence_counter=counter,
        switch_status=status,
        mic=mic,
        authenticated_body=authenticated_body,
    )


def _reference_nonce_and_aad(
    address: str, counter: int, status: int, *, reverse_address: bool = True
) -> tuple[bytes, bytes]:
    """Independently reconstruct the PTM nonce/AAD, deliberately not reusing
    crypto.py's private helpers, so Oracle B is a genuinely separate path.
    """
    display_order = bytes.fromhex(address.replace(":", ""))
    address_bytes = bytes(reversed(display_order)) if reverse_address else display_order
    counter_bytes = counter.to_bytes(4, "little")
    nonce = address_bytes + counter_bytes + bytes(3)
    aad = (
        bytes([_AAD_LENGTH_BYTE, _AAD_TYPE_BYTE])
        + _AAD_MANUFACTURER_ID_BYTES
        + counter_bytes
        + bytes([status])
    )
    return nonce, aad


def _oracle_b_mic(
    key: bytes, address: str, counter: int, status: int, *, reverse_address: bool = True
) -> bytes:
    nonce, aad = _reference_nonce_and_aad(
        address, counter, status, reverse_address=reverse_address
    )
    return ccm_encrypt_and_tag(key, nonce, b"", aad, tag_length=4)


# ---------------------------------------------------------------------------
# Oracle A: official RFC 3610 test vectors (nonzero AAD), independent of PTM
# ---------------------------------------------------------------------------

_RFC3610_KEY = bytes.fromhex("C0C1C2C3C4C5C6C7C8C9CACBCCCDCECF")


@pytest.mark.parametrize(
    "nonce_hex, aad_hex, payload_hex, tag_length, expected_hex",
    [
        # RFC 3610 section 8, packet vector #1 (M=8, L=2).
        (
            "00000003020100A0A1A2A3A4A5",
            "0001020304050607",
            "08090A0B0C0D0E0F101112131415161718191A1B1C1D1E",
            8,
            "588C979A61C663D2F066D0C2C0F989806D5F6B61DAC38417E8D12CFDF926E0",
        ),
        # RFC 3610 section 8, packet vector #2 (M=8, L=2).
        (
            "00000004030201A0A1A2A3A4A5",
            "0001020304050607",
            "08090A0B0C0D0E0F101112131415161718191A1B1C1D1E1F",
            8,
            "72C91A36E135F8CF291CA894085C87E3CC15C439C9E43A3BA091D56E10400916",
        ),
        # RFC 3610 section 8, packet vector #3 (M=8, L=2).
        (
            "00000005040302A0A1A2A3A4A5",
            "0001020304050607",
            "08090A0B0C0D0E0F101112131415161718191A1B1C1D1E1F20",
            8,
            "51B1E5F44A197D1DA46B0F8E2D282AE871E838BB64DA8596574ADAA76FBD9FB0C5",
        ),
    ],
)
def test_rfc3610_official_vectors_match_aesccm_at_documented_tag_length(
    nonce_hex, aad_hex, payload_hex, tag_length, expected_hex
):
    """Confirms `cryptography`'s AESCCM reproduces official RFC 3610 vectors.

    Public standard test vectors from RFC 3610 itself -- not device-derived
    -- hardcoded here per the gate doc's "generic RFC 3610 CCM known-answer
    vectors" requirement.
    """
    aesccm = AESCCM(_RFC3610_KEY, tag_length=tag_length)
    ciphertext = aesccm.encrypt(
        bytes.fromhex(nonce_hex), bytes.fromhex(payload_hex), bytes.fromhex(aad_hex)
    )
    assert ciphertext == bytes.fromhex(expected_hex)


# ---------------------------------------------------------------------------
# Oracle B: independent from-scratch CCM agrees with crypto.py
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "counter, status",
    [(0, 0b00010), (1, 0b00011), (2, 0b10001), (100, 0b01000)],
)
def test_oracle_b_agrees_with_crypto_module_on_valid_tags(counter, status):
    mic = _oracle_b_mic(SYNTHETIC_KEY, SYNTHETIC_ADDRESS, counter, status)
    telegram = _build_telegram(counter, status, mic)

    assert verify_telegram_mic(SYNTHETIC_KEY, SYNTHETIC_ADDRESS, telegram) is True


# ---------------------------------------------------------------------------
# Negative vectors: mutate exactly one thing at a time from a valid triple
# ---------------------------------------------------------------------------

_BASE_COUNTER = 42
_BASE_STATUS = 0b00110
_BASE_MIC = _oracle_b_mic(SYNTHETIC_KEY, SYNTHETIC_ADDRESS, _BASE_COUNTER, _BASE_STATUS)
_BASE_TELEGRAM = _build_telegram(_BASE_COUNTER, _BASE_STATUS, _BASE_MIC)


def test_base_synthetic_triple_verifies_true():
    assert verify_telegram_mic(SYNTHETIC_KEY, SYNTHETIC_ADDRESS, _BASE_TELEGRAM) is True


def test_wrong_key_single_byte_flip_fails_verification():
    wrong_key = bytearray(SYNTHETIC_KEY)
    wrong_key[0] ^= 0x01

    assert (
        verify_telegram_mic(bytes(wrong_key), SYNTHETIC_ADDRESS, _BASE_TELEGRAM)
        is False
    )


def test_address_byte_order_reversal_is_load_bearing():
    """A MIC computed WITHOUT the LE address-byte reversal must not verify.

    Proves the reversal in `crypto._over_air_address_bytes` is load-bearing:
    if it were accidentally dropped from the real code path, real-device
    MICs (which are always over the true over-air little-endian address)
    would stop verifying, exactly like this deliberately "wrong" one does.
    """
    wrong_mic = _oracle_b_mic(
        SYNTHETIC_KEY,
        SYNTHETIC_ADDRESS,
        _BASE_COUNTER,
        _BASE_STATUS,
        reverse_address=False,
    )
    telegram = _build_telegram(_BASE_COUNTER, _BASE_STATUS, wrong_mic)

    assert verify_telegram_mic(SYNTHETIC_KEY, SYNTHETIC_ADDRESS, telegram) is False


def test_wrong_counter_in_authenticated_body_fails_verification():
    mutated = replace(
        _BASE_TELEGRAM,
        authenticated_body=(_BASE_COUNTER + 1).to_bytes(4, "little")
        + bytes([_BASE_STATUS]),
    )

    assert verify_telegram_mic(SYNTHETIC_KEY, SYNTHETIC_ADDRESS, mutated) is False


def test_wrong_status_in_authenticated_body_fails_verification():
    mutated = replace(
        _BASE_TELEGRAM,
        authenticated_body=_BASE_COUNTER.to_bytes(4, "little")
        + bytes([_BASE_STATUS ^ 0x01]),
    )

    assert verify_telegram_mic(SYNTHETIC_KEY, SYNTHETIC_ADDRESS, mutated) is False


@pytest.mark.parametrize("byte_index", [0, 1, 2, 3])
def test_each_mic_byte_flip_independently_fails_verification(byte_index):
    flipped = bytearray(_BASE_MIC)
    flipped[byte_index] ^= 0xFF
    mutated = replace(_BASE_TELEGRAM, mic=bytes(flipped))

    assert verify_telegram_mic(SYNTHETIC_KEY, SYNTHETIC_ADDRESS, mutated) is False


@pytest.mark.parametrize("byte_index", [0, 1, 2, 3])
def test_wrong_aad_prefix_byte_fails_verification(byte_index):
    """Mutate one of Length/Type/Manufacturer-ID(2) and show the resulting
    MIC does not verify against crypto.py's fixed, correct AAD prefix.
    """
    prefix = bytearray([_AAD_LENGTH_BYTE, _AAD_TYPE_BYTE, *_AAD_MANUFACTURER_ID_BYTES])
    prefix[byte_index] ^= 0xFF
    counter_bytes = _BASE_COUNTER.to_bytes(4, "little")
    aad = bytes(prefix) + counter_bytes + bytes([_BASE_STATUS])
    nonce, _ = _reference_nonce_and_aad(SYNTHETIC_ADDRESS, _BASE_COUNTER, _BASE_STATUS)
    wrong_mic = ccm_encrypt_and_tag(SYNTHETIC_KEY, nonce, b"", aad, tag_length=4)
    telegram = _build_telegram(_BASE_COUNTER, _BASE_STATUS, wrong_mic)

    assert verify_telegram_mic(SYNTHETIC_KEY, SYNTHETIC_ADDRESS, telegram) is False


def test_truncated_mic_fails_verification():
    mutated = replace(_BASE_TELEGRAM, mic=_BASE_MIC[:2])

    assert verify_telegram_mic(SYNTHETIC_KEY, SYNTHETIC_ADDRESS, mutated) is False


@pytest.mark.parametrize("bad_key_length", [0, 1, 15, 17, 32])
def test_wrong_key_length_raises_typed_precondition_error(bad_key_length):
    bad_key = bytes(bad_key_length)

    with pytest.raises(KeyLengthError):
        verify_telegram_mic(bad_key, SYNTHETIC_ADDRESS, _BASE_TELEGRAM)
