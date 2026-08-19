"""End-to-end tests for button_pipeline.py's fail-closed pipeline.

Synthetic telegrams are built with a valid MIC via tests/ccm_reference.py
(the same independent CCM oracle used in test_crypto.py), so these tests
exercise the pipeline exactly as a real, MIC-verified advertisement would.
All key/address material in this file is synthetic test data.
"""

from __future__ import annotations

import asyncio

import pytest

from custom_components.enocean_ptm216b import button_pipeline
from custom_components.enocean_ptm216b.commissioning_store import CommissioningStore
from custom_components.enocean_ptm216b.const import ENOCEAN_MANUFACTURER_ID
from custom_components.enocean_ptm216b.identity import canonicalize_address
from custom_components.enocean_ptm216b.runtime_data import Ptm216bRuntimeData
from custom_components.enocean_ptm216b.telegram import Button

from ccm_reference import ccm_encrypt_and_tag

SYNTHETIC_KEY = bytes(range(16))
ADDRESS = "AA:BB:CC:DD:EE:FF"
CANONICAL_ADDRESS = canonicalize_address(ADDRESS)
SECRET = b"\x01" * 32
PRESS_A0 = 0b00011
RELEASE_A0 = 0b00010
RESERVED_STATUS = 0b100000
_AAD_PREFIX = bytes([0x0C, 0xFF, 0xDA, 0x03])


def _mic_for(counter: int, status: int) -> bytes:
    address_bytes = bytes(reversed(bytes.fromhex(CANONICAL_ADDRESS)))
    counter_bytes = counter.to_bytes(4, "little")
    nonce = address_bytes + counter_bytes + bytes(3)
    aad = _AAD_PREFIX + counter_bytes + bytes([status])
    return ccm_encrypt_and_tag(SYNTHETIC_KEY, nonce, b"", aad, tag_length=4)


def _value_for(counter: int, status: int) -> bytes:
    counter_bytes = counter.to_bytes(4, "little")
    return counter_bytes + bytes([status]) + _mic_for(counter, status)


def _manufacturer_data(counter: int, status: int) -> dict[int, bytes]:
    return {ENOCEAN_MANUFACTURER_ID: _value_for(counter, status)}


async def _make_runtime(hass) -> tuple[Ptm216bRuntimeData, CommissioningStore]:
    store = CommissioningStore(hass)
    await store.async_load()
    await store.async_add(CANONICAL_ADDRESS, SYNTHETIC_KEY, "Test switch")
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET, commissioning_store=store)
    return runtime, store


# ---------------------------------------------------------------------------
# Uncommissioned / unparseable address: pure no-op
# ---------------------------------------------------------------------------


async def test_uncommissioned_address_is_a_complete_no_op(hass):
    runtime, _store = await _make_runtime(hass)
    calls: list = []

    button_pipeline.handle_advertisement(
        runtime, "11:22:33:44:55:66", _manufacturer_data(1, PRESS_A0), calls.append
    )

    assert calls == []
    assert runtime.commissioned_switches == {}


async def test_unparseable_address_is_a_complete_no_op(hass):
    runtime, _store = await _make_runtime(hass)
    calls: list = []

    button_pipeline.handle_advertisement(
        runtime, "not-an-address", _manufacturer_data(1, PRESS_A0), calls.append
    )

    assert calls == []


# ---------------------------------------------------------------------------
# First-trust initialization
# ---------------------------------------------------------------------------


async def test_first_trust_initializes_counter_without_verified_or_rejected(hass):
    runtime, store = await _make_runtime(hass)
    switch_runtime = runtime.commissioned_switch_runtime(CANONICAL_ADDRESS)
    events: list[bool] = []
    switch_runtime.set_event_listener(Button.A0, events.append)
    calls: list = []

    button_pipeline.handle_advertisement(
        runtime, ADDRESS, _manufacturer_data(5, PRESS_A0), calls.append
    )
    assert len(calls) == 1
    await calls[0]

    assert events == []
    assert switch_runtime.verified_count == 0
    assert switch_runtime.rejected_count == 0
    assert store.get_counter(CANONICAL_ADDRESS) == 5

    reloaded = CommissioningStore(hass)
    await reloaded.async_load()
    assert reloaded.get_counter(CANONICAL_ADDRESS) == 5


# ---------------------------------------------------------------------------
# Accepted telegram: durable-save-before-event ordering
# ---------------------------------------------------------------------------


async def test_accepted_telegram_fires_event_only_after_store_save_completes(hass):
    runtime, store = await _make_runtime(hass)
    store.set_counter(CANONICAL_ADDRESS, 5)
    await store.async_save()

    call_order: list[str] = []
    original_save = store.async_save

    async def tracked_save() -> None:
        await original_save()
        call_order.append("saved")

    store.async_save = tracked_save

    switch_runtime = runtime.commissioned_switch_runtime(CANONICAL_ADDRESS)
    events: list[bool] = []

    def _listener(is_press: bool) -> None:
        call_order.append("event_fired")
        events.append(is_press)

    switch_runtime.set_event_listener(Button.A0, _listener)

    calls: list = []
    button_pipeline.handle_advertisement(
        runtime, ADDRESS, _manufacturer_data(6, PRESS_A0), calls.append
    )
    await calls[0]

    assert call_order == ["saved", "event_fired"]
    assert events == [True]
    assert switch_runtime.verified_count == 1
    assert switch_runtime.rejected_count == 0
    assert store.get_counter(CANONICAL_ADDRESS) == 6


async def test_accepted_release_maps_to_is_press_false(hass):
    runtime, store = await _make_runtime(hass)
    store.set_counter(CANONICAL_ADDRESS, 5)
    await store.async_save()
    switch_runtime = runtime.commissioned_switch_runtime(CANONICAL_ADDRESS)
    events: list[bool] = []
    switch_runtime.set_event_listener(Button.A0, events.append)

    calls: list = []
    button_pipeline.handle_advertisement(
        runtime, ADDRESS, _manufacturer_data(6, RELEASE_A0), calls.append
    )
    await calls[0]

    assert events == [False]


# ---------------------------------------------------------------------------
# Duplicate / replay
# ---------------------------------------------------------------------------


async def test_duplicate_counter_is_rejected_and_fires_no_event(hass):
    runtime, store = await _make_runtime(hass)
    store.set_counter(CANONICAL_ADDRESS, 5)
    await store.async_save()
    switch_runtime = runtime.commissioned_switch_runtime(CANONICAL_ADDRESS)
    events: list[bool] = []
    switch_runtime.set_event_listener(Button.A0, events.append)

    calls: list = []
    button_pipeline.handle_advertisement(
        runtime, ADDRESS, _manufacturer_data(5, PRESS_A0), calls.append
    )
    await calls[0]

    assert events == []
    assert switch_runtime.rejected_count == 1
    assert switch_runtime.verified_count == 0
    assert store.get_counter(CANONICAL_ADDRESS) == 5


async def test_replay_rejected_counter_is_rejected_and_fires_no_event(hass):
    runtime, store = await _make_runtime(hass)
    store.set_counter(CANONICAL_ADDRESS, 5)
    await store.async_save()
    switch_runtime = runtime.commissioned_switch_runtime(CANONICAL_ADDRESS)
    events: list[bool] = []
    switch_runtime.set_event_listener(Button.A0, events.append)

    calls: list = []
    button_pipeline.handle_advertisement(
        runtime, ADDRESS, _manufacturer_data(4, PRESS_A0), calls.append
    )
    await calls[0]

    assert events == []
    assert switch_runtime.rejected_count == 1
    assert store.get_counter(CANONICAL_ADDRESS) == 5


# ---------------------------------------------------------------------------
# MIC-invalid: rejected synchronously, no async hop
# ---------------------------------------------------------------------------


async def test_mic_invalid_is_rejected_synchronously_without_an_async_hop(hass):
    runtime, _store = await _make_runtime(hass)
    switch_runtime = runtime.commissioned_switch_runtime(CANONICAL_ADDRESS)
    value = bytearray(_value_for(5, PRESS_A0))
    value[-1] ^= 0xFF  # corrupt the MIC's last byte
    calls: list = []

    button_pipeline.handle_advertisement(
        runtime, ADDRESS, {ENOCEAN_MANUFACTURER_ID: bytes(value)}, calls.append
    )

    assert calls == []  # no create_task call: proves this stayed synchronous
    assert switch_runtime.rejected_count == 1
    assert switch_runtime.verified_count == 0


# ---------------------------------------------------------------------------
# Shape rejection: every unsupported length, and a missing manufacturer key
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("length", [0, 1, 8, 10, 11, 12, 13, 24, 30])
async def test_unsupported_length_is_rejected_synchronously(hass, length):
    runtime, _store = await _make_runtime(hass)
    switch_runtime = runtime.commissioned_switch_runtime(CANONICAL_ADDRESS)
    calls: list = []

    button_pipeline.handle_advertisement(
        runtime, ADDRESS, {ENOCEAN_MANUFACTURER_ID: bytes(length)}, calls.append
    )

    assert calls == []
    assert switch_runtime.rejected_count == 1
    assert switch_runtime.verified_count == 0


async def test_missing_manufacturer_data_key_is_rejected_synchronously(hass):
    runtime, _store = await _make_runtime(hass)
    switch_runtime = runtime.commissioned_switch_runtime(CANONICAL_ADDRESS)
    calls: list = []

    button_pipeline.handle_advertisement(runtime, ADDRESS, {}, calls.append)

    assert calls == []
    assert switch_runtime.rejected_count == 1


# ---------------------------------------------------------------------------
# Status rejection: counter has already durably advanced, no event fires
# ---------------------------------------------------------------------------


async def test_status_reject_still_advances_counter_but_fires_no_event(hass):
    runtime, store = await _make_runtime(hass)
    store.set_counter(CANONICAL_ADDRESS, 5)
    await store.async_save()
    switch_runtime = runtime.commissioned_switch_runtime(CANONICAL_ADDRESS)
    events: list[bool] = []
    switch_runtime.set_event_listener(Button.A0, events.append)

    calls: list = []
    button_pipeline.handle_advertisement(
        runtime, ADDRESS, _manufacturer_data(6, RESERVED_STATUS), calls.append
    )
    await calls[0]

    assert events == []
    assert switch_runtime.verified_count == 0
    assert switch_runtime.rejected_count == 1
    # Deliberate, subtle ordering: the counter already durably advanced,
    # per the gate doc's fixed gate order (counter before status).
    assert store.get_counter(CANONICAL_ADDRESS) == 6
    reloaded = CommissioningStore(hass)
    await reloaded.async_load()
    assert reloaded.get_counter(CANONICAL_ADDRESS) == 6


# ---------------------------------------------------------------------------
# Concurrency: two simultaneous callbacks with the SAME counter
# ---------------------------------------------------------------------------


async def test_concurrent_same_counter_callbacks_serialize_to_one_accept(hass):
    runtime, store = await _make_runtime(hass)
    store.set_counter(CANONICAL_ADDRESS, 5)
    await store.async_save()
    switch_runtime = runtime.commissioned_switch_runtime(CANONICAL_ADDRESS)
    events: list[bool] = []
    switch_runtime.set_event_listener(Button.A0, events.append)

    calls: list = []
    button_pipeline.handle_advertisement(
        runtime, ADDRESS, _manufacturer_data(6, PRESS_A0), calls.append
    )
    button_pipeline.handle_advertisement(
        runtime, ADDRESS, _manufacturer_data(6, PRESS_A0), calls.append
    )
    assert len(calls) == 2

    await asyncio.gather(*calls)

    assert switch_runtime.verified_count == 1
    assert switch_runtime.rejected_count == 1
    assert events == [True]
    assert store.get_counter(CANONICAL_ADDRESS) == 6


# ---------------------------------------------------------------------------
# Restart persistence at the pipeline level
# ---------------------------------------------------------------------------


async def test_restart_recreates_store_and_keeps_counter_for_replay_decisions(hass):
    store1 = CommissioningStore(hass)
    await store1.async_load()
    await store1.async_add(CANONICAL_ADDRESS, SYNTHETIC_KEY, "Test switch")
    runtime1 = Ptm216bRuntimeData(_hmac_secret=SECRET, commissioning_store=store1)
    calls: list = []
    button_pipeline.handle_advertisement(
        runtime1, ADDRESS, _manufacturer_data(10, PRESS_A0), calls.append
    )
    await calls[0]
    assert store1.get_counter(CANONICAL_ADDRESS) == 10

    # Simulate a Home Assistant restart: fresh store and runtime objects,
    # loaded from the same underlying (test) Home Assistant Store.
    store2 = CommissioningStore(hass)
    await store2.async_load()
    runtime2 = Ptm216bRuntimeData(_hmac_secret=SECRET, commissioning_store=store2)
    switch_runtime2 = runtime2.commissioned_switch_runtime(CANONICAL_ADDRESS)

    calls2: list = []
    # A counter only valid pre-restart is correctly replay-rejected post-restart.
    button_pipeline.handle_advertisement(
        runtime2, ADDRESS, _manufacturer_data(10, PRESS_A0), calls2.append
    )
    await calls2[0]
    assert switch_runtime2.rejected_count == 1
    assert switch_runtime2.verified_count == 0

    calls3: list = []
    button_pipeline.handle_advertisement(
        runtime2, ADDRESS, _manufacturer_data(11, PRESS_A0), calls3.append
    )
    await calls3[0]
    assert switch_runtime2.verified_count == 1
    assert store2.get_counter(CANONICAL_ADDRESS) == 11
