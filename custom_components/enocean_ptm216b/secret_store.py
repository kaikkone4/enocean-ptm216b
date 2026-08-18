"""Private integration-local storage for the HMAC secret."""

from __future__ import annotations

import base64
import secrets

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_STORAGE_KEY = "enocean_ptm216b.hmac_secret"
_STORAGE_VERSION = 1
_SECRET_BYTES = 32


class IntegrationSecretStore:
    """Load or create the integration's private 256-bit HMAC secret."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, str]] = Store(
            hass, _STORAGE_VERSION, _STORAGE_KEY, private=True
        )

    async def async_get_or_create(self) -> bytes:
        """Return the persisted secret, creating it once when absent."""
        stored = await self._store.async_load()
        if stored is not None:
            return base64.b64decode(stored["secret"], validate=True)

        secret = secrets.token_bytes(_SECRET_BYTES)
        await self._store.async_save(
            {"secret": base64.b64encode(secret).decode("ascii")}
        )
        return secret
