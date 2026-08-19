"""Shared commissioning-input parsing: QR/label text, manual fields, photo QR.

Relocated from ``config_flow.py`` (Phase 4's now-removed ``commission_switch``
step) so both that step's spirit and the Phase 5A per-switch subentry wizard's
key-entry step can use the exact same tolerant parsing and precedence rules.
Nothing here is ever echoed back into an error message, an abort reason, or a
redisplayed form's description placeholders -- see ``config_flow.py``'s
module docstring for why.
"""

from __future__ import annotations

import re

from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.core import HomeAssistant

from .identity import canonicalize_address
from .qr_decode import decode_qr_image, is_qr_decode_available

_KEY_HEX_LENGTH = 32
_QR_ADDRESS_RE = re.compile(r"30S([0-9A-Fa-f]{12})", re.IGNORECASE)
_QR_KEY_RE = re.compile(r"Z([0-9A-Fa-f]{" + str(_KEY_HEX_LENGTH) + r"})", re.IGNORECASE)
_KEY_HEX_RE = re.compile(r"^[0-9A-Fa-f]{" + str(_KEY_HEX_LENGTH) + r"}$")

# Label/QR photos are small; this bound only exists to keep a single upload
# from spending unbounded time/memory in the decode executor job, never to
# accommodate any legitimate larger use case.
MAX_QR_IMAGE_BYTES = 5 * 1024 * 1024


def _parse_qr_payload(text: str) -> tuple[str | None, bytes | None]:
    """Tolerantly extract (address, key) from EnOcean label/QR text.

    Looks for the EnOcean label data-identifier tokens ``30S`` (followed by
    12 hex digits: the address) and ``Z`` (followed by 32 hex digits: the
    16-byte security key) anywhere in ``text``, case-insensitively. Tokens
    may be separated by ``+``, whitespace, or nothing at all -- this
    searches for each token independently rather than splitting on a fixed
    separator, so any of those layouts work. Returns ``(None, None)``
    components for whichever token is absent or does not parse; callers
    must not merge a partial QR result with the manual fallback fields (see
    :func:`resolve_commissioning_input`).
    """
    address: str | None = None
    address_match = _QR_ADDRESS_RE.search(text)
    if address_match:
        try:
            address = canonicalize_address(address_match.group(1))
        except ValueError:
            address = None

    key_match = _QR_KEY_RE.search(text)
    key = bytes.fromhex(key_match.group(1)) if key_match else None

    return address, key


def _parse_manual_address(text: str) -> str | None:
    """Accept colon-separated or plain 12-hex manual address entry."""
    text = text.strip()
    if not text:
        return None
    try:
        return canonicalize_address(text)
    except ValueError:
        return None


def _parse_manual_key(text: str) -> bytes | None:
    """Accept a 32-hex-character (case-insensitive) manual security key."""
    text = text.strip()
    if not _KEY_HEX_RE.fullmatch(text):
        return None
    return bytes.fromhex(text)


def resolve_commissioning_input(
    user_input: dict[str, object],
) -> tuple[str | None, bytes | None]:
    """Resolve one (canonical address, 16-byte key) pair from form input.

    Precedence: if ``qr_payload`` parses to BOTH an address and a key, it
    wins outright over the manual ``address``/``security_key`` fields --
    partial results from the two sources are never merged. Only when the QR
    payload is absent, or does not yield both fields, do the manual fields
    apply. Does not consider ``qr_image`` -- see
    :func:`resolve_commissioning_input_with_photo` for the async variant
    that also decodes an uploaded photo, at strictly higher precedence than
    ``qr_payload``.
    """
    qr_payload = str(user_input.get("qr_payload") or "").strip()
    if qr_payload:
        address, key = _parse_qr_payload(qr_payload)
        if address is not None and key is not None:
            return address, key

    address = _parse_manual_address(str(user_input.get("address") or ""))
    key = _parse_manual_key(str(user_input.get("security_key") or ""))
    if address is not None and key is not None:
        return address, key
    return None, None


def _decode_uploaded_qr_image(hass: HomeAssistant, file_id: str) -> str | None:
    """Read and decode one uploaded QR-photo file; must run in an executor.

    Returns ``None`` (never raises) whenever the file is missing, too large,
    or does not decode to any text -- every failure mode here is treated
    identically, exactly like :func:`crypto.verify_telegram_mic` treats every
    MIC failure identically. The temp file and any decoded text are
    transient: nothing here is logged or persisted.
    """
    try:
        with process_uploaded_file(hass, file_id) as path:
            if path.stat().st_size > MAX_QR_IMAGE_BYTES:
                return None
            image_bytes = path.read_bytes()
    except (ValueError, OSError):
        return None
    return decode_qr_image(image_bytes)


async def resolve_commissioning_input_with_photo(
    hass: HomeAssistant, user_input: dict[str, object]
) -> tuple[str | None, bytes | None]:
    """Resolve one (canonical address, 16-byte key) pair, photo QR included.

    Precedence: ``qr_image`` (decoded, only when the optional decode library
    is importable) -> ``qr_payload`` -> manual ``address``/``security_key``
    fields, per :func:`resolve_commissioning_input`. A source that does not
    yield both an address and a key never merges with a lower-precedence
    source's partial result.
    """
    file_id = user_input.get("qr_image")
    if file_id and is_qr_decode_available():
        text = await hass.async_add_executor_job(
            _decode_uploaded_qr_image, hass, str(file_id)
        )
        if text:
            address, key = _parse_qr_payload(text)
            if address is not None and key is not None:
                return address, key

    return resolve_commissioning_input(user_input)
