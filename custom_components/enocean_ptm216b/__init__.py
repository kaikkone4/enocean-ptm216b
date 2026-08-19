"""Passive EnOcean PTM 216B Bluetooth advertisement observer."""

from __future__ import annotations

import logging

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from . import button_pipeline
from .commissioning_store import CommissioningStore
from .const import ENOCEAN_MANUFACTURER_ID
from .runtime_data import Ptm216bRuntimeData
from .secret_store import IntegrationSecretStore

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "event"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Register a passive callback; this integration never connects to BLE devices."""
    secret = await IntegrationSecretStore(hass).async_get_or_create()
    commissioning_store = CommissioningStore(hass)
    await commissioning_store.async_load()
    entry.runtime_data = Ptm216bRuntimeData(
        _hmac_secret=secret, commissioning_store=commissioning_store
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    @callback
    def _handle_advertisement(
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        """Observe matching advertisements; commissioned switches also decode."""
        entry.runtime_data.advertisement_count += 1
        entry.runtime_data.record_advertisement_observation(
            service_info.address,
            service_info.manufacturer_data,
            service_info.connectable,
        )
        sensor = getattr(entry.runtime_data, "sensor", None)
        if sensor is not None:
            sensor.async_write_ha_state()
        _LOGGER.debug("Observed matching EnOcean BLE advertisement")

        # Entirely additive: a no-op for every address that is not currently
        # commissioned. See button_pipeline.py for the fail-closed pipeline
        # this drives for commissioned switches only.
        button_pipeline.handle_advertisement(
            entry.runtime_data,
            service_info.address,
            service_info.manufacturer_data,
            hass.async_create_task,
        )

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
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entry.runtime_data.cancel_designation_capture()
        entry.runtime_data.cancel_evidence_capture()
    return unload_ok
