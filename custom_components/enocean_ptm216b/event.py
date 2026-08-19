"""Button-pattern press/release event entities for commissioned PTM 216B switches.

One :class:`~homeassistant.components.event.EventEntity` per
:class:`~telegram.ButtonPattern` exists per commissioned switch. For a
two-rocker switch (``rockers == 2``, the default) that is all six patterns
-- A0, A1, B0, B1, and, as of Phase 5D, the two combo patterns A0+B0 and
A1+B1, which fire only when both halves of one rocker genuinely actuate in
the SAME telegram (one energy bow). For a one-rocker switch (``rockers ==
1``, see ``config_flow.py``'s Add-device wizard and ``__init__.py``'s
migration, which backfills ``rockers == 2`` for every pre-Phase-5A switch)
only two entities exist, A0 and A1 -- every one of that switch's six raw
patterns is silently aliased down to one of those two logical buttons by
``telegram.normalize_button_pattern`` before it ever reaches this module
(see ``runtime_data.CommissionedSwitchRuntime.record_verified_and_fire``),
so a B0/B0-ish or combo telegram from a 1-rocker switch's plate fires the
A0 (or A1) entity, never a nonexistent B0/combo one. Entities are created
at setup for every commissioned switch's subentry (so they survive a Home
Assistant restart) and again after any Add-device-wizard or
subentry-removal reload. Each entity is tied to its subentry via
``config_subentry_id`` on :func:`~homeassistant.helpers.entity_platform.
AddEntitiesCallback.__call__` -- NOT via ``DeviceInfo`` -- so removing the
subentry cleanly removes its device and entities.

Each entity fires only when ``button_pipeline.py``'s fail-closed pipeline
decodes a MIC-verified, counter-accepted telegram whose (normalized)
pattern names this exact entity; see that module's docstring for the full
gate order and the first-trust policy that decides when NO event fires at
all.

As of Phase 5B, each raw verified telegram passes through
``press_timing.PressTimingTracker`` (one per switch, owned by
``runtime_data.CommissionedSwitchRuntime``) before reaching this entity, so
``event_types`` now also includes the derived ``short_press``/
``long_press`` actions -- see ``press_timing.py``'s module docstring for
the hold-time state machine and its radio-loss safety rule.
``async_setup_entry`` configures each switch's tracker threshold/scheduler
once, from that switch's ``long_press_threshold_ms`` subentry field (see
``config_flow.py``'s key-entry/reconfigure schemas). It also sets
``switch_runtime.rockers`` from the same subentry's ``rockers`` field --
the one thing that feeds ``normalize_button_pattern`` above.

Orphan-entity cleanup (Phase 5D): when a switch's ``rockers`` setting
changes from 2 to 1 via Reconfigure, the full config-entry reload this
triggers (see ``__init__.py``'s ``_async_subentries_changed``) re-runs this
function, which now only builds two entities for that subentry instead of
six -- but ``async_add_entities`` is purely additive and does NOT, on its
own, remove a previously-registered unique_id that this run does not
re-add. ``_prune_stale_event_entities`` below removes exactly those four
now-stale registry entries (B0, B1, A0+B0, A1+B1) for that subentry, and
only this platform's (domain ``event``) entities for that subentry --
never a sensor-platform entity or another subentry's entities. See
``tests/test_event_entities.py``'s orphan-cleanup-on-reload test.
"""

from __future__ import annotations

from typing import Callable

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .const import ATTR_ACTION, ATTR_BUTTON, DOMAIN, EVENT_ENOCEAN_PTM216B
from .press_timing import DEFAULT_LONG_PRESS_THRESHOLD_MS, PressAction
from .telegram import ButtonPattern

_EVENT_TYPES = [action.value for action in PressAction]
# All six patterns, in ButtonPattern's own declared order (A0, A1, B0, B1,
# A0_B0, A1_B1) -- the two-rocker default entity set.
_TWO_ROCKER_PATTERNS = tuple(ButtonPattern)
_SINGLE_ROCKER_PATTERNS = (ButtonPattern.A0, ButtonPattern.A1)


def _make_scheduler(
    hass: HomeAssistant,
) -> Callable[[float, Callable[[], None]], Callable[[], None]]:
    """Return a press_timing.PressScheduler backed by Home Assistant's event loop.

    Mirrors ``config_flow.py``'s own ``_schedule`` helper exactly -- the
    same ``async_call_later``-based convention every bounded timer in this
    integration uses.
    """

    def _schedule(delay: float, finish: Callable[[], None]) -> Callable[[], None]:
        return async_call_later(hass, delay, lambda _now: finish())

    return _schedule


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create each commissioned switch's button-pattern event entities, per subentry."""
    store = entry.runtime_data.commissioning_store
    if store is None:
        return

    handles_to_addresses = {
        entry.runtime_data.commissioned_device_handle(address): address
        for address in store.switches
    }
    registry = er.async_get(hass)

    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != "switch":
            continue
        canonical_address = handles_to_addresses.get(subentry.data.get("handle"))
        switch = store.switches.get(canonical_address) if canonical_address else None
        if canonical_address is None or switch is None:
            continue
        # Matches this file's/device_trigger.py's shared convention: only
        # exactly 1 means single-rocker, everything else (including a
        # missing field, for a pre-Phase-5A migrated switch) is the
        # two-rocker default.
        rockers = 1 if subentry.data.get("rockers") == 1 else 2
        patterns = _SINGLE_ROCKER_PATTERNS if rockers == 1 else _TWO_ROCKER_PATTERNS
        switch_runtime = entry.runtime_data.commissioned_switch_runtime(
            canonical_address
        )
        switch_runtime.rockers = rockers
        switch_runtime.press_tracker.threshold_ms = subentry.data.get(
            "long_press_threshold_ms", DEFAULT_LONG_PRESS_THRESHOLD_MS
        )
        switch_runtime.press_tracker.scheduler = _make_scheduler(hass)
        entities = [
            Ptm216bButtonEventEntity(entry, canonical_address, switch.name, pattern)
            for pattern in patterns
        ]
        async_add_entities(entities, config_subentry_id=subentry_id)

        handle = entry.runtime_data.commissioned_device_handle(canonical_address)
        expected_unique_ids = {
            f"{entry.entry_id}_{handle}_{pattern.value}" for pattern in patterns
        }
        _prune_stale_event_entities(registry, entry, subentry_id, expected_unique_ids)


def _prune_stale_event_entities(
    registry: er.EntityRegistry,
    entry: ConfigEntry,
    subentry_id: str,
    expected_unique_ids: set[str],
) -> None:
    """Remove this platform's registry entries for a subentry that this run
    no longer re-adds -- e.g. B0/B1/A0+B0/A1+B1 left behind after a switch's
    ``rockers`` setting changes from 2 to 1 via Reconfigure.

    ``async_add_entities`` above is purely additive: it never removes a
    previously-registered unique_id that this setup pass simply does not
    include this time, so without this, those four entities' registry
    entries -- and the (now permanently `unavailable`) entities they'd
    produce -- would survive every future reload indefinitely. This is NOT
    the same cleanup as Home Assistant's own subentry-*deletion* handling
    (``async_remove_subentry`` -> ``async_clear_config_subentry``, see
    ``test_subentry_removal.py``): that fires only when the subentry itself
    is deleted, not when it survives with a shrunk pattern set, so it does
    not apply here and this explicit pass is necessary.

    Scoped tightly to avoid collateral damage: only entities in this
    platform's own domain (``event``) AND belonging to this exact
    ``subentry_id`` are even considered, so this never touches a
    sensor-platform diagnostic entity or another subentry's (another
    commissioned switch's) entities, even though they share the same
    config entry.
    """
    for entity_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity_entry.domain != "event":
            continue
        if entity_entry.config_subentry_id != subentry_id:
            continue
        if entity_entry.unique_id not in expected_unique_ids:
            registry.async_remove(entity_entry.entity_id)


class Ptm216bButtonEventEntity(EventEntity):
    """One button pattern's press/release event, for one commissioned switch.

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

    As of Phase 5B, :meth:`_handle_button_event` also fires
    ``const.EVENT_ENOCEAN_PTM216B`` on the event bus, carrying only
    ``{device_id, button, action}`` -- ``device_trigger.py`` filters on
    exactly this bus event to offer this pattern's short_press/long_press/
    press/release as device triggers in the automation editor. Fired only
    once this entity has a ``device_entry`` (i.e. once it has actually gone
    through entity-platform registration) -- a bare, unregistered instance
    (as constructed directly in unit tests) safely skips the bus fire and
    still triggers/writes state exactly as before.

    ``_attr_unique_id`` is built from ``pattern.value`` exactly as it was
    from the old ``Button`` enum's ``.value`` before Phase 5D --
    ``ButtonPattern.A0.value == "A0"`` etc. are byte-for-byte identical
    strings, so every pre-existing A0/A1/B0/B1 entity keeps its unique_id
    (and therefore its entity_id, area assignment, and automation
    references) completely unchanged across this upgrade; see
    ``tests/test_event_entities.py``'s explicit stable-unique_id test. The
    two combo patterns' unique_ids (``..._A0+B0``, ``..._A1+B1``) are new
    -- that is expected, not a migration concern.
    """

    _attr_has_entity_name = True
    _attr_event_types = _EVENT_TYPES

    def __init__(
        self,
        entry: ConfigEntry,
        canonical_address: str,
        name: str,
        pattern: ButtonPattern,
    ) -> None:
        self._entry = entry
        self._canonical_address = canonical_address
        self._pattern = pattern
        handle = entry.runtime_data.commissioned_device_handle(canonical_address)
        self._attr_unique_id = f"{entry.entry_id}_{handle}_{pattern.value}"
        self._attr_name = pattern.value
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, handle)},
            name=name,
            manufacturer="EnOcean",
            model="PTM 216B",
        )

    async def async_added_to_hass(self) -> None:
        """Register this pattern's listener with the switch's runtime record."""
        await super().async_added_to_hass()
        switch_runtime = self._entry.runtime_data.commissioned_switch_runtime(
            self._canonical_address
        )
        switch_runtime.set_event_listener(self._pattern, self._handle_button_event)

    async def async_will_remove_from_hass(self) -> None:
        """Unregister without leaving a stale listener on the runtime record."""
        switch_runtime = self._entry.runtime_data.commissioned_switch_runtime(
            self._canonical_address
        )
        switch_runtime.set_event_listener(self._pattern, None)
        await super().async_will_remove_from_hass()

    def _handle_button_event(self, action: PressAction) -> None:
        """Trigger this pattern's event, write state, and fire the trigger bus event."""
        self._trigger_event(action.value)
        self.async_write_ha_state()
        if self.hass is not None and self.device_entry is not None:
            self.hass.bus.async_fire(
                EVENT_ENOCEAN_PTM216B,
                {
                    ATTR_DEVICE_ID: self.device_entry.id,
                    ATTR_BUTTON: self._pattern.value,
                    ATTR_ACTION: action.value,
                },
            )
