"""Diagnostic entities for passive EnOcean PTM 216B observation."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Expose only an advertisement counter during the observation MVP."""
    advertisement_sensor = Ptm216bAdvertisementCounter(entry)
    capture_sensor = Ptm216bDesignationCaptureSensor(entry)
    entry.runtime_data.sensor = advertisement_sensor
    async_add_entities([advertisement_sensor, capture_sensor])


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


class Ptm216bDesignationCaptureSensor(SensorEntity):
    """Expose only aggregate manual-capture status and its generic outcome."""

    _attr_has_entity_name = True
    _attr_name = "Designation capture"
    _attr_icon = "mdi:timer-marker-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_designation_capture"

    async def async_added_to_hass(self) -> None:
        """Subscribe to aggregate runtime changes after the entity is ready."""
        await super().async_added_to_hass()
        self._entry.runtime_data.capture_state_listener = self.async_write_ha_state

    async def async_will_remove_from_hass(self) -> None:
        """Remove the runtime listener without retaining the entity."""
        self._entry.runtime_data.capture_state_listener = None
        await super().async_will_remove_from_hass()

    @property
    def native_value(self) -> str:
        """Return only whether capture is active or inert."""
        return self._entry.runtime_data.capture_state.value

    @property
    def extra_state_attributes(self) -> dict[str, int | str]:
        """Return aggregate count and a generic result, never identity data."""
        runtime = self._entry.runtime_data
        return {
            "observation_count": runtime.capture_observation_count,
            "designation_outcome": runtime.designation_outcome.value,
        }
