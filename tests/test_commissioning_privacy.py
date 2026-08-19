"""Privacy assertions for Phase 4/5A commissioning.

commissioning_store.py is a DELIBERATE, documented exception that persists
an address and a device-specific key -- see its module docstring. This file
proves everything else still holds: the pipeline never logs an address or
key, no repr() of any runtime object leaks one, and no entity state,
attribute, subentry data, or device-registry identifier exposes the
canonical address or key -- only the non-reversible HMAC device handle,
exactly like every other phase in this repo. In the style of
test_privacy.py and test_decoder_privacy.py. All key/address/name material
here is synthetic test data.
"""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.enocean_ptm216b import button_pipeline
from custom_components.enocean_ptm216b.commissioning_store import CommissioningStore
from custom_components.enocean_ptm216b.config_flow import SwitchSubentryFlow
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
from conftest import RecordingAddEntities

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


async def _commissioned_entry(
    hass, *, name: str = "Living room switch"
) -> MockConfigEntry:
    store = CommissioningStore(hass)
    await store.async_load()
    await store.async_add(CANONICAL_ADDRESS, SYNTHETIC_KEY, name)
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET, commissioning_store=store)
    handle = runtime.commissioned_device_handle(CANONICAL_ADDRESS)

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        subentries_data=[
            {
                "data": {"handle": handle, "name": name, "rockers": 2},
                "subentry_type": "switch",
                "title": name,
                "unique_id": handle,
            }
        ],
    )
    entry.runtime_data = runtime
    entry.add_to_hass(hass)
    return entry


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
    entry = await _commissioned_entry(hass)

    recorder = RecordingAddEntities()
    await async_setup_event_entry(hass, entry, recorder)

    assert recorder.added
    for entity in recorder.added:
        identifiers = entity.device_info["identifiers"]
        assert CANONICAL_ADDRESS not in str(identifiers)
        assert ADDRESS not in str(identifiers)
        ((_domain, handle),) = identifiers
        assert handle.startswith("test-")
        assert handle == f"test-{handle[5:]}"


async def test_subentry_data_never_carries_the_raw_address_or_key(hass):
    """A "switch" subentry's ``data`` holds only ``handle``/``name``/
    ``rockers`` -- never the canonical address or the security key. This is
    what makes it safe for subentry data to appear in diagnostics/frontend
    responses, unlike the private commissioning store.
    """
    entry = await _commissioned_entry(hass)

    (subentry,) = entry.subentries.values()
    serialized = repr(subentry.data)

    assert CANONICAL_ADDRESS not in serialized
    assert ADDRESS not in serialized
    assert SYNTHETIC_KEY.hex() not in serialized
    assert set(subentry.data) == {"handle", "name", "rockers"}


async def test_key_entry_form_errors_never_echo_submitted_material(hass):
    entry = await _commissioned_entry(hass, name="Other switch")
    flow = SwitchSubentryFlow()
    flow.hass = hass
    flow.handler = (entry.entry_id, "switch")
    flow.flow_id = "test-flow-id"
    flow.context = {"source": "user"}

    result = await flow.async_step_key_entry_manual(
        {
            "qr_payload": "super-secret-garbage-payload",
            "address": "",
            "security_key": "",
            "name": "X",
        }
    )

    assert result["type"] == "form"
    assert "super-secret-garbage-payload" not in repr(result)


async def test_commissioned_entity_state_and_attributes_never_leak_material(hass):
    entry = await _commissioned_entry(hass)

    events = RecordingAddEntities()
    sensors = RecordingAddEntities()
    await async_setup_event_entry(hass, entry, events)
    await async_setup_sensor_entry(hass, entry, sensors)

    commissioned_entities = [
        entity
        for entity in (*events.added, *sensors.added)
        if hasattr(entity, "_canonical_address")
    ]
    assert len(commissioned_entities) == 8  # 6 event entities + 2 diagnostic sensors
    for entity in commissioned_entities:
        serialized = repr((entity.unique_id, entity.device_info))
        assert CANONICAL_ADDRESS not in serialized
        assert ADDRESS not in serialized
        assert SYNTHETIC_KEY.hex() not in serialized
