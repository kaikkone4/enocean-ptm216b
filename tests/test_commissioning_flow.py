"""Tests for the commission_switch/decommission_switch config-flow steps.

All key/address/name material in this file is synthetic test data.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import homeassistant.helpers.device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.enocean_ptm216b.commissioning_store import CommissioningStore
from custom_components.enocean_ptm216b.config_flow import (
    ConfigFlow,
    _parse_manual_address,
    _parse_manual_key,
    _parse_qr_payload,
    _resolve_commissioning_input,
)
from custom_components.enocean_ptm216b.const import DOMAIN
from custom_components.enocean_ptm216b.identity import (
    canonicalize_address,
    device_identifier,
)
from custom_components.enocean_ptm216b.runtime_data import Ptm216bRuntimeData

SECRET = b"\x01" * 32
ADDRESS = "AA:BB:CC:DD:EE:FF"
CANONICAL_ADDRESS = canonicalize_address(ADDRESS)
OTHER_ADDRESS = "11:22:33:44:55:66"
SYNTHETIC_KEY = bytes(range(16))
SYNTHETIC_KEY_HEX = SYNTHETIC_KEY.hex()
OTHER_KEY_HEX = bytes(range(16, 32)).hex()


# ---------------------------------------------------------------------------
# _parse_qr_payload: token extraction
# ---------------------------------------------------------------------------


def test_parse_qr_payload_extracts_address_and_key_plus_separated():
    payload = f"30S{CANONICAL_ADDRESS}+Z{SYNTHETIC_KEY_HEX}"

    address, key = _parse_qr_payload(payload)

    assert address == CANONICAL_ADDRESS
    assert key == SYNTHETIC_KEY


def test_parse_qr_payload_extracts_address_and_key_whitespace_separated():
    payload = f"30S{CANONICAL_ADDRESS} Z{SYNTHETIC_KEY_HEX}"

    address, key = _parse_qr_payload(payload)

    assert address == CANONICAL_ADDRESS
    assert key == SYNTHETIC_KEY


def test_parse_qr_payload_is_case_insensitive():
    payload = f"30s{CANONICAL_ADDRESS.lower()}+z{SYNTHETIC_KEY_HEX.upper()}"

    address, key = _parse_qr_payload(payload)

    assert address == CANONICAL_ADDRESS
    assert key == SYNTHETIC_KEY


def test_parse_qr_payload_returns_none_none_for_unrelated_text():
    address, key = _parse_qr_payload("not a valid payload at all")

    assert address is None
    assert key is None


def test_parse_qr_payload_returns_partial_result_when_only_address_present():
    address, key = _parse_qr_payload(f"30S{CANONICAL_ADDRESS}")

    assert address == CANONICAL_ADDRESS
    assert key is None


def test_parse_qr_payload_returns_partial_result_when_only_key_present():
    address, key = _parse_qr_payload(f"Z{SYNTHETIC_KEY_HEX}")

    assert address is None
    assert key == SYNTHETIC_KEY


# ---------------------------------------------------------------------------
# manual fallback parsing
# ---------------------------------------------------------------------------


def test_parse_manual_address_accepts_colon_form():
    assert _parse_manual_address("AA:BB:CC:DD:EE:FF") == CANONICAL_ADDRESS


def test_parse_manual_address_accepts_plain_hex_form():
    assert _parse_manual_address(CANONICAL_ADDRESS.lower()) == CANONICAL_ADDRESS


def test_parse_manual_address_rejects_invalid_text():
    assert _parse_manual_address("not-an-address") is None


def test_parse_manual_address_rejects_empty_text():
    assert _parse_manual_address("   ") is None


def test_parse_manual_key_accepts_case_insensitive_32_hex():
    assert _parse_manual_key(SYNTHETIC_KEY_HEX.upper()) == SYNTHETIC_KEY


def test_parse_manual_key_rejects_wrong_length():
    assert _parse_manual_key(SYNTHETIC_KEY_HEX[:-2]) is None


def test_parse_manual_key_rejects_non_hex_characters():
    assert _parse_manual_key("g" * 32) is None


# ---------------------------------------------------------------------------
# _resolve_commissioning_input: precedence
# ---------------------------------------------------------------------------


def test_resolve_prefers_qr_payload_over_manual_fields_when_both_present():
    user_input = {
        "qr_payload": f"30S{CANONICAL_ADDRESS}+Z{SYNTHETIC_KEY_HEX}",
        "address": OTHER_ADDRESS,
        "security_key": OTHER_KEY_HEX,
        "name": "Test",
    }

    address, key = _resolve_commissioning_input(user_input)

    assert address == CANONICAL_ADDRESS
    assert key == SYNTHETIC_KEY


def test_resolve_falls_back_to_manual_fields_when_qr_absent():
    user_input = {
        "qr_payload": "",
        "address": ADDRESS,
        "security_key": SYNTHETIC_KEY_HEX,
        "name": "Test",
    }

    address, key = _resolve_commissioning_input(user_input)

    assert address == CANONICAL_ADDRESS
    assert key == SYNTHETIC_KEY


def test_resolve_falls_back_to_manual_fields_without_merging_partial_qr():
    """QR yields only an address; manual fields are used as a whole, not
    merged with the QR-derived address.
    """
    user_input = {
        "qr_payload": f"30S{CANONICAL_ADDRESS}",
        "address": OTHER_ADDRESS,
        "security_key": SYNTHETIC_KEY_HEX,
        "name": "Test",
    }

    address, key = _resolve_commissioning_input(user_input)

    assert address == canonicalize_address(OTHER_ADDRESS)
    assert key == SYNTHETIC_KEY


def test_resolve_returns_none_none_when_nothing_parses():
    user_input = {"qr_payload": "", "address": "", "security_key": "", "name": "Test"}

    address, key = _resolve_commissioning_input(user_input)

    assert address is None
    assert key is None


# ---------------------------------------------------------------------------
# full config-flow: commission_switch
# ---------------------------------------------------------------------------


async def _entry_without_designation(hass) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={})
    store = CommissioningStore(hass)
    await store.async_load()
    entry.runtime_data = Ptm216bRuntimeData(
        _hmac_secret=SECRET, commissioning_store=store
    )
    entry.add_to_hass(hass)
    return entry


async def _entry_with_designation(hass) -> MockConfigEntry:
    entry = await _entry_without_designation(hass)
    entry.runtime_data.designated_identifier = device_identifier(
        SECRET, CANONICAL_ADDRESS
    )
    return entry


def _reconfigure_flow_for(hass, entry: MockConfigEntry) -> ConfigFlow:
    """Build a flow instance for one entry without going through the real
    ``hass.config_entries.flow.async_init`` integration-loading path.

    That path loads this integration's declared dependencies (``bluetooth``
    -> ``bluetooth_adapters``), which requires ``dbus_fast`` and is
    unavailable outside Linux -- the very reason the pre-existing
    ``test_config_flow.py`` full-flow tests are macOS-environmental
    failures. Constructing the flow directly and setting only the
    attributes ``ConfigFlow``/``FlowHandler`` actually read
    (``hass``, ``context``, ``flow_id``, ``handler``) exercises the exact
    same step-handler logic without that unrelated dependency chain.
    """
    flow = ConfigFlow()
    flow.hass = hass
    flow.handler = DOMAIN
    flow.flow_id = "test-flow-id"
    flow.context = {"source": "reconfigure", "entry_id": entry.entry_id}
    return flow


async def test_commission_switch_aborts_without_a_designated_device(hass):
    entry = await _entry_without_designation(hass)
    flow = _reconfigure_flow_for(hass, entry)

    result = await flow.async_step_commission_switch(None)

    assert result["type"] == "abort"
    assert result["reason"] == "no_designated_device"


async def test_commission_switch_shows_form_when_designated(hass):
    entry = await _entry_with_designation(hass)
    flow = _reconfigure_flow_for(hass, entry)

    result = await flow.async_step_commission_switch(None)

    assert result["type"] == "form"
    assert result["step_id"] == "commission_switch"


async def test_commission_switch_shows_error_for_unparseable_input(hass):
    entry = await _entry_with_designation(hass)
    flow = _reconfigure_flow_for(hass, entry)

    result = await flow.async_step_commission_switch(
        {"qr_payload": "garbage text", "address": "", "security_key": "", "name": "X"}
    )

    assert result["type"] == "form"
    assert result["step_id"] == "commission_switch"
    assert result["errors"] == {"base": "invalid_commissioning_data"}
    assert "garbage text" not in repr(result)
    assert entry.runtime_data.commissioning_store.switches == {}


async def test_commission_switch_aborts_on_designation_mismatch(hass):
    entry = await _entry_with_designation(hass)
    flow = _reconfigure_flow_for(hass, entry)

    result = await flow.async_step_commission_switch(
        {
            "qr_payload": "",
            "address": OTHER_ADDRESS,
            "security_key": SYNTHETIC_KEY_HEX,
            "name": "Wrong switch",
        }
    )

    assert result["type"] == "abort"
    assert result["reason"] == "designation_mismatch"
    assert entry.runtime_data.commissioning_store.switches == {}


async def test_commission_switch_succeeds_via_manual_fields_and_reloads(hass):
    entry = await _entry_with_designation(hass)
    flow = _reconfigure_flow_for(hass, entry)

    with patch.object(
        hass.config_entries, "async_reload", AsyncMock(return_value=True)
    ) as reload:
        result = await flow.async_step_commission_switch(
            {
                "qr_payload": "",
                "address": ADDRESS,
                "security_key": SYNTHETIC_KEY_HEX,
                "name": "Living room switch",
            }
        )

    assert result["type"] == "abort"
    assert result["reason"] == "commissioning_complete"
    reload.assert_awaited_once_with(entry.entry_id)
    switch = entry.runtime_data.commissioning_store.get(CANONICAL_ADDRESS)
    assert switch is not None
    assert switch.key == SYNTHETIC_KEY
    assert switch.name == "Living room switch"
    assert switch.counter is None


async def test_commission_switch_succeeds_via_qr_payload(hass):
    entry = await _entry_with_designation(hass)
    flow = _reconfigure_flow_for(hass, entry)

    with patch.object(
        hass.config_entries, "async_reload", AsyncMock(return_value=True)
    ):
        result = await flow.async_step_commission_switch(
            {
                "qr_payload": f"30S{CANONICAL_ADDRESS}+Z{SYNTHETIC_KEY_HEX}",
                "address": "",
                "security_key": "",
                "name": "QR switch",
            }
        )

    assert result["type"] == "abort"
    assert result["reason"] == "commissioning_complete"
    switch = entry.runtime_data.commissioning_store.get(CANONICAL_ADDRESS)
    assert switch is not None
    assert switch.name == "QR switch"


# ---------------------------------------------------------------------------
# full config-flow: decommission_switch
# ---------------------------------------------------------------------------


async def test_decommission_switch_aborts_when_none_commissioned(hass):
    entry = await _entry_without_designation(hass)
    flow = _reconfigure_flow_for(hass, entry)

    result = await flow.async_step_decommission_switch(None)

    assert result["type"] == "abort"
    assert result["reason"] == "no_commissioned_devices"


async def test_decommission_switch_shows_a_form_of_names(hass):
    entry = await _entry_without_designation(hass)
    await entry.runtime_data.commissioning_store.async_add(
        CANONICAL_ADDRESS, SYNTHETIC_KEY, "Living room switch"
    )
    flow = _reconfigure_flow_for(hass, entry)

    result = await flow.async_step_decommission_switch(None)

    assert result["type"] == "form"
    assert result["step_id"] == "decommission_switch"


async def test_decommission_switch_options_never_carry_the_raw_address(hass):
    """The select's option `value` must be the non-reversible device handle,
    never the canonical address -- unlike the visible `label` (the switch's
    name), a selector's `value` is still serialized to the frontend as part
    of the form response, so the address must never appear there either.
    """
    entry = await _entry_without_designation(hass)
    await entry.runtime_data.commissioning_store.async_add(
        CANONICAL_ADDRESS, SYNTHETIC_KEY, "Living room switch"
    )
    handle = entry.runtime_data.commissioned_device_handle(CANONICAL_ADDRESS)
    flow = _reconfigure_flow_for(hass, entry)

    result = await flow.async_step_decommission_switch(None)

    (select_selector,) = result["data_schema"].schema.values()
    options = select_selector.config["options"]
    assert options == [{"value": handle, "label": "Living room switch"}]
    serialized = repr(options)
    assert CANONICAL_ADDRESS not in serialized
    assert ADDRESS not in serialized


async def test_decommission_switch_removes_store_entry_and_device_then_reloads(hass):
    entry = await _entry_without_designation(hass)
    store = entry.runtime_data.commissioning_store
    await store.async_add(CANONICAL_ADDRESS, SYNTHETIC_KEY, "Living room switch")
    handle = entry.runtime_data.commissioned_device_handle(CANONICAL_ADDRESS)
    registry = dr.async_get(hass)
    registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, handle)},
        name="Living room switch",
        manufacturer="EnOcean",
        model="PTM 216B",
    )
    flow = _reconfigure_flow_for(hass, entry)

    with patch.object(
        hass.config_entries, "async_reload", AsyncMock(return_value=True)
    ) as reload:
        # The submitted "switch" value is the non-reversible device handle
        # (what the selector's option `value` now carries), never the raw
        # canonical address.
        result = await flow.async_step_decommission_switch({"switch": handle})

    assert result["type"] == "abort"
    assert result["reason"] == "decommissioning_complete"
    reload.assert_awaited_once_with(entry.entry_id)
    assert store.get(CANONICAL_ADDRESS) is None
    assert registry.async_get_device(identifiers={(DOMAIN, handle)}) is None
