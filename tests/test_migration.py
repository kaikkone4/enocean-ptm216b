"""Tests for __init__.py's Phase 5A migration and subentry reconciliation.

All key/address/name material in this file is synthetic test data.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components import enocean_ptm216b
from custom_components.enocean_ptm216b.commissioning_store import CommissioningStore
from custom_components.enocean_ptm216b.const import DOMAIN
from custom_components.enocean_ptm216b.identity import canonicalize_address

SECRET = b"\x01" * 32
ADDRESS = "AA:BB:CC:DD:EE:FF"
CANONICAL_ADDRESS = canonicalize_address(ADDRESS)
SYNTHETIC_KEY = bytes(range(16))


async def test_migrate_entry_backfills_one_subentry_per_v0_4_0_record(hass):
    store = CommissioningStore(hass)
    await store.async_load()
    await store.async_add(CANONICAL_ADDRESS, SYNTHETIC_KEY, "Living room switch")
    entry = MockConfigEntry(domain=DOMAIN, data={}, version=1)
    entry.add_to_hass(hass)

    with patch(
        "custom_components.enocean_ptm216b.IntegrationSecretStore.async_get_or_create",
        AsyncMock(return_value=SECRET),
    ):
        result = await enocean_ptm216b.async_migrate_entry(hass, entry)

    assert result is True
    assert len(entry.subentries) == 1
    (subentry,) = entry.subentries.values()
    assert subentry.subentry_type == "switch"
    assert subentry.title == "Living room switch"
    assert subentry.data["name"] == "Living room switch"
    assert subentry.data["rockers"] == 2
    assert "handle" in subentry.data
    # No address/key leak in the subentry data itself, or its repr.
    serialized = repr(subentry)
    assert CANONICAL_ADDRESS not in serialized
    assert ADDRESS not in serialized
    assert SYNTHETIC_KEY.hex() not in serialized
    assert CANONICAL_ADDRESS not in repr(subentry.data)


async def test_migrate_entry_is_a_noop_when_already_migrated(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={}, version=2)
    entry.add_to_hass(hass)

    with patch(
        "custom_components.enocean_ptm216b.IntegrationSecretStore.async_get_or_create",
        AsyncMock(return_value=SECRET),
    ) as get_secret:
        result = await enocean_ptm216b.async_migrate_entry(hass, entry)

    assert result is True
    get_secret.assert_not_called()
    assert entry.subentries == {}


async def test_migrate_entry_backfills_nothing_for_an_empty_store(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={}, version=1)
    entry.add_to_hass(hass)

    with patch(
        "custom_components.enocean_ptm216b.IntegrationSecretStore.async_get_or_create",
        AsyncMock(return_value=SECRET),
    ):
        result = await enocean_ptm216b.async_migrate_entry(hass, entry)

    assert result is True
    assert entry.subentries == {}


async def _setup_entry_directly(hass, entry: MockConfigEntry) -> None:
    """Call async_setup_entry the same way tests/test_setup.py already does,
    bypassing the real dependency-loading path (see that file's tests for
    why: it requires ``dbus_fast``, unavailable outside Linux).
    """
    with (
        patch(
            "custom_components.enocean_ptm216b.bluetooth.async_register_callback",
            return_value=Mock(),
        ),
        patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()),
    ):
        assert await enocean_ptm216b.async_setup_entry(hass, entry)


async def test_setup_reconciliation_purges_a_store_record_with_no_subentry(hass):
    """Simulates the decommission-via-subentry-deletion path: the subentry
    is gone (e.g. removed through the frontend), but the private store
    record survives until the next setup/reload reconciles them.
    """
    store = CommissioningStore(hass)
    await store.async_load()
    await store.async_add(CANONICAL_ADDRESS, SYNTHETIC_KEY, "Orphaned switch")
    entry = MockConfigEntry(domain=DOMAIN, data={}, version=2)
    entry.add_to_hass(hass)

    with patch(
        "custom_components.enocean_ptm216b.IntegrationSecretStore.async_get_or_create",
        AsyncMock(return_value=SECRET),
    ):
        await _setup_entry_directly(hass, entry)

    assert entry.runtime_data.commissioning_store.get(CANONICAL_ADDRESS) is None


async def test_setup_reconciliation_keeps_a_store_record_with_a_live_subentry(hass):
    store = CommissioningStore(hass)
    await store.async_load()
    await store.async_add(CANONICAL_ADDRESS, SYNTHETIC_KEY, "Kept switch")

    from custom_components.enocean_ptm216b.identity import (
        device_handle,
        device_identifier,
    )

    handle = device_handle(device_identifier(SECRET, CANONICAL_ADDRESS))
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        version=2,
        subentries_data=[
            {
                "data": {"handle": handle, "name": "Kept switch", "rockers": 2},
                "subentry_type": "switch",
                "title": "Kept switch",
                "unique_id": handle,
            }
        ],
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.enocean_ptm216b.IntegrationSecretStore.async_get_or_create",
        AsyncMock(return_value=SECRET),
    ):
        await _setup_entry_directly(hass, entry)

    switch = entry.runtime_data.commissioning_store.get(CANONICAL_ADDRESS)
    assert switch is not None
    assert switch.name == "Kept switch"


async def test_update_listener_reloads_exactly_once_per_call(hass):
    """One update-listener invocation triggers exactly one reload -- it must
    not itself re-trigger the listener (only entry/subentry *data* changes
    do that), or a subentry add/remove would loop reloads forever.
    """
    entry = MockConfigEntry(domain=DOMAIN, data={}, version=2)
    entry.add_to_hass(hass)

    with patch(
        "custom_components.enocean_ptm216b.IntegrationSecretStore.async_get_or_create",
        AsyncMock(return_value=SECRET),
    ):
        await _setup_entry_directly(hass, entry)

    assert len(entry.update_listeners) == 1

    with patch.object(
        hass.config_entries, "async_reload", AsyncMock(return_value=True)
    ) as reload:
        await entry.update_listeners[0](hass, entry)

    reload.assert_awaited_once_with(entry.entry_id)
