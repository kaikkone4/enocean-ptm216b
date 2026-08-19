"""Tests for device_trigger.py: device triggers for short/long press, per
button, in the automation editor.

Devices are registered directly against the device registry (mirroring
exactly what ``event.py``'s ``DeviceInfo``/``async_add_entities`` produce in
production) rather than through a full config-entry setup, which would pull
in this integration's declared ``bluetooth_adapters``/``bluetooth``
dependency -- unavailable/dbus_fast-broken outside Linux, per this repo's
existing documented convention (see test_commissioning_flow.py's own notes
on the same limitation). ``async_get_device_automation_platform`` (used by
the ``automation`` component to load this module) only loads the
integration module and checks Python requirements -- it does not set up
component dependencies -- so the attach+fire roundtrip test below, which
goes through the real ``automation`` component, works the same on macOS and
Linux CI.

All address/key material in this file is synthetic test data.
"""

from __future__ import annotations

import pytest
import voluptuous as vol
from homeassistant.components import automation
from homeassistant.components.device_automation import InvalidDeviceAutomationConfig
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.enocean_ptm216b import device_trigger
from custom_components.enocean_ptm216b.const import DOMAIN, EVENT_ENOCEAN_PTM216B
from custom_components.enocean_ptm216b.identity import canonicalize_address
from custom_components.enocean_ptm216b.runtime_data import Ptm216bRuntimeData

SECRET = b"\x01" * 32
ADDRESS = "AA:BB:CC:DD:EE:FF"
CANONICAL_ADDRESS = canonicalize_address(ADDRESS)
OTHER_ADDRESS = "11:22:33:44:55:66"
OTHER_CANONICAL_ADDRESS = canonicalize_address(OTHER_ADDRESS)


def _handle_for(address: str) -> str:
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET)
    return runtime.commissioned_device_handle(address)


async def _switch_device(
    hass, *, address: str = CANONICAL_ADDRESS, rockers: int = 2, name: str = "Switch"
):
    """Register a config entry + "switch" subentry + device registry entry,
    exactly the shape ``async_get_triggers``/``async_validate_trigger_config``
    look up -- without going through the real Bluetooth-dependent setup.
    """
    handle = _handle_for(address)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        subentries_data=[
            {
                "data": {"handle": handle, "name": name, "rockers": rockers},
                "subentry_type": "switch",
                "title": name,
                "unique_id": handle,
            }
        ],
    )
    entry.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, handle)},
        name=name,
    )
    return entry, device


# ---------------------------------------------------------------------------
# async_get_triggers: rocker-count respect
# ---------------------------------------------------------------------------


async def test_async_get_triggers_offers_all_sixteen_for_a_two_rocker_switch(hass):
    _entry, device = await _switch_device(hass, rockers=2)

    triggers = await device_trigger.async_get_triggers(hass, device.id)

    assert len(triggers) == 16
    pairs = {(t["type"], t["subtype"]) for t in triggers}
    assert pairs == {
        (action, button)
        for action in ("press", "release", "short_press", "long_press")
        for button in ("A0", "A1", "B0", "B1")
    }
    assert all(t["platform"] == "device" for t in triggers)
    assert all(t["domain"] == DOMAIN for t in triggers)
    assert all(t["device_id"] == device.id for t in triggers)


async def test_async_get_triggers_offers_only_a0_a1_for_a_one_rocker_switch(hass):
    _entry, device = await _switch_device(hass, rockers=1)

    triggers = await device_trigger.async_get_triggers(hass, device.id)

    assert len(triggers) == 8
    subtypes = {t["subtype"] for t in triggers}
    assert subtypes == {"A0", "A1"}


async def test_async_get_triggers_returns_empty_for_an_unknown_device(hass):
    triggers = await device_trigger.async_get_triggers(hass, "not-a-real-device-id")

    assert triggers == []


async def test_async_get_triggers_returns_empty_when_no_subentry_matches(hass):
    """A device that carries this integration's identifier but whose handle
    matches no live subentry (e.g. the subentry was since removed) offers no
    triggers -- mirrors ``event.py``'s own entity-creation filtering.
    """
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "stale-handle")},
    )

    triggers = await device_trigger.async_get_triggers(hass, device.id)

    assert triggers == []


# ---------------------------------------------------------------------------
# async_validate_trigger_config
# ---------------------------------------------------------------------------


def _config(device_id: str, action: str, button: str) -> dict[str, object]:
    return {
        "platform": "device",
        "domain": DOMAIN,
        "device_id": device_id,
        "type": action,
        "subtype": button,
    }


async def test_async_validate_trigger_config_accepts_a_valid_trigger(hass):
    _entry, device = await _switch_device(hass, rockers=2)

    validated = await device_trigger.async_validate_trigger_config(
        hass, _config(device.id, "short_press", "B1")
    )

    assert validated["type"] == "short_press"
    assert validated["subtype"] == "B1"


async def test_async_validate_trigger_config_rejects_an_unknown_action_type(hass):
    _entry, device = await _switch_device(hass, rockers=2)

    with pytest.raises(vol.Invalid):
        await device_trigger.async_validate_trigger_config(
            hass, _config(device.id, "triple_press", "A0")
        )


async def test_async_validate_trigger_config_rejects_an_unknown_button_subtype(hass):
    _entry, device = await _switch_device(hass, rockers=2)

    with pytest.raises(vol.Invalid):
        await device_trigger.async_validate_trigger_config(
            hass, _config(device.id, "press", "C0")
        )


async def test_async_validate_trigger_config_rejects_b_button_on_one_rocker_switch(
    hass,
):
    _entry, device = await _switch_device(hass, rockers=1)

    with pytest.raises(InvalidDeviceAutomationConfig):
        await device_trigger.async_validate_trigger_config(
            hass, _config(device.id, "press", "B0")
        )


async def test_async_validate_trigger_config_rejects_an_unrecognized_device(hass):
    with pytest.raises(InvalidDeviceAutomationConfig):
        await device_trigger.async_validate_trigger_config(
            hass, _config("not-a-real-device-id", "press", "A0")
        )


# ---------------------------------------------------------------------------
# async_attach_trigger: roundtrip through the real event bus, via the real
# `automation` component (so this exercises the same code path a user's
# automation editor selection would).
# ---------------------------------------------------------------------------


def _fire(hass, device_id: str, button: str, action: str) -> None:
    hass.bus.async_fire(
        EVENT_ENOCEAN_PTM216B,
        {"device_id": device_id, "button": button, "action": action},
    )


async def _automation_for(hass, device_id: str, action: str, button: str) -> None:
    assert await async_setup_component(
        hass,
        automation.DOMAIN,
        {
            automation.DOMAIN: [
                {
                    "trigger": {
                        "platform": "device",
                        "domain": DOMAIN,
                        "device_id": device_id,
                        "type": action,
                        "subtype": button,
                    },
                    "action": {"action": "test.automation"},
                }
            ]
        },
    )


async def test_attach_trigger_fires_the_configured_automation_on_a_match(hass):
    _entry, device = await _switch_device(hass, rockers=2)
    calls = async_mock_service(hass, "test", "automation")
    await _automation_for(hass, device.id, "short_press", "A0")

    _fire(hass, device.id, "A0", "short_press")
    await hass.async_block_till_done()

    assert len(calls) == 1


async def test_attach_trigger_does_not_fire_for_a_different_button(hass):
    _entry, device = await _switch_device(hass, rockers=2)
    calls = async_mock_service(hass, "test", "automation")
    await _automation_for(hass, device.id, "short_press", "A0")

    _fire(hass, device.id, "A1", "short_press")
    await hass.async_block_till_done()

    assert len(calls) == 0


async def test_attach_trigger_does_not_fire_for_a_different_action(hass):
    _entry, device = await _switch_device(hass, rockers=2)
    calls = async_mock_service(hass, "test", "automation")
    await _automation_for(hass, device.id, "short_press", "A0")

    _fire(hass, device.id, "A0", "long_press")
    await hass.async_block_till_done()

    assert len(calls) == 0


async def test_attach_trigger_does_not_fire_for_a_different_device(hass):
    _entry1, device1 = await _switch_device(
        hass, address=CANONICAL_ADDRESS, rockers=2, name="Switch one"
    )
    _entry2, device2 = await _switch_device(
        hass, address=OTHER_CANONICAL_ADDRESS, rockers=2, name="Switch two"
    )
    calls = async_mock_service(hass, "test", "automation")
    await _automation_for(hass, device1.id, "short_press", "A0")

    _fire(hass, device2.id, "A0", "short_press")
    await hass.async_block_till_done()

    assert len(calls) == 0


async def test_attach_trigger_long_press_roundtrip(hass):
    _entry, device = await _switch_device(hass, rockers=1)
    calls = async_mock_service(hass, "test", "automation")
    await _automation_for(hass, device.id, "long_press", "A1")

    _fire(hass, device.id, "A1", "long_press")
    await hass.async_block_till_done()

    assert len(calls) == 1
