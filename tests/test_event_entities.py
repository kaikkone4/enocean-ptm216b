"""Tests for event.py: per-commissioned-switch button event entities."""

from __future__ import annotations

from unittest.mock import Mock

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.enocean_ptm216b.commissioning_store import CommissioningStore
from custom_components.enocean_ptm216b.const import DOMAIN
from custom_components.enocean_ptm216b.event import (
    Ptm216bButtonEventEntity,
    async_setup_entry,
)
from custom_components.enocean_ptm216b.identity import canonicalize_address
from custom_components.enocean_ptm216b.press_timing import (
    DEFAULT_LONG_PRESS_THRESHOLD_MS,
    PressAction,
)
from custom_components.enocean_ptm216b.runtime_data import Ptm216bRuntimeData
from custom_components.enocean_ptm216b.telegram import Button, Ptm216bButtonState

from conftest import RecordingAddEntities

SECRET = b"\x01" * 32
ADDRESS = "AA:BB:CC:DD:EE:FF"
CANONICAL_ADDRESS = canonicalize_address(ADDRESS)
SYNTHETIC_KEY = bytes(range(16))


def _make_entry() -> Mock:
    entry = Mock(entry_id="entry-id")
    entry.runtime_data = Ptm216bRuntimeData(_hmac_secret=SECRET)
    return entry


async def _commissioned_entry(
    hass,
    *,
    name: str = "Living room switch",
    rockers: int = 2,
    threshold_ms: int | None = None,
) -> MockConfigEntry:
    """Build an entry with a store record and a matching "switch" subentry,
    the same shape ``__init__.py``'s reconciliation and this platform's
    ``async_setup_entry`` both expect after Phase 5A.

    ``threshold_ms=None`` (the default) omits ``long_press_threshold_ms``
    from subentry data entirely, mirroring a switch commissioned before
    Phase 5B -- exercising ``async_setup_entry``'s ``.get(..., default)``
    fallback.
    """
    store = CommissioningStore(hass)
    await store.async_load()
    await store.async_add(CANONICAL_ADDRESS, SYNTHETIC_KEY, name)
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET, commissioning_store=store)
    handle = runtime.commissioned_device_handle(CANONICAL_ADDRESS)

    subentry_data = {"handle": handle, "name": name, "rockers": rockers}
    if threshold_ms is not None:
        subentry_data["long_press_threshold_ms"] = threshold_ms

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        subentries_data=[
            {
                "data": subentry_data,
                "subentry_type": "switch",
                "title": name,
                "unique_id": handle,
            }
        ],
    )
    entry.runtime_data = runtime
    entry.add_to_hass(hass)
    return entry


def test_event_entity_unique_id_and_device_info_never_expose_the_address():
    entry = _make_entry()
    handle = entry.runtime_data.commissioned_device_handle(CANONICAL_ADDRESS)

    entity = Ptm216bButtonEventEntity(
        entry, CANONICAL_ADDRESS, "Living room switch", Button.A0
    )

    assert entity.unique_id == f"entry-id_{handle}_A0"
    assert entity.device_info["identifiers"] == {(DOMAIN, handle)}
    assert entity.device_info["manufacturer"] == "EnOcean"
    assert entity.device_info["model"] == "PTM 216B"
    assert entity.device_info["name"] == "Living room switch"
    assert CANONICAL_ADDRESS not in entity.unique_id
    assert CANONICAL_ADDRESS not in str(entity.device_info["identifiers"])
    assert CANONICAL_ADDRESS not in repr(entity.device_info)


def test_event_entity_declares_all_four_press_action_event_types():
    entry = _make_entry()
    entity = Ptm216bButtonEventEntity(entry, CANONICAL_ADDRESS, "Switch", Button.B1)

    assert entity.event_types == ["press", "release", "short_press", "long_press"]
    assert entity.name == "B1"


async def test_event_entity_registers_and_unregisters_with_the_switch_runtime():
    entry = _make_entry()
    entity = Ptm216bButtonEventEntity(entry, CANONICAL_ADDRESS, "Switch", Button.A0)
    switch_runtime = entry.runtime_data.commissioned_switch_runtime(CANONICAL_ADDRESS)

    await entity.async_added_to_hass()
    # Bound-method identity (`is`) is not guaranteed across attribute
    # accesses in Python; equality (same __self__/__func__) is the correct
    # check here.
    assert (
        switch_runtime.press_tracker._listeners[Button.A0]
        == entity._handle_button_event
    )

    await entity.async_will_remove_from_hass()
    assert Button.A0 not in switch_runtime.press_tracker._listeners


def test_handle_button_event_press_triggers_and_writes_state():
    entry = _make_entry()
    entity = Ptm216bButtonEventEntity(entry, CANONICAL_ADDRESS, "Switch", Button.A0)
    entity.async_write_ha_state = Mock()

    entity._handle_button_event(PressAction.PRESS)

    assert entity.state_attributes["event_type"] == "press"
    entity.async_write_ha_state.assert_called_once_with()


def test_handle_button_event_release_triggers_and_writes_state():
    entry = _make_entry()
    entity = Ptm216bButtonEventEntity(entry, CANONICAL_ADDRESS, "Switch", Button.A0)
    entity.async_write_ha_state = Mock()

    entity._handle_button_event(PressAction.RELEASE)

    assert entity.state_attributes["event_type"] == "release"
    entity.async_write_ha_state.assert_called_once_with()


def test_handle_button_event_short_press_triggers_and_writes_state():
    entry = _make_entry()
    entity = Ptm216bButtonEventEntity(entry, CANONICAL_ADDRESS, "Switch", Button.A0)
    entity.async_write_ha_state = Mock()

    entity._handle_button_event(PressAction.SHORT_PRESS)

    assert entity.state_attributes["event_type"] == "short_press"
    entity.async_write_ha_state.assert_called_once_with()


def test_handle_button_event_long_press_triggers_and_writes_state():
    entry = _make_entry()
    entity = Ptm216bButtonEventEntity(entry, CANONICAL_ADDRESS, "Switch", Button.A0)
    entity.async_write_ha_state = Mock()

    entity._handle_button_event(PressAction.LONG_PRESS)

    assert entity.state_attributes["event_type"] == "long_press"
    entity.async_write_ha_state.assert_called_once_with()


def test_handle_button_event_without_hass_skips_bus_fire_but_still_triggers():
    """A bare, unregistered entity (as constructed in every test above --
    ``self.hass``/``self.device_entry`` are never set) must never try to
    fire a bus event; it still triggers/writes state exactly as before.
    """
    entry = _make_entry()
    entity = Ptm216bButtonEventEntity(entry, CANONICAL_ADDRESS, "Switch", Button.A0)
    entity.async_write_ha_state = Mock()
    assert entity.hass is None
    assert entity.device_entry is None

    entity._handle_button_event(PressAction.PRESS)  # must not raise

    assert entity.state_attributes["event_type"] == "press"


async def test_async_setup_entry_creates_four_entities_per_commissioned_switch(hass):
    entry = await _commissioned_entry(hass, rockers=2)

    recorder = RecordingAddEntities()
    await async_setup_entry(hass, entry, recorder)

    assert len(recorder.added) == 4
    assert {entity._button for entity in recorder.added} == set(Button)
    assert all(
        isinstance(entity, Ptm216bButtonEventEntity) for entity in recorder.added
    )
    (subentry_id,) = entry.subentries
    assert recorder.subentry_ids == [subentry_id] * 4


async def test_async_setup_entry_creates_only_a0_a1_for_a_single_rocker_switch(hass):
    entry = await _commissioned_entry(hass, rockers=1)

    recorder = RecordingAddEntities()
    await async_setup_entry(hass, entry, recorder)

    assert len(recorder.added) == 2
    assert {entity._button for entity in recorder.added} == {Button.A0, Button.A1}


async def test_async_setup_entry_creates_nothing_when_no_switches_commissioned(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    store = CommissioningStore(hass)
    await store.async_load()
    entry.runtime_data = Ptm216bRuntimeData(
        _hmac_secret=SECRET, commissioning_store=store
    )
    entry.add_to_hass(hass)

    recorder = RecordingAddEntities()
    await async_setup_entry(hass, entry, recorder)

    assert recorder.added == []


async def test_only_the_matching_buttons_entity_fires(hass):
    entry = await _commissioned_entry(hass, rockers=2)

    recorder = RecordingAddEntities()
    await async_setup_entry(hass, entry, recorder)
    entities = {entity._button: entity for entity in recorder.added}
    for entity in entities.values():
        entity.async_write_ha_state = Mock()
        await entity.async_added_to_hass()

    switch_runtime = entry.runtime_data.commissioned_switch_runtime(CANONICAL_ADDRESS)
    switch_runtime.record_verified_and_fire(
        Ptm216bButtonState(button=Button.B1, is_press=True)
    )

    assert entities[Button.B1].state_attributes["event_type"] == "press"
    entities[Button.B1].async_write_ha_state.assert_called_once_with()
    for button in (Button.A0, Button.A1, Button.B0):
        assert entities[button].state is None
        entities[button].async_write_ha_state.assert_not_called()


async def test_verified_b_button_telegram_on_single_rocker_switch_fires_no_event(hass):
    """A rockers==1 switch never gets a B0/B1 event entity, so
    ``CommissionedSwitchRuntime.record_verified_and_fire`` for a B-button
    telegram finds no listener and is a no-op -- it still counts as
    verified, per button_pipeline.py's own docstring, it just fires
    nothing observable. No change to button_pipeline.py/runtime_data.py was
    needed for this: only event.py's entity-creation filtering.
    """
    entry = await _commissioned_entry(hass, rockers=1)

    recorder = RecordingAddEntities()
    await async_setup_entry(hass, entry, recorder)
    for entity in recorder.added:
        entity.async_write_ha_state = Mock()
        await entity.async_added_to_hass()

    switch_runtime = entry.runtime_data.commissioned_switch_runtime(CANONICAL_ADDRESS)
    switch_runtime.record_verified_and_fire(
        Ptm216bButtonState(button=Button.B0, is_press=True)
    )

    assert switch_runtime.verified_count == 1
    for entity in recorder.added:
        entity.async_write_ha_state.assert_not_called()


# ---------------------------------------------------------------------------
# Phase 5B: press_tracker threshold wiring + short_press/long_press routing
# ---------------------------------------------------------------------------


async def test_async_setup_entry_wires_the_default_threshold_when_field_is_absent(
    hass,
):
    """A pre-Phase-5B subentry has no ``long_press_threshold_ms`` field at
    all; ``async_setup_entry`` must still configure the tracker with the
    default rather than leaving it unset/erroring.
    """
    entry = await _commissioned_entry(hass, threshold_ms=None)

    recorder = RecordingAddEntities()
    await async_setup_entry(hass, entry, recorder)

    switch_runtime = entry.runtime_data.commissioned_switch_runtime(CANONICAL_ADDRESS)
    assert switch_runtime.press_tracker.threshold_ms == DEFAULT_LONG_PRESS_THRESHOLD_MS
    assert switch_runtime.press_tracker.scheduler is not None


async def test_async_setup_entry_wires_a_custom_threshold_from_subentry_data(hass):
    entry = await _commissioned_entry(hass, threshold_ms=1500)

    recorder = RecordingAddEntities()
    await async_setup_entry(hass, entry, recorder)

    switch_runtime = entry.runtime_data.commissioned_switch_runtime(CANONICAL_ADDRESS)
    assert switch_runtime.press_tracker.threshold_ms == 1500


async def test_short_press_and_long_press_route_through_to_the_entity(hass):
    """End-to-end (minus real Bluetooth wiring): a verified press followed
    by the tracker's own hold timer firing reaches the button entity as
    short_press/long_press, exactly like raw press/release already did.
    """
    entry = await _commissioned_entry(hass, threshold_ms=500)
    recorder = RecordingAddEntities()
    await async_setup_entry(hass, entry, recorder)
    entities = {entity._button: entity for entity in recorder.added}
    for entity in entities.values():
        entity.async_write_ha_state = Mock()
        await entity.async_added_to_hass()

    switch_runtime = entry.runtime_data.commissioned_switch_runtime(CANONICAL_ADDRESS)
    switch_runtime.record_verified_and_fire(
        Ptm216bButtonState(button=Button.A0, is_press=True)
    )
    assert entities[Button.A0].state_attributes["event_type"] == "press"

    # Fire the tracker's own hold timer directly -- this test does not
    # depend on real wall-clock time, only on the scheduler actually being
    # wired (see test_async_setup_entry_wires_the_default_threshold_when_
    # field_is_absent for that wiring itself).
    open_press = switch_runtime.press_tracker._open[Button.A0]
    assert open_press.cancel_timer is not None  # a real cancel handle was stored
    switch_runtime.press_tracker._fire_long_press(Button.A0, open_press)

    assert entities[Button.A0].state_attributes["event_type"] == "long_press"

    switch_runtime.record_verified_and_fire(
        Ptm216bButtonState(button=Button.A0, is_press=False)
    )
    assert entities[Button.A0].state_attributes["event_type"] == "release"
