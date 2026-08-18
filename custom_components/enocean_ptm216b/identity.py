"""Local pseudonymous BLE device handles.

Addresses enter these helpers only transiently. Callers must not persist or log
canonical addresses or the returned full identifiers outside protected local state.
"""

from __future__ import annotations

import hashlib
import hmac
import re

_ADDRESS_HEX_RE = re.compile(r"^[0-9A-F]{12}$")


def canonicalize_address(address: str) -> str:
    """Return a normalized BLE address or fail closed for unsupported input."""
    compact = address.replace(":", "").upper()
    if not _ADDRESS_HEX_RE.fullmatch(compact):
        raise ValueError("unsupported BLE address format")
    return compact


def device_identifier(secret: bytes, address: str) -> str:
    """Return a keyed, local-only pseudonymous identifier for an address."""
    return hmac.new(
        secret, canonicalize_address(address).encode("ascii"), hashlib.sha256
    ).hexdigest()


def device_handle(identifier: str) -> str:
    """Return a short display handle without exposing the BLE address."""
    return f"test-{identifier[:16]}"
