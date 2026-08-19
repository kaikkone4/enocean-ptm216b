"""Independent from-scratch RFC 3610 CCM implementation, for test use only.

Composed directly from `cryptography`'s raw AES-ECB block-cipher primitive
(`cryptography.hazmat.primitives.ciphers.algorithms.AES` in ECB mode) --
NOT from `cryptography`'s own `AESCCM` class -- so that comparing
`crypto.verify_telegram_mic` (which uses `AESCCM`) against this
implementation is a genuinely independent second oracle, per
docs/decoder-test-preparation.md's "Keep two independent synthetic oracles"
requirement. See tests/test_crypto.py, "Oracle B".

This module makes no claim about the provenance of key/nonce/AAD/plaintext
material passed to it; callers in tests/ are responsible for using only
synthetic, non-device material.
"""

from __future__ import annotations

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

_BLOCK_SIZE = 16


def _encrypt_block(key: bytes, block: bytes) -> bytes:
    """Encrypt exactly one 16-byte block with raw AES-ECB (the CCM primitive)."""
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return encryptor.update(block) + encryptor.finalize()


def _xor_bytes(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


def _pad_block(data: bytes) -> bytes:
    """Zero-pad ``data`` up to the next 16-byte boundary (RFC 3610 section 2.2)."""
    remainder = len(data) % _BLOCK_SIZE
    if remainder == 0:
        return data
    return data + bytes(_BLOCK_SIZE - remainder)


def _l_param(nonce: bytes) -> int:
    """RFC 3610: nonce length is 15 - L, so L = 15 - len(nonce)."""
    return _BLOCK_SIZE - 1 - len(nonce)


def _format_b0(
    nonce: bytes, aad: bytes, plaintext_length: int, tag_length: int
) -> bytes:
    """Format B_0 per RFC 3610 section 2.2: flags || nonce || message length."""
    l_param = _l_param(nonce)
    has_aad_bit = 0x40 if aad else 0x00
    m_field = ((tag_length - 2) // 2) << 3
    flags = has_aad_bit | m_field | (l_param - 1)
    length_field = plaintext_length.to_bytes(l_param, "big")
    return bytes([flags]) + nonce + length_field


def _format_aad_blocks(aad: bytes) -> bytes:
    """Format the AAD length-prefixed, zero-padded block(s) per section 2.2."""
    if not aad:
        return b""
    if len(aad) >= 0xFF00:
        raise ValueError("AAD too long for this reference implementation")
    encoded_length = len(aad).to_bytes(2, "big")
    return _pad_block(encoded_length + aad)


def _cbc_mac(
    key: bytes, nonce: bytes, aad: bytes, plaintext: bytes, tag_length: int
) -> bytes:
    """Compute the raw (unencrypted) CBC-MAC tag, per RFC 3610 section 2.2."""
    blocks = (
        _format_b0(nonce, aad, len(plaintext), tag_length)
        + _format_aad_blocks(aad)
        + _pad_block(plaintext)
    )
    x = bytes(_BLOCK_SIZE)
    for offset in range(0, len(blocks), _BLOCK_SIZE):
        block = blocks[offset : offset + _BLOCK_SIZE]
        x = _encrypt_block(key, _xor_bytes(x, block))
    return x[:tag_length]


def _counter_block(nonce: bytes, counter: int) -> bytes:
    """Format A_i per RFC 3610 section 2.3: flags(L-1 only) || nonce || counter."""
    l_param = _l_param(nonce)
    flags = l_param - 1
    counter_field = counter.to_bytes(l_param, "big")
    return bytes([flags]) + nonce + counter_field


def _ctr_keystream(key: bytes, nonce: bytes, block_count: int) -> bytes:
    """Return ``block_count`` blocks of CTR keystream: S_0, S_1, ... concatenated."""
    return b"".join(
        _encrypt_block(key, _counter_block(nonce, counter))
        for counter in range(block_count)
    )


def ccm_encrypt_and_tag(
    key: bytes, nonce: bytes, plaintext: bytes, aad: bytes, tag_length: int
) -> bytes:
    """Return ciphertext || encrypted tag, per RFC 3610's CCM construction.

    S_0 (counter block 0) encrypts the CBC-MAC tag; S_1, S_2, ... encrypt the
    message via CTR mode. With an empty ``plaintext`` (the PTM 216B
    authentication-only case this repo needs), the result is exactly the
    encrypted tag, matching `cryptography.AESCCM`'s behavior of returning
    only the tag when encrypting empty plaintext.
    """
    mac = _cbc_mac(key, nonce, aad, plaintext, tag_length)
    message_blocks = (
        (len(plaintext) + _BLOCK_SIZE - 1) // _BLOCK_SIZE if plaintext else 0
    )
    keystream = _ctr_keystream(key, nonce, 1 + message_blocks)
    s0, message_keystream = keystream[:_BLOCK_SIZE], keystream[_BLOCK_SIZE:]
    ciphertext = _xor_bytes(plaintext, message_keystream[: len(plaintext)])
    encrypted_tag = _xor_bytes(mac, s0[:tag_length])
    return ciphertext + encrypted_tag


def ccm_decrypt_and_verify(
    key: bytes, nonce: bytes, ciphertext: bytes, aad: bytes, tag_length: int
) -> bytes | None:
    """Inverse of :func:`ccm_encrypt_and_tag`; returns plaintext or ``None``.

    Returns ``None`` (never raises) when the recomputed tag does not match,
    mirroring this repo's fail-closed, non-leaking verification convention.
    """
    body, received_tag = ciphertext[:-tag_length], ciphertext[-tag_length:]
    message_blocks = (len(body) + _BLOCK_SIZE - 1) // _BLOCK_SIZE if body else 0
    keystream = _ctr_keystream(key, nonce, 1 + message_blocks)
    s0, message_keystream = keystream[:_BLOCK_SIZE], keystream[_BLOCK_SIZE:]
    plaintext = _xor_bytes(body, message_keystream[: len(body)])
    expected_mac = _cbc_mac(key, nonce, aad, plaintext, tag_length)
    encrypted_expected_tag = _xor_bytes(expected_mac, s0[:tag_length])
    if encrypted_expected_tag != received_tag:
        return None
    return plaintext
