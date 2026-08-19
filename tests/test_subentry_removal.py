"""Tests for the decommission-via-subentry-deletion path (Phase 5A).

Removing a "switch" subentry (what the frontend's delete-subentry action
calls under the hood: ``hass.config_entries.async_remove_subentry``)
already cleans up the device/entity registry automatically -- this is a
verified fact about Home Assistant's own subentry machinery, not something
this integration implements. This integration's own job is purging the
*private* commissioning store record, which the registry cleanup knows
nothing about -- see ``__init__.py``'s ``_async_reconcile_commissioning_store``.
"""

from __future__ import annotations

import homeassistant.helpers.device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.enocean_ptm216b import _async_reconcile_commissioning_store
from custom_components.enocean_ptm216b.commissioning_store import CommissioningStore
from custom_components.enocean_ptm216b.const import DOMAIN
from custom_components.enocean_ptm216b.identity import canonicalize_address
from custom_components.enocean_ptm216b.runtime_data import Ptm216bRuntimeData

SECRET = b"\x01" * 32
ADDRESS = "AA:BB:CC:DD:EE:FF"
CANONICAL_ADDRESS = canonicalize_address(ADDRESS)
SYNTHETIC_KEY = bytes(range(16))


async def test_removing_the_subentry_clears_its_device_automatically(hass):
    store = CommissioningStore(hass)
    await store.async_load()
    await store.async_add(CANONICAL_ADDRESS, SYNTHETIC_KEY, "Living room switch")
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET, commissioning_store=store)
    handle = runtime.commissioned_device_handle(CANONICAL_ADDRESS)

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        subentries_data=[
            {
                "data": {"handle": handle, "name": "Living room switch", "rockers": 2},
                "subentry_type": "switch",
                "title": "Living room switch",
                "unique_id": handle,
            }
        ],
    )
    entry.runtime_data = runtime
    entry.add_to_hass(hass)
    (subentry_id,) = entry.subentries

    registry = dr.async_get(hass)
    registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        config_subentry_id=subentry_id,
        identifiers={(DOMAIN, handle)},
        name="Living room switch",
        manufacturer="EnOcean",
        model="PTM 216B",
    )
    assert registry.async_get_device(identifiers={(DOMAIN, handle)}) is not None

    hass.config_entries.async_remove_subentry(entry, subentry_id)

    assert entry.subentries == {}
    assert registry.async_get_device(identifiers={(DOMAIN, handle)}) is None
    # The registry cleanup knows nothing about the private store -- it is
    # still there until the next reconciliation pass (below).
    assert store.get(CANONICAL_ADDRESS) is not None


async def test_reconciliation_purges_the_store_after_subentry_removal(hass):
    store = CommissioningStore(hass)
    await store.async_load()
    await store.async_add(CANONICAL_ADDRESS, SYNTHETIC_KEY, "Living room switch")
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET, commissioning_store=store)
    handle = runtime.commissioned_device_handle(CANONICAL_ADDRESS)

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        subentries_data=[
            {
                "data": {"handle": handle, "name": "Living room switch", "rockers": 2},
                "subentry_type": "switch",
                "title": "Living room switch",
                "unique_id": handle,
            }
        ],
    )
    entry.runtime_data = runtime
    entry.add_to_hass(hass)
    (subentry_id,) = entry.subentries

    hass.config_entries.async_remove_subentry(entry, subentry_id)
    assert store.get(CANONICAL_ADDRESS) is not None  # not yet reconciled

    await _async_reconcile_commissioning_store(runtime, entry, store)

    assert store.get(CANONICAL_ADDRESS) is None


async def test_reconciliation_never_touches_an_unrelated_still_live_switch(hass):
    other_address = canonicalize_address("11:22:33:44:55:66")
    store = CommissioningStore(hass)
    await store.async_load()
    await store.async_add(CANONICAL_ADDRESS, SYNTHETIC_KEY, "Removed switch")
    await store.async_add(other_address, bytes(range(16, 32)), "Kept switch")
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET, commissioning_store=store)
    kept_handle = runtime.commissioned_device_handle(other_address)

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        subentries_data=[
            {
                "data": {"handle": kept_handle, "name": "Kept switch", "rockers": 2},
                "subentry_type": "switch",
                "title": "Kept switch",
                "unique_id": kept_handle,
            }
        ],
    )
    entry.runtime_data = runtime
    entry.add_to_hass(hass)

    await _async_reconcile_commissioning_store(runtime, entry, store)

    assert store.get(CANONICAL_ADDRESS) is None
    assert store.get(other_address) is not None
