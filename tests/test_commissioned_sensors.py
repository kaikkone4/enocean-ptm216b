"""Tests for the per-commissioned-switch diagnostic sensors in sensor.py."""

from __future__ import annotations

from unittest.mock import Mock

from homeassistant.helpers.entity import EntityCategory
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.enocean_ptm216b.commissioning_store import CommissioningStore
from custom_components.enocean_ptm216b.const import DOMAIN
from custom_components.enocean_ptm216b.identity import canonicalize_address
from custom_components.enocean_ptm216b.runtime_data import Ptm216bRuntimeData
from custom_components.enocean_ptm216b.sensor import (
    Ptm216bRejectedTelegramsSensor,
    Ptm216bVerifiedTelegramsSensor,
    async_setup_entry,
)

SECRET = b"\x01" * 32
ADDRESS = "AA:BB:CC:DD:EE:FF"
CANONICAL_ADDRESS = canonicalize_address(ADDRESS)
SYNTHETIC_KEY = bytes(range(16))


def _make_entry() -> Mock:
    entry = Mock(entry_id="entry-id")
    entry.runtime_data = Ptm216bRuntimeData(_hmac_secret=SECRET)
    return entry


def test_verified_sensor_exposes_only_the_live_verified_count():
    entry = _make_entry()
    sensor = Ptm216bVerifiedTelegramsSensor(entry, CANONICAL_ADDRESS, "Switch")

    assert sensor.native_value == 0
    assert sensor.entity_category is EntityCategory.DIAGNOSTIC
    assert CANONICAL_ADDRESS not in sensor.unique_id

    switch_runtime = entry.runtime_data.commissioned_switch_runtime(CANONICAL_ADDRESS)
    switch_runtime.verified_count = 3

    assert sensor.native_value == 3


def test_rejected_sensor_exposes_only_the_live_rejected_count():
    entry = _make_entry()
    sensor = Ptm216bRejectedTelegramsSensor(entry, CANONICAL_ADDRESS, "Switch")

    assert sensor.native_value == 0
    assert sensor.entity_category is EntityCategory.DIAGNOSTIC

    switch_runtime = entry.runtime_data.commissioned_switch_runtime(CANONICAL_ADDRESS)
    switch_runtime.rejected_count = 5

    assert sensor.native_value == 5


def test_verified_and_rejected_sensors_share_a_device_but_have_distinct_ids():
    entry = _make_entry()
    verified = Ptm216bVerifiedTelegramsSensor(entry, CANONICAL_ADDRESS, "Switch")
    rejected = Ptm216bRejectedTelegramsSensor(entry, CANONICAL_ADDRESS, "Switch")

    assert verified.device_info["identifiers"] == rejected.device_info["identifiers"]
    assert verified.unique_id != rejected.unique_id


async def test_both_sensors_redraw_independently_on_their_own_counter():
    entry = _make_entry()
    verified = Ptm216bVerifiedTelegramsSensor(entry, CANONICAL_ADDRESS, "Switch")
    rejected = Ptm216bRejectedTelegramsSensor(entry, CANONICAL_ADDRESS, "Switch")
    verified.async_write_ha_state = Mock()
    rejected.async_write_ha_state = Mock()
    await verified.async_added_to_hass()
    await rejected.async_added_to_hass()

    switch_runtime = entry.runtime_data.commissioned_switch_runtime(CANONICAL_ADDRESS)
    switch_runtime.record_rejected()

    verified.async_write_ha_state.assert_called_once_with()
    rejected.async_write_ha_state.assert_called_once_with()

    await verified.async_will_remove_from_hass()
    await rejected.async_will_remove_from_hass()
    verified.async_write_ha_state.reset_mock()
    rejected.async_write_ha_state.reset_mock()

    switch_runtime.record_rejected()

    verified.async_write_ha_state.assert_not_called()
    rejected.async_write_ha_state.assert_not_called()


async def test_async_setup_entry_adds_two_diagnostic_sensors_per_switch(hass):
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

    verified = [e for e in added if isinstance(e, Ptm216bVerifiedTelegramsSensor)]
    rejected = [e for e in added if isinstance(e, Ptm216bRejectedTelegramsSensor)]
    # Plus the 3 always-present observation-MVP sensors.
    assert len(added) == 5
    assert len(verified) == 1
    assert len(rejected) == 1


async def test_async_setup_entry_adds_no_commissioned_sensors_without_switches(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    store = CommissioningStore(hass)
    await store.async_load()
    entry.runtime_data = Ptm216bRuntimeData(
        _hmac_secret=SECRET, commissioning_store=store
    )
    entry.add_to_hass(hass)

    added: list = []
    await async_setup_entry(hass, entry, added.extend)

    assert len(added) == 3
