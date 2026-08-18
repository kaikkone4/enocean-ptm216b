from unittest.mock import AsyncMock, Mock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components import enocean_ptm216b
from custom_components.enocean_ptm216b.const import DOMAIN
from custom_components.enocean_ptm216b.runtime_data import (
    CaptureState,
    Ptm216bRuntimeData,
)


@pytest.mark.asyncio
async def test_unload_unloads_sensor_platform(hass):
    entry = MockConfigEntry(domain=DOMAIN)
    cancel_timer = Mock()
    entry.runtime_data = Ptm216bRuntimeData(_hmac_secret=b"\x01" * 32)
    entry.runtime_data.start_designation_capture(Mock(return_value=cancel_timer))
    entry.runtime_data.designation_candidates.add("local-pseudonym")
    entry.add_to_hass(hass)
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

    assert await enocean_ptm216b.async_unload_entry(hass, entry)
    hass.config_entries.async_unload_platforms.assert_awaited_once_with(
        entry, ["sensor"]
    )
    cancel_timer.assert_called_once_with()
    assert entry.runtime_data.capture_state is CaptureState.INERT
    assert entry.runtime_data.designation_candidates == set()
    assert entry.runtime_data.capture_timer is None
