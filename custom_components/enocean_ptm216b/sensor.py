"""Diagnostic entities for passive EnOcean PTM 216B observation."""

from __future__ import annotations

from dataclasses import asdict

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .evidence_capture import EvidenceState


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Expose the observation-MVP sensors plus one pair per commissioned switch."""
    advertisement_sensor = Ptm216bAdvertisementCounter(entry)
    capture_sensor = Ptm216bDesignationCaptureSensor(entry)
    evidence_sensor = Ptm216bEvidenceCaptureSensor(entry)
    entry.runtime_data.sensor = advertisement_sensor
    entities: list[SensorEntity] = [
        advertisement_sensor,
        capture_sensor,
        evidence_sensor,
    ]

    store = entry.runtime_data.commissioning_store
    if store is not None:
        for canonical_address, switch in store.switches.items():
            entities.append(
                Ptm216bVerifiedTelegramsSensor(entry, canonical_address, switch.name)
            )
            entities.append(
                Ptm216bRejectedTelegramsSensor(entry, canonical_address, switch.name)
            )

    async_add_entities(entities)


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
        """Return the current bounded phase or inert state."""
        return self._entry.runtime_data.capture_state.value

    @property
    def extra_state_attributes(self) -> dict[str, int | str]:
        """Return aggregate count and a generic result, never identity data."""
        runtime = self._entry.runtime_data
        return {
            "observation_count": runtime.capture_observation_count,
            "designation_outcome": runtime.designation_outcome.value,
        }


class Ptm216bEvidenceCaptureSensor(SensorEntity):
    """Expose only structural telegram evidence, never raw bytes or identifiers.

    See docs/decoder-test-preparation.md, "Exact evidence required before
    parser code", for the structural facts this entity may expose. It never
    exposes an address, raw payload byte, absolute counter, absolute switch
    status, or full identifier.
    """

    _attr_has_entity_name = True
    _attr_name = "Evidence capture"
    _attr_icon = "mdi:magnify-scan"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_evidence_capture"

    async def async_added_to_hass(self) -> None:
        """Subscribe to aggregate runtime changes after the entity is ready."""
        await super().async_added_to_hass()
        self._entry.runtime_data.evidence_state_listener = self.async_write_ha_state

    async def async_will_remove_from_hass(self) -> None:
        """Remove the runtime listener without retaining the entity."""
        self._entry.runtime_data.evidence_state_listener = None
        await super().async_will_remove_from_hass()

    @property
    def native_value(self) -> str:
        """Return the current bounded evidence-capture state, or inert."""
        collector = self._entry.runtime_data.evidence_collector
        if collector is None:
            return EvidenceState.INERT.value
        return collector.state.value

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Return only the live count while collecting, or the full summary."""
        collector = self._entry.runtime_data.evidence_collector
        if collector is None:
            return {}
        if collector.state is EvidenceState.COLLECTING:
            return {"callbacks_accepted": collector.callbacks_accepted}
        if collector.state is EvidenceState.COMPLETE:
            summary = collector.summary
            return asdict(summary) if summary is not None else {}
        return {}


class _Ptm216bCommissionedSwitchDiagnosticSensor(SensorEntity):
    """Shared device-registry wiring for one commissioned switch's diagnostics.

    Subclasses only need to set ``_name_suffix``/``_unique_suffix`` and read
    the counter they expose from ``runtime_data.CommissionedSwitchRuntime``
    -- see :meth:`runtime_data.CommissionedSwitchRuntime.record_verified_and_fire`
    and ``.record_rejected`` for the exact rule deciding what increments each
    one, documented once, in that one place.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _unique_suffix: str

    def __init__(self, entry: ConfigEntry, canonical_address: str, name: str) -> None:
        self._entry = entry
        self._canonical_address = canonical_address
        handle = entry.runtime_data.commissioned_device_handle(canonical_address)
        self._attr_unique_id = f"{entry.entry_id}_{handle}_{self._unique_suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, handle)},
            name=name,
            manufacturer="EnOcean",
            model="PTM 216B",
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to this switch's runtime counters after the entity is ready."""
        await super().async_added_to_hass()
        switch_runtime = self._entry.runtime_data.commissioned_switch_runtime(
            self._canonical_address
        )
        switch_runtime.add_diagnostics_listener(self.async_write_ha_state)

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe without retaining a stale listener on the runtime record."""
        switch_runtime = self._entry.runtime_data.commissioned_switch_runtime(
            self._canonical_address
        )
        switch_runtime.remove_diagnostics_listener(self.async_write_ha_state)
        await super().async_will_remove_from_hass()


class Ptm216bVerifiedTelegramsSensor(_Ptm216bCommissionedSwitchDiagnosticSensor):
    """Count telegrams that passed shape+MIC+counter+status and fired an event.

    Does NOT include first-trust counter initialization (which fires no
    event) or a status-decode rejection on an already-accepted counter --
    see ``runtime_data.CommissionedSwitchRuntime.record_verified_and_fire``.
    """

    _attr_name = "Verified telegrams"
    _attr_icon = "mdi:check-decagram-outline"
    _unique_suffix = "verified"

    @property
    def native_value(self) -> int:
        """Return the live verified-telegram count since setup."""
        return self._entry.runtime_data.commissioned_switch_runtime(
            self._canonical_address
        ).verified_count


class Ptm216bRejectedTelegramsSensor(_Ptm216bCommissionedSwitchDiagnosticSensor):
    """Aggregate count of every rejected telegram for one commissioned switch.

    Includes parse-shape rejects, MIC-verification failures, duplicate and
    replay-rejected counters, and status-decode failures -- see
    ``runtime_data.CommissionedSwitchRuntime.record_rejected``. Never exposes
    a reason, byte, address, or counter -- only this aggregate integer.
    """

    _attr_name = "Rejected telegrams"
    _attr_icon = "mdi:close-octagon-outline"
    _unique_suffix = "rejected"

    @property
    def native_value(self) -> int:
        """Return the live rejected-telegram count since setup."""
        return self._entry.runtime_data.commissioned_switch_runtime(
            self._canonical_address
        ).rejected_count
