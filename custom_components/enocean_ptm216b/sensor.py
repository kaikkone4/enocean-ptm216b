"""Diagnostic entities for passive EnOcean PTM 216B observation."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Expose only an advertisement counter during the observation MVP."""
    sensor = Ptm216bAdvertisementCounter(entry)
    entry.runtime_data.sensor = sensor
    async_add_entities([sensor])


class Ptm216bAdvertisementCounter(SensorEntity):
    """Count passively observed EnOcean BLE advertisements without storing payloads."""

    _attr_has_entity_name = True
    _attr_name = "Observed advertisements"
    _attr_icon = "mdi:bluetooth-connect"

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_advertisement_count"

    @property
    def native_value(self) -> int:
        """Return the number of matching advertisements seen since setup."""
        return self._entry.runtime_data.advertisement_count
