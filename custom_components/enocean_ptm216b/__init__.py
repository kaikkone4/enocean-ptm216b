"""Passive EnOcean PTM 216B Bluetooth advertisement observer."""

from __future__ import annotations

import logging

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers.event import async_call_later

from .const import (
    DOMAIN,
    ENOCEAN_MANUFACTURER_ID,
    SERVICE_START_DESIGNATION_CAPTURE,
)
from .runtime_data import Ptm216bRuntimeData
from .secret_store import IntegrationSecretStore

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Register a passive callback; this integration never connects to BLE devices."""
    secret = await IntegrationSecretStore(hass).async_get_or_create()
    entry.runtime_data = Ptm216bRuntimeData(_hmac_secret=secret)
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])

    @callback
    def _start_designation_capture(_call: ServiceCall) -> None:
        """Start capture only after this explicit service invocation."""
        entry.runtime_data.start_designation_capture(
            lambda delay, finish: async_call_later(hass, delay, lambda _now: finish())
        )

    hass.services.async_register(
        DOMAIN, SERVICE_START_DESIGNATION_CAPTURE, _start_designation_capture
    )

    @callback
    def _handle_advertisement(
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        """Observe matching advertisements without decoding or emitting actions yet."""
        entry.runtime_data.advertisement_count += 1
        entry.runtime_data.record_designation_candidate(service_info.address)
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
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["sensor"])
    if unload_ok:
        entry.runtime_data.cancel_designation_capture()
        hass.services.async_remove(DOMAIN, SERVICE_START_DESIGNATION_CAPTURE)
    return unload_ok
