"""Passive EnOcean PTM 216B Bluetooth advertisement observer."""

from __future__ import annotations

import logging

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .const import ENOCEAN_MANUFACTURER_ID
from .runtime_data import Ptm216bRuntimeData

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Register a passive callback; this integration never connects to BLE devices."""
    entry.runtime_data = Ptm216bRuntimeData()
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])

    @callback
    def _handle_advertisement(
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        """Observe matching advertisements without decoding or emitting actions yet."""
        entry.runtime_data.advertisement_count += 1
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
    """Unload the passive callback and diagnostic sensor platform."""
    return await hass.config_entries.async_unload_platforms(entry, ["sensor"])
