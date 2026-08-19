"""Tests for commissioning_input.py's parsing helpers and the SwitchSubentryFlow
Add-device wizard (config_flow.py).

All key/address/name material in this file is synthetic test data.
"""

from __future__ import annotations

from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.enocean_ptm216b.commissioning_input import (
    _parse_manual_address,
    _parse_manual_key,
    _parse_qr_payload,
    resolve_commissioning_input,
)
from custom_components.enocean_ptm216b.commissioning_store import CommissioningStore
from custom_components.enocean_ptm216b.config_flow import SwitchSubentryFlow
from custom_components.enocean_ptm216b.const import DOMAIN
from custom_components.enocean_ptm216b.identity import (
    canonicalize_address,
    device_identifier,
)
from custom_components.enocean_ptm216b.runtime_data import (
    CaptureState,
    DesignationOutcome,
    Ptm216bRuntimeData,
)

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
# resolve_commissioning_input: precedence
# ---------------------------------------------------------------------------


def test_resolve_prefers_qr_payload_over_manual_fields_when_both_present():
    user_input = {
        "qr_payload": f"30S{CANONICAL_ADDRESS}+Z{SYNTHETIC_KEY_HEX}",
        "address": OTHER_ADDRESS,
        "security_key": OTHER_KEY_HEX,
        "name": "Test",
    }

    address, key = resolve_commissioning_input(user_input)

    assert address == CANONICAL_ADDRESS
    assert key == SYNTHETIC_KEY


def test_resolve_falls_back_to_manual_fields_when_qr_absent():
    user_input = {
        "qr_payload": "",
        "address": ADDRESS,
        "security_key": SYNTHETIC_KEY_HEX,
        "name": "Test",
    }

    address, key = resolve_commissioning_input(user_input)

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

    address, key = resolve_commissioning_input(user_input)

    assert address == canonicalize_address(OTHER_ADDRESS)
    assert key == SYNTHETIC_KEY


def test_resolve_returns_none_none_when_nothing_parses():
    user_input = {"qr_payload": "", "address": "", "security_key": "", "name": "Test"}

    address, key = resolve_commissioning_input(user_input)

    assert address is None
    assert key is None


# ---------------------------------------------------------------------------
# SwitchSubentryFlow: the Add-device wizard
# ---------------------------------------------------------------------------


async def _entry_without_designation(hass) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={}, version=2)
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


def _subentry_flow_for(hass, entry: MockConfigEntry) -> SwitchSubentryFlow:
    """Build a flow instance without going through the real flow manager.

    Mirrors this repo's existing ``_reconfigure_flow_for`` convention (see
    git history): going through ``hass.config_entries.subentries.async_init``
    loads this integration's declared dependencies (``bluetooth`` ->
    ``bluetooth_adapters``), which requires ``dbus_fast`` and is unavailable
    outside Linux. Constructing the flow directly and setting only the
    attributes ``ConfigSubentryFlow``/``FlowHandler`` actually read
    (``hass``, ``context``, ``flow_id``, ``handler``) exercises the exact
    same step-handler logic without that unrelated dependency chain.
    """
    flow = SwitchSubentryFlow()
    flow.hass = hass
    flow.handler = (entry.entry_id, "switch")
    flow.flow_id = "test-subentry-flow-id"
    flow.context = {"source": "user"}
    return flow


async def test_user_step_shows_a_menu_of_detect_or_manual(hass):
    entry = await _entry_without_designation(hass)
    flow = _subentry_flow_for(hass, entry)

    result = await flow.async_step_user(None)

    assert result["type"] == "menu"
    assert set(result["menu_options"]) == {"detect", "key_entry_manual"}


async def test_key_entry_manual_shows_a_form(hass):
    entry = await _entry_without_designation(hass)
    flow = _subentry_flow_for(hass, entry)

    result = await flow.async_step_key_entry_manual(None)

    assert result["type"] == "form"
    assert result["step_id"] == "key_entry_manual"


async def test_key_entry_manual_shows_error_for_unparseable_input(hass):
    entry = await _entry_without_designation(hass)
    flow = _subentry_flow_for(hass, entry)

    result = await flow.async_step_key_entry_manual(
        {"qr_payload": "garbage text", "address": "", "security_key": "", "name": "X"}
    )

    assert result["type"] == "form"
    assert result["step_id"] == "key_entry_manual"
    assert result["errors"] == {"base": "invalid_commissioning_data"}
    assert "garbage text" not in repr(result)
    assert entry.runtime_data.commissioning_store.switches == {}


async def test_key_entry_manual_succeeds_via_manual_fields(hass):
    entry = await _entry_without_designation(hass)
    flow = _subentry_flow_for(hass, entry)

    result = await flow.async_step_key_entry_manual(
        {
            "qr_payload": "",
            "address": ADDRESS,
            "security_key": SYNTHETIC_KEY_HEX,
            "name": "Living room switch",
            "rockers": "2",
        }
    )

    assert result["type"] == "create_entry"
    assert result["title"] == "Living room switch"
    handle = entry.runtime_data.commissioned_device_handle(CANONICAL_ADDRESS)
    assert result["data"] == {
        "handle": handle,
        "name": "Living room switch",
        "rockers": 2,
    }
    assert result["unique_id"] == handle
    switch = entry.runtime_data.commissioning_store.get(CANONICAL_ADDRESS)
    assert switch is not None
    assert switch.key == SYNTHETIC_KEY
    assert switch.name == "Living room switch"
    assert switch.counter is None


async def test_key_entry_manual_succeeds_via_qr_payload(hass):
    entry = await _entry_without_designation(hass)
    flow = _subentry_flow_for(hass, entry)

    result = await flow.async_step_key_entry_manual(
        {
            "qr_payload": f"30S{CANONICAL_ADDRESS}+Z{SYNTHETIC_KEY_HEX}",
            "address": "",
            "security_key": "",
            "name": "QR switch",
            "rockers": "1",
        }
    )

    assert result["type"] == "create_entry"
    switch = entry.runtime_data.commissioning_store.get(CANONICAL_ADDRESS)
    assert switch is not None
    assert switch.name == "QR switch"
    assert result["data"]["rockers"] == 1


async def test_key_entry_manual_rejects_an_already_commissioned_address(hass):
    """Re-submitting an already-commissioned address must fail closed with
    a normal form error, not an unhandled exception from the flow manager's
    duplicate-unique_id check when the subentry is later added.
    """
    entry = await _entry_without_designation(hass)
    await entry.runtime_data.commissioning_store.async_add(
        CANONICAL_ADDRESS, SYNTHETIC_KEY, "Already commissioned"
    )
    flow = _subentry_flow_for(hass, entry)

    result = await flow.async_step_key_entry_manual(
        {
            "qr_payload": "",
            "address": ADDRESS,
            "security_key": SYNTHETIC_KEY_HEX,
            "name": "Duplicate attempt",
        }
    )

    assert result["type"] == "form"
    assert result["errors"] == {"base": "switch_already_commissioned"}
    switch = entry.runtime_data.commissioning_store.get(CANONICAL_ADDRESS)
    assert switch.name == "Already commissioned"


async def test_key_entry_manual_never_cross_checks_designation(hass):
    """Skipping detection means any parseable address/key commissions --
    there is nothing to cross-check against (see the "typo protection is
    off" warning in strings.json's key_entry_manual description).
    """
    entry = await _entry_without_designation(hass)
    flow = _subentry_flow_for(hass, entry)

    result = await flow.async_step_key_entry_manual(
        {
            "qr_payload": "",
            "address": OTHER_ADDRESS,
            "security_key": SYNTHETIC_KEY_HEX,
            "name": "Whatever switch",
        }
    )

    assert result["type"] == "create_entry"


async def test_key_entry_detected_aborts_on_designation_mismatch(hass):
    entry = await _entry_with_designation(hass)
    flow = _subentry_flow_for(hass, entry)

    result = await flow.async_step_key_entry_detected(
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


async def test_key_entry_detected_succeeds_when_address_matches(hass):
    entry = await _entry_with_designation(hass)
    flow = _subentry_flow_for(hass, entry)

    result = await flow.async_step_key_entry_detected(
        {
            "qr_payload": "",
            "address": ADDRESS,
            "security_key": SYNTHETIC_KEY_HEX,
            "name": "Matched switch",
        }
    )

    assert result["type"] == "create_entry"
    switch = entry.runtime_data.commissioning_store.get(CANONICAL_ADDRESS)
    assert switch is not None
    assert switch.name == "Matched switch"


async def test_key_entry_form_omits_qr_image_field_when_decoder_unavailable(hass):
    entry = await _entry_without_designation(hass)
    flow = _subentry_flow_for(hass, entry)

    with patch(
        "custom_components.enocean_ptm216b.config_flow.is_qr_decode_available",
        return_value=False,
    ):
        result = await flow.async_step_key_entry_manual(None)

    assert "qr_image" not in result["data_schema"].schema
    assert result["description_placeholders"]["qr_status"] != ""


async def test_key_entry_form_includes_qr_image_field_when_decoder_available(hass):
    entry = await _entry_without_designation(hass)
    flow = _subentry_flow_for(hass, entry)

    with patch(
        "custom_components.enocean_ptm216b.config_flow.is_qr_decode_available",
        return_value=True,
    ):
        result = await flow.async_step_key_entry_manual(None)

    assert "qr_image" in result["data_schema"].schema
    assert result["description_placeholders"]["qr_status"] == ""


# ---------------------------------------------------------------------------
# SwitchSubentryFlow: detection progress
# ---------------------------------------------------------------------------


async def test_detect_starts_baseline_and_shows_progress(hass):
    entry = await _entry_without_designation(hass)
    flow = _subentry_flow_for(hass, entry)

    result = await flow.async_step_detect(None)

    assert result["type"] == "progress"
    assert result["progress_action"] == "detect_baseline"
    assert entry.runtime_data.capture_state is CaptureState.BASELINE
    flow.async_remove()


async def test_detect_advances_to_key_entry_detected_on_selection(hass):
    entry = await _entry_without_designation(hass)
    flow = _subentry_flow_for(hass, entry)

    await flow.async_step_detect(None)
    entry.runtime_data.designation_outcome = DesignationOutcome.SELECTED
    entry.runtime_data.designated_identifier = device_identifier(
        SECRET, CANONICAL_ADDRESS
    )
    entry.runtime_data.capture_state = CaptureState.INERT

    result = await flow.async_step_detect(None)

    assert result["type"] == "form"
    assert result["step_id"] == "key_entry_detected"


async def test_detect_offers_retry_menu_on_no_selection(hass):
    entry = await _entry_without_designation(hass)
    flow = _subentry_flow_for(hass, entry)

    await flow.async_step_detect(None)
    entry.runtime_data.designation_outcome = DesignationOutcome.NO_SELECTION
    entry.runtime_data.capture_state = CaptureState.INERT

    result = await flow.async_step_detect(None)

    assert result["type"] == "menu"
    assert result["step_id"] == "detect_failed"
    assert set(result["menu_options"]) == {"detect", "key_entry_manual"}


async def test_detect_shows_new_progress_action_per_phase(hass):
    entry = await _entry_without_designation(hass)
    flow = _subentry_flow_for(hass, entry)

    await flow.async_step_detect(None)
    entry.runtime_data.capture_state = CaptureState.PRESS

    result = await flow.async_step_detect(None)

    assert result["type"] == "progress"
    assert result["progress_action"] == "detect_press"
    flow.async_remove()


async def test_async_remove_cancels_a_running_detection(hass):
    entry = await _entry_without_designation(hass)
    flow = _subentry_flow_for(hass, entry)

    await flow.async_step_detect(None)
    assert entry.runtime_data.capture_state is CaptureState.BASELINE

    flow.async_remove()

    assert entry.runtime_data.capture_state is CaptureState.INERT
