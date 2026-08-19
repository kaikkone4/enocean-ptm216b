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
from custom_components.enocean_ptm216b.runtime_data import Ptm216bRuntimeData
from custom_components.enocean_ptm216b.telegram import Button, Ptm216bButtonState

SECRET = b"\x01" * 32
ADDRESS = "AA:BB:CC:DD:EE:FF"
CANONICAL_ADDRESS = canonicalize_address(ADDRESS)
SYNTHETIC_KEY = bytes(range(16))


def _make_entry() -> Mock:
    entry = Mock(entry_id="entry-id")
    entry.runtime_data = Ptm216bRuntimeData(_hmac_secret=SECRET)
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


def test_event_entity_declares_press_and_release_event_types():
    entry = _make_entry()
    entity = Ptm216bButtonEventEntity(entry, CANONICAL_ADDRESS, "Switch", Button.B1)

    assert entity.event_types == ["press", "release"]
    assert entity.name == "B1"


async def test_event_entity_registers_and_unregisters_with_the_switch_runtime():
    entry = _make_entry()
    entity = Ptm216bButtonEventEntity(entry, CANONICAL_ADDRESS, "Switch", Button.A0)
    switch_runtime = entry.runtime_data.commissioned_switch_runtime(CANONICAL_ADDRESS)

    await entity.async_added_to_hass()
    # Bound-method identity (`is`) is not guaranteed across attribute
    # accesses in Python; equality (same __self__/__func__) is the correct
    # check here.
    assert switch_runtime._event_listeners[Button.A0] == entity._handle_button_event

    await entity.async_will_remove_from_hass()
    assert Button.A0 not in switch_runtime._event_listeners


def test_handle_button_event_press_triggers_and_writes_state():
    entry = _make_entry()
    entity = Ptm216bButtonEventEntity(entry, CANONICAL_ADDRESS, "Switch", Button.A0)
    entity.async_write_ha_state = Mock()

    entity._handle_button_event(True)

    assert entity.state_attributes["event_type"] == "press"
    entity.async_write_ha_state.assert_called_once_with()


def test_handle_button_event_release_triggers_and_writes_state():
    entry = _make_entry()
    entity = Ptm216bButtonEventEntity(entry, CANONICAL_ADDRESS, "Switch", Button.A0)
    entity.async_write_ha_state = Mock()

    entity._handle_button_event(False)

    assert entity.state_attributes["event_type"] == "release"
    entity.async_write_ha_state.assert_called_once_with()


async def test_async_setup_entry_creates_four_entities_per_commissioned_switch(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    store = CommissioningStore(hass)
    await store.async_load()
    await store.async_add(CANONICAL_ADDRESS, SYNTHETIC_KEY, "Living room switch")
    entry.runtime_data = Ptm216bRuntimeData(
        _hmac_secret=SECRET, commissioning_store=store
    )
    entry.add_to_hass(hass)

    added: list = []
    await async_setup_entry(hass, entry, added.extend)

    assert len(added) == 4
    assert {entity._button for entity in added} == set(Button)
    assert all(isinstance(entity, Ptm216bButtonEventEntity) for entity in added)


async def test_async_setup_entry_creates_nothing_when_no_switches_commissioned(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    store = CommissioningStore(hass)
    await store.async_load()
    entry.runtime_data = Ptm216bRuntimeData(
        _hmac_secret=SECRET, commissioning_store=store
    )
    entry.add_to_hass(hass)

    added: list = []
    await async_setup_entry(hass, entry, added.extend)

    assert added == []


async def test_only_the_matching_buttons_entity_fires(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    store = CommissioningStore(hass)
    await store.async_load()
    await store.async_add(CANONICAL_ADDRESS, SYNTHETIC_KEY, "Living room switch")
    entry.runtime_data = Ptm216bRuntimeData(
        _hmac_secret=SECRET, commissioning_store=store
    )
    entry.add_to_hass(hass)

    added: list = []
    await async_setup_entry(hass, entry, added.extend)
    entities = {entity._button: entity for entity in added}
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
