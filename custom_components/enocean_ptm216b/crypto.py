"""CCM (RFC 3610) MIC verification for PTM 216B data telegrams.

Implements User Manual sections 5.1-5.1.2: a 128-bit device-specific secret,
a 13-byte nonce built from the six-byte over-air source address and the
four-byte sequence counter (both little-endian), three zero bytes, and a
4-byte (M=4) authentication tag over reconstructed authenticated data (AAD).
No plaintext is ever encrypted or decrypted here -- authentication-only mode
means the CCM "ciphertext" is the bare tag, so this module only verifies,
never exposes decrypted bytes, and never lets a rejection reason leak byte
content. See docs/decoder-test-preparation.md, "Fail-closed decoder
contract", item 3-4.
"""

from __future__ import annotations

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESCCM

from .identity import canonicalize_address
from .telegram import Ptm216bTelegram

_KEY_LENGTH = 16
_TAG_LENGTH = 4
# AD Length byte value for the 9-byte authentication-only telegram shape,
# per User Manual section 4.6.1 (0x0C == 12: the AD-field byte count
# Length/Type/Manufacturer-ID/Counter/Status/MIC would occupy on air, minus
# the Length byte itself). Home Assistant/bleak strips this prefix from the
# delivered value, so it is reconstructed here purely to rebuild the
# authenticated-data input the device actually signed.
_AD_LENGTH_BYTE = 0x0C
_AD_TYPE_BYTE = 0xFF
# EnOcean manufacturer ID 0x03DA, little-endian on-air byte order.
_MANUFACTURER_ID_BYTES = bytes([0xDA, 0x03])
_NONCE_TRAILING_ZERO_BYTES = 3


class KeyLengthError(Exception):
    """Fail-closed precondition failure: the supplied key is not 16 bytes.

    Never includes the key itself -- only its (non-sensitive) length -- so
    this is always safe to log, unlike a MIC verification failure, which
    must never raise at all (see :func:`verify_telegram_mic`).
    """

    def __init__(self, length: int) -> None:
        self.length = length
        super().__init__(f"key must be exactly {_KEY_LENGTH} bytes (got {length})")


def _over_air_address_bytes(source_address: str) -> bytes:
    """Return the 6 over-air (little-endian) address bytes for the nonce.

    :func:`identity.canonicalize_address` returns a 12-hex-digit canonical
    string in DISPLAY order (big-endian, i.e. the usual ``AA:BB:CC:DD:EE:FF``
    reading order). The CCM nonce needs the six-byte source address in
    over-air little-endian order (User Manual section 5.1.2), so this
    function reverses the canonical big-endian bytes.

    This is the ONE remaining fact in this module that only a live key/MIC
    test against a real device can prove -- everything else here (AAD
    framing, nonce trailer, tag length) is directly stated by the manual and
    is exercised against both an official RFC 3610 oracle and an independent
    from-scratch CCM implementation in tests/. If a live test ever falsifies
    the reversal direction, this is the only function that needs to change.
    """
    canonical = canonicalize_address(source_address)
    display_order = bytes.fromhex(canonical)
    return bytes(reversed(display_order))


def _build_nonce(source_address: str, telegram_authenticated_body: bytes) -> bytes:
    """Build the 13-byte CCM nonce: address(6, LE) + counter(4, LE) + zeros(3)."""
    address_bytes = _over_air_address_bytes(source_address)
    # authenticated_body is counter(4) + status(1) exactly as received; the
    # received counter bytes are already little-endian on-air order, so no
    # further reversal is needed here (unlike the address).
    counter_bytes = telegram_authenticated_body[0:4]
    return address_bytes + counter_bytes + bytes(_NONCE_TRAILING_ZERO_BYTES)


def _build_aad(telegram_authenticated_body: bytes) -> bytes:
    """Reconstruct the 9-byte AAD Home Assistant/bleak strips from delivery.

    Length(1) + Type(1) + Manufacturer-ID(2, LE) + Counter(4) + Status(1),
    per User Manual section 5.1.1: Authenticated input covers Length, AD
    Type, Manufacturer ID, Sequence Counter, and Switch Status.
    """
    return (
        bytes([_AD_LENGTH_BYTE, _AD_TYPE_BYTE])
        + _MANUFACTURER_ID_BYTES
        + telegram_authenticated_body
    )


def verify_telegram_mic(
    key: bytes, source_address: str, telegram: Ptm216bTelegram
) -> bool:
    """Verify a telegram's 4-byte MIC; True only on cryptographic success.

    ``key`` must be exactly 16 bytes -- an invalid key length is a distinct,
    non-timing-sensitive precondition failure and raises
    :class:`KeyLengthError` before any comparison happens. Every other
    failure mode (wrong key content, wrong address, wrong counter, wrong
    status, wrong/truncated MIC) funnels indistinguishably to ``return
    False``: no exception, no byte content, ever leaves this function on the
    verification path itself, per docs/decoder-test-preparation.md,
    "Fail-closed decoder contract", item 4 ("AES-CCM errors must be
    indistinguishable in user-visible output").
    """
    if len(key) != _KEY_LENGTH:
        raise KeyLengthError(len(key))

    nonce = _build_nonce(source_address, telegram.authenticated_body)
    aad = _build_aad(telegram.authenticated_body)

    aesccm = AESCCM(key, tag_length=_TAG_LENGTH)
    try:
        # Authentication-only mode: plaintext is empty, so the CCM
        # "ciphertext" for this tag length is exactly the 4-byte MIC.
        # decrypt() raises InvalidTag iff the tag does not authenticate.
        aesccm.decrypt(nonce, telegram.mic, aad)
    except InvalidTag:
        return False
    return True
