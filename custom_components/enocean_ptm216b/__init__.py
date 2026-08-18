"""Passive EnOcean PTM 216B Bluetooth advertisement observer."""

from __future__ import annotations

import logging
from time import monotonic

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .const import ENOCEAN_MANUFACTURER_ID
from .runtime_data import Ptm216bRuntimeData
from .secret_store import IntegrationSecretStore

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Register a passive callback; this integration never connects to BLE devices."""
    secret = await IntegrationSecretStore(hass).async_get_or_create()
    entry.runtime_data = Ptm216bRuntimeData(_hmac_secret=secret)
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])

    @callback
    def _handle_advertisement(
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        """Observe matching advertisements without decoding or emitting actions yet."""
        entry.runtime_data.advertisement_count += 1
        entry.runtime_data.record_designation_candidate(
            service_info.address, monotonic()
        )
        sensor = getattr(entry.runtime_data, "sensor", None)
        if sensor is not None:
            sensor.async_write_ha_state()
        _LOGGER.debug("Observed matching EnOcean BLE advertisement")

    entry.async_on_unload(
        bluetooth.async_register_callback(
            hass,
            _handle_advertisement,
            {"manufacturer_id": ENOCEAN_MANUFACTURER_ID, "connectable": False},
            bluetooth.BluetoothScanningMode.PASSIVE,
        )
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload callbacks/platforms and discard ephemeral capture state."""
    entry.runtime_data.cancel_designation_capture()
    return await hass.config_entries.async_unload_platforms(entry, ["sensor"])
