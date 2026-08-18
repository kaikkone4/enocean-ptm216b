from unittest.mock import AsyncMock, Mock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.enocean_ptm216b.const import DOMAIN
from custom_components.enocean_ptm216b.identity import device_identifier

ADDRESS = "AA:BB:CC:DD:EE:FF"
OTHER_ADDRESS = "11:22:33:44:55:66"
RAW_PAYLOAD = b"private-manufacturer-payload"
SECRET = b"\x01" * 32


async def _setup_callback(hass):
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.enocean_ptm216b.bluetooth.async_register_callback",
            return_value=Mock(),
        ) as register_callback,
        patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()),
        patch(
            "custom_components.enocean_ptm216b.IntegrationSecretStore.async_get_or_create",
            AsyncMock(return_value=SECRET),
        ),
    ):
        from custom_components import enocean_ptm216b

        assert await enocean_ptm216b.async_setup_entry(hass, entry)
    return entry, register_callback.call_args.args[1]


@pytest.mark.asyncio
async def test_callback_does_not_collect_candidates_while_capture_is_inert(
    hass, caplog
):
    entry, advertisement_callback = await _setup_callback(hass)
    caplog.set_level("DEBUG")
    service_info = Mock(
        address=ADDRESS,
        manufacturer_data={0x03DA: RAW_PAYLOAD},
    )

    advertisement_callback(service_info, Mock())

    assert entry.runtime_data.designation_candidates == {}
    assert ADDRESS not in caplog.text
    assert repr(RAW_PAYLOAD) not in caplog.text
    assert ADDRESS not in repr(entry.runtime_data)
    assert repr(RAW_PAYLOAD) not in repr(entry.runtime_data)


@pytest.mark.asyncio
async def test_callback_aggregates_only_pseudonymous_candidates_during_capture(
    hass, caplog
):
    entry, advertisement_callback = await _setup_callback(hass)
    caplog.set_level("DEBUG")
    entry.runtime_data.start_designation_capture(Mock(return_value=Mock()))
    first = Mock(address=ADDRESS, manufacturer_data={0x03DA: RAW_PAYLOAD})
    second = Mock(address=OTHER_ADDRESS, manufacturer_data={0x03DA: b"other-private"})

    advertisement_callback(first, Mock())
    advertisement_callback(first, Mock())
    advertisement_callback(second, Mock())

    identifier = device_identifier(entry.runtime_data._hmac_secret, ADDRESS)
    other_identifier = device_identifier(entry.runtime_data._hmac_secret, OTHER_ADDRESS)
    assert set(entry.runtime_data.designation_candidates) == {
        identifier,
        other_identifier,
    }
    candidate = entry.runtime_data.designation_candidates[identifier]
    assert candidate.observation_count == 2
    assert vars(candidate) == {"observation_count": 2}
    assert len(identifier) == 64
    assert ADDRESS not in repr(entry.runtime_data)
    assert repr(RAW_PAYLOAD) not in repr(entry.runtime_data)
    assert ADDRESS not in caplog.text
    assert repr(RAW_PAYLOAD) not in caplog.text
    assert identifier not in caplog.text
    assert repr(SECRET) not in caplog.text
    assert entry.data == {}
