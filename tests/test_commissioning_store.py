"""Tests for commissioning_store.py: load/save, counter accessors, privacy.

All key/address/name material in this file is synthetic test data.
"""

from __future__ import annotations

import pytest

from custom_components.enocean_ptm216b.commissioning_store import (
    CommissioningKeyLengthError,
    CommissioningStore,
)

SYNTHETIC_KEY = bytes(range(16))
OTHER_KEY = bytes(range(16, 32))
ADDRESS = "AABBCCDDEEFF"
OTHER_ADDRESS = "112233445566"
KEY_MARKER = b"private-secret-marker"


async def test_switches_is_empty_before_and_after_loading_nothing_stored(hass):
    store = CommissioningStore(hass)

    assert store.switches == {}

    await store.async_load()

    assert store.switches == {}
    assert store.get(ADDRESS) is None
    assert store.get_counter(ADDRESS) is None


async def test_async_add_caches_and_persists_a_new_switch(hass):
    store = CommissioningStore(hass)
    await store.async_load()

    await store.async_add(ADDRESS, SYNTHETIC_KEY, "Living room switch")

    switch = store.get(ADDRESS)
    assert switch is not None
    assert switch.key == SYNTHETIC_KEY
    assert switch.name == "Living room switch"
    assert switch.counter is None
    assert store.get_counter(ADDRESS) is None


async def test_async_add_rejects_a_key_that_is_not_sixteen_bytes(hass):
    store = CommissioningStore(hass)
    await store.async_load()

    with pytest.raises(CommissioningKeyLengthError) as excinfo:
        await store.async_add(ADDRESS, KEY_MARKER, "Bad key switch")

    assert excinfo.value.length == len(KEY_MARKER)
    assert store.get(ADDRESS) is None


async def test_async_remove_discards_and_persists(hass):
    store = CommissioningStore(hass)
    await store.async_load()
    await store.async_add(ADDRESS, SYNTHETIC_KEY, "Living room switch")

    await store.async_remove(ADDRESS)

    assert store.get(ADDRESS) is None
    assert store.switches == {}

    reloaded = CommissioningStore(hass)
    await reloaded.async_load()
    assert reloaded.switches == {}


async def test_set_counter_updates_cache_only_until_async_save(hass):
    store = CommissioningStore(hass)
    await store.async_load()
    await store.async_add(ADDRESS, SYNTHETIC_KEY, "Living room switch")

    store.set_counter(ADDRESS, 7)

    assert store.get_counter(ADDRESS) == 7
    # Not yet durable: a fresh store instance still sees the old (None) value.
    reloaded = CommissioningStore(hass)
    await reloaded.async_load()
    assert reloaded.get_counter(ADDRESS) is None


async def test_async_save_makes_the_counter_advance_durable(hass):
    store = CommissioningStore(hass)
    await store.async_load()
    await store.async_add(ADDRESS, SYNTHETIC_KEY, "Living room switch")
    store.set_counter(ADDRESS, 42)

    await store.async_save()

    reloaded = CommissioningStore(hass)
    await reloaded.async_load()
    assert reloaded.get_counter(ADDRESS) == 42
    assert reloaded.get(ADDRESS).key == SYNTHETIC_KEY
    assert reloaded.get(ADDRESS).name == "Living room switch"


async def test_multiple_switches_persist_independently(hass):
    store = CommissioningStore(hass)
    await store.async_load()
    await store.async_add(ADDRESS, SYNTHETIC_KEY, "First")
    await store.async_add(OTHER_ADDRESS, OTHER_KEY, "Second")
    store.set_counter(ADDRESS, 3)
    await store.async_save()

    reloaded = CommissioningStore(hass)
    await reloaded.async_load()

    assert set(reloaded.switches) == {ADDRESS, OTHER_ADDRESS}
    assert reloaded.get_counter(ADDRESS) == 3
    assert reloaded.get_counter(OTHER_ADDRESS) is None
    assert reloaded.get(OTHER_ADDRESS).key == OTHER_KEY


async def test_store_private_flag_matches_secret_store_convention(hass):
    store = CommissioningStore(hass)

    assert store._store._private is True


def test_commissioning_key_length_error_never_leaks_key_bytes():
    with pytest.raises(CommissioningKeyLengthError) as excinfo:
        raise CommissioningKeyLengthError(len(KEY_MARKER))

    serialized = repr(excinfo.value) + str(excinfo.value)
    assert KEY_MARKER.decode("latin-1") not in serialized
    assert KEY_MARKER.hex() not in serialized


async def test_commissioned_switch_repr_never_leaks_key_or_counter(hass):
    store = CommissioningStore(hass)
    await store.async_load()
    await store.async_add(ADDRESS, SYNTHETIC_KEY, "Living room switch")
    store.set_counter(ADDRESS, 999)

    switch = store.get(ADDRESS)
    serialized = repr(switch)

    assert SYNTHETIC_KEY.hex() not in serialized
    assert repr(SYNTHETIC_KEY) not in serialized
    assert "999" not in serialized


async def test_commissioning_store_repr_never_leaks_address_or_key(hass):
    store = CommissioningStore(hass)
    await store.async_load()
    await store.async_add(ADDRESS, SYNTHETIC_KEY, "Living room switch")

    serialized = repr(store)

    assert ADDRESS not in serialized
    assert SYNTHETIC_KEY.hex() not in serialized
