from unittest.mock import AsyncMock, Mock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.enocean_ptm216b.const import DOMAIN


@pytest.mark.asyncio
async def test_setup_registers_passive_enocean_advertisement_callback(hass, caplog):
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.enocean_ptm216b.bluetooth.async_register_callback",
            return_value=Mock(),
        ) as register_callback,
        patch.object(
            hass.config_entries, "async_forward_entry_setups", AsyncMock()
        ) as forward_setups,
    ):
        from custom_components import enocean_ptm216b

        assert await enocean_ptm216b.async_setup_entry(hass, entry)

    forward_setups.assert_awaited_once_with(entry, ["sensor"])
    callback = register_callback.call_args.args[1]
    caplog.set_level("DEBUG")
    service_info = Mock(address="AA:BB:CC:DD:EE:FF")
    sensor = Mock()
    entry.runtime_data.sensor = sensor
    callback(service_info, Mock())
    assert entry.runtime_data.advertisement_count == 1
    sensor.async_write_ha_state.assert_called_once()
    assert "AA:BB:CC:DD:EE:FF" not in caplog.text

    register_callback.assert_called_once()
    _, _, matcher, mode = register_callback.call_args.args
    assert matcher == {"manufacturer_id": 0x03DA, "connectable": False}
    assert mode.name == "PASSIVE"
