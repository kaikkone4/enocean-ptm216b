from unittest.mock import AsyncMock, Mock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components import enocean_ptm216b
from custom_components.enocean_ptm216b.const import DOMAIN
from custom_components.enocean_ptm216b.runtime_data import CaptureState


@pytest.mark.asyncio
async def test_setup_exposes_no_capture_action_or_service(hass):
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.enocean_ptm216b.bluetooth.async_register_callback",
            return_value=Mock(),
        ),
        patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()),
        patch(
            "custom_components.enocean_ptm216b.IntegrationSecretStore.async_get_or_create",
            AsyncMock(return_value=b"\x01" * 32),
        ),
    ):
        assert await enocean_ptm216b.async_setup_entry(hass, entry)

    assert not hass.services.has_service(DOMAIN, "start_designation_capture")
    assert entry.runtime_data.capture_state is CaptureState.INERT
    assert entry.runtime_data.capture_timer is None
    assert entry.runtime_data.designation_candidates == {}
