"""Button press/release event entities for commissioned PTM 216B switches.

One :class:`~homeassistant.components.event.EventEntity` per rocker button
exists per commissioned switch -- A0/A1 always, B0/B1 only when that
switch's "switch" subentry declares ``rockers == 2`` (see
``config_flow.py``'s Add-device wizard and ``__init__.py``'s migration,
which backfills ``rockers == 2`` for every pre-Phase-5A switch). Entities
are created at setup for every commissioned switch's subentry (so they
survive a Home Assistant restart) and again after any Add-device-wizard or
subentry-removal reload. Each entity is tied to its subentry via
``config_subentry_id`` on :func:`~homeassistant.helpers.entity_platform.
AddEntitiesCallback.__call__` -- NOT via ``DeviceInfo`` -- so removing the
subentry cleanly removes its device and entities.

Each entity fires only when ``button_pipeline.py``'s fail-closed pipeline
decodes a MIC-verified, counter-accepted telegram naming this exact button;
see that module's docstring for the full gate order and the first-trust
policy that decides when NO event fires at all. A verified B0/B1 telegram
on a ``rockers == 1`` switch still counts as verified (see
``runtime_data.CommissionedSwitchRuntime.record_verified_and_fire``) -- it
just fires no event, because no listener is ever registered for that
button here.
"""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .telegram import Button

_EVENT_TYPES = ["press", "release"]
_SINGLE_ROCKER_BUTTONS = (Button.A0, Button.A1)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create each commissioned switch's button event entities, per subentry."""
    store = entry.runtime_data.commissioning_store
    if store is None:
        return

    handles_to_addresses = {
        entry.runtime_data.commissioned_device_handle(address): address
        for address in store.switches
    }

    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != "switch":
            continue
        canonical_address = handles_to_addresses.get(subentry.data.get("handle"))
        switch = store.switches.get(canonical_address) if canonical_address else None
        if canonical_address is None or switch is None:
            continue
        buttons = (
            _SINGLE_ROCKER_BUTTONS
            if subentry.data.get("rockers") == 1
            else tuple(Button)
        )
        entities = [
            Ptm216bButtonEventEntity(entry, canonical_address, switch.name, button)
            for button in buttons
        ]
        async_add_entities(entities, config_subentry_id=subentry_id)


class Ptm216bButtonEventEntity(EventEntity):
    """One rocker button's press/release event, for one commissioned switch.

    ``is_press`` -> ``"press"`` and ``not is_press`` -> ``"release"`` follow
    the manual-sourced polarity documented on
    :class:`telegram.Ptm216bButtonState` -- NOT yet live-proven against a
    real device; see docs/evidence-findings.md, "Button bit mapping --
    absolute bit0 polarity". If live testing ever shows the opposite, only
    this mapping needs to change.

    :meth:`homeassistant.components.event.EventEntity._trigger_event` does
    NOT call ``async_write_ha_state()`` itself, so
    :meth:`_handle_button_event` calls it explicitly right after triggering
    -- otherwise the state/event would never actually become observable.
    """

    _attr_has_entity_name = True
    _attr_event_types = _EVENT_TYPES

    def __init__(
        self, entry: ConfigEntry, canonical_address: str, name: str, button: Button
    ) -> None:
        self._entry = entry
        self._canonical_address = canonical_address
        self._button = button
        handle = entry.runtime_data.commissioned_device_handle(canonical_address)
        self._attr_unique_id = f"{entry.entry_id}_{handle}_{button.value}"
        self._attr_name = button.value
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, handle)},
            name=name,
            manufacturer="EnOcean",
            model="PTM 216B",
        )

    async def async_added_to_hass(self) -> None:
        """Register this button's listener with the switch's runtime record."""
        await super().async_added_to_hass()
        switch_runtime = self._entry.runtime_data.commissioned_switch_runtime(
            self._canonical_address
        )
        switch_runtime.set_event_listener(self._button, self._handle_button_event)

    async def async_will_remove_from_hass(self) -> None:
        """Unregister without leaving a stale listener on the runtime record."""
        switch_runtime = self._entry.runtime_data.commissioned_switch_runtime(
            self._canonical_address
        )
        switch_runtime.set_event_listener(self._button, None)
        await super().async_will_remove_from_hass()

    def _handle_button_event(self, is_press: bool) -> None:
        """Trigger this button's event and immediately write the new state."""
        self._trigger_event("press" if is_press else "release")
        self.async_write_ha_state()
