"""Privacy assertions for Phase 4 commissioning.

commissioning_store.py is a DELIBERATE, documented exception that persists
an address and a device-specific key -- see its module docstring. This file
proves everything else still holds: the pipeline never logs an address or
key, no repr() of any runtime object leaks one, and no entity state,
attribute, or device-registry identifier exposes the canonical address or
key -- only the non-reversible HMAC device handle, exactly like every other
phase in this repo. In the style of test_privacy.py and
test_decoder_privacy.py. All key/address/name material here is synthetic
test data.
"""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.enocean_ptm216b import button_pipeline
from custom_components.enocean_ptm216b.commissioning_store import CommissioningStore
from custom_components.enocean_ptm216b.config_flow import ConfigFlow
from custom_components.enocean_ptm216b.const import DOMAIN, ENOCEAN_MANUFACTURER_ID
from custom_components.enocean_ptm216b.event import (
    async_setup_entry as async_setup_event_entry,
)
from custom_components.enocean_ptm216b.identity import canonicalize_address
from custom_components.enocean_ptm216b.runtime_data import Ptm216bRuntimeData
from custom_components.enocean_ptm216b.sensor import (
    async_setup_entry as async_setup_sensor_entry,
)

from ccm_reference import ccm_encrypt_and_tag

KEY_MARKER = b"private-secret-marker"  # distinctive marker bytes, never a real key
SYNTHETIC_KEY = (KEY_MARKER * 2)[:16]
ADDRESS = "AA:BB:CC:DD:EE:FF"
CANONICAL_ADDRESS = canonicalize_address(ADDRESS)
SECRET = b"\x01" * 32
_AAD_PREFIX = bytes([0x0C, 0xFF, 0xDA, 0x03])


def _valid_value(counter: int, status: int) -> bytes:
    address_bytes = bytes(reversed(bytes.fromhex(CANONICAL_ADDRESS)))
    counter_bytes = counter.to_bytes(4, "little")
    nonce = address_bytes + counter_bytes + bytes(3)
    aad = _AAD_PREFIX + counter_bytes + bytes([status])
    mic = ccm_encrypt_and_tag(SYNTHETIC_KEY, nonce, b"", aad, tag_length=4)
    return counter_bytes + bytes([status]) + mic


async def test_pipeline_never_logs_address_or_key(hass, caplog):
    caplog.set_level("DEBUG", logger="custom_components.enocean_ptm216b")
    store = CommissioningStore(hass)
    await store.async_load()
    await store.async_add(CANONICAL_ADDRESS, SYNTHETIC_KEY, "Living room switch")
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET, commissioning_store=store)
    calls: list = []

    button_pipeline.handle_advertisement(
        runtime,
        ADDRESS,
        {ENOCEAN_MANUFACTURER_ID: _valid_value(1, 0b00011)},
        calls.append,
    )
    await calls[0]

    # Scoped to this integration's own logger records: the test-only mock
    # Store in pytest_homeassistant_custom_component logs the raw data it
    # writes at DEBUG for test-debugging convenience -- real Home
    # Assistant's Store (homeassistant/helpers/storage.py) only ever logs
    # the storage key and file path, never the data content. That
    # harness-only log line is not a leak from this integration's own code,
    # so it must be excluded here rather than asserted against.
    our_text = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name.startswith("custom_components.enocean_ptm216b")
    )
    assert ADDRESS not in our_text
    assert CANONICAL_ADDRESS not in our_text
    assert SYNTHETIC_KEY.hex() not in our_text
    assert KEY_MARKER.decode("latin-1") not in our_text


async def test_commissioning_store_and_runtime_repr_never_leak_material(hass):
    store = CommissioningStore(hass)
    await store.async_load()
    await store.async_add(CANONICAL_ADDRESS, SYNTHETIC_KEY, "Living room switch")
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET, commissioning_store=store)
    runtime.commissioned_switch_runtime(CANONICAL_ADDRESS)

    serialized = repr(store) + repr(runtime) + repr(store.get(CANONICAL_ADDRESS))

    assert CANONICAL_ADDRESS not in serialized
    assert ADDRESS not in serialized
    assert SYNTHETIC_KEY.hex() not in serialized
    assert KEY_MARKER.decode("latin-1") not in serialized
    assert repr(SECRET) not in serialized


async def test_device_registry_identifier_is_the_non_reversible_handle(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    store = CommissioningStore(hass)
    await store.async_load()
    await store.async_add(CANONICAL_ADDRESS, SYNTHETIC_KEY, "Living room switch")
    entry.runtime_data = Ptm216bRuntimeData(
        _hmac_secret=SECRET, commissioning_store=store
    )
    entry.add_to_hass(hass)

    added: list = []
    await async_setup_event_entry(hass, entry, added.extend)

    assert added
    for entity in added:
        identifiers = entity.device_info["identifiers"]
        assert CANONICAL_ADDRESS not in str(identifiers)
        assert ADDRESS not in str(identifiers)
        ((_domain, handle),) = identifiers
        assert handle.startswith("test-")
        assert handle == f"test-{handle[5:]}"


async def test_decommission_form_options_never_expose_the_raw_address(hass):
    """The decommission selector's option `value` is serialized to the
    frontend as part of the form response, just like `label` -- so the
    canonical address must not appear there either, even though only the
    name (`label`) is ever rendered as visible text. Only the non-reversible
    device handle may serve as the option `value`.
    """
    entry = MockConfigEntry(domain=DOMAIN, data={})
    store = CommissioningStore(hass)
    await store.async_load()
    await store.async_add(CANONICAL_ADDRESS, SYNTHETIC_KEY, "Living room switch")
    entry.runtime_data = Ptm216bRuntimeData(
        _hmac_secret=SECRET, commissioning_store=store
    )
    entry.add_to_hass(hass)

    flow = ConfigFlow()
    flow.hass = hass
    flow.handler = DOMAIN
    flow.flow_id = "test-flow-id"
    flow.context = {"source": "reconfigure", "entry_id": entry.entry_id}

    result = await flow.async_step_decommission_switch(None)

    assert result["type"] == "form"
    selectors = [
        field
        for field in result["data_schema"].schema.values()
        if hasattr(field, "serialize")
    ]
    assert selectors
    serialized_options = repr([selector.serialize() for selector in selectors])

    assert CANONICAL_ADDRESS not in serialized_options
    assert ADDRESS not in serialized_options
    handle = entry.runtime_data.commissioned_device_handle(CANONICAL_ADDRESS)
    assert handle in serialized_options
    assert "Living room switch" in serialized_options


async def test_commissioned_entity_state_and_attributes_never_leak_material(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    store = CommissioningStore(hass)
    await store.async_load()
    await store.async_add(CANONICAL_ADDRESS, SYNTHETIC_KEY, "Living room switch")
    entry.runtime_data = Ptm216bRuntimeData(
        _hmac_secret=SECRET, commissioning_store=store
    )
    entry.add_to_hass(hass)

    events: list = []
    sensors: list = []
    await async_setup_event_entry(hass, entry, events.extend)
    await async_setup_sensor_entry(hass, entry, sensors.extend)

    commissioned_entities = [
        entity
        for entity in (*events, *sensors)
        if hasattr(entity, "_canonical_address")
    ]
    assert len(commissioned_entities) == 6  # 4 event entities + 2 diagnostic sensors
    for entity in commissioned_entities:
        serialized = repr((entity.unique_id, entity.device_info))
        assert CANONICAL_ADDRESS not in serialized
        assert ADDRESS not in serialized
        assert SYNTHETIC_KEY.hex() not in serialized
