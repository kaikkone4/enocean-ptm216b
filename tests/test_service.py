from unittest.mock import AsyncMock, Mock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components import enocean_ptm216b
from custom_components.enocean_ptm216b.const import (
    DOMAIN,
    SERVICE_START_DESIGNATION_CAPTURE,
)
from custom_components.enocean_ptm216b.runtime_data import (
    DESIGNATION_CAPTURE_SECONDS,
    CaptureState,
)


async def _setup_entry(hass):
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
    return entry


@pytest.mark.asyncio
async def test_service_is_registered_but_capture_remains_manual_only(hass):
    entry = await _setup_entry(hass)

    assert hass.services.has_service(DOMAIN, SERVICE_START_DESIGNATION_CAPTURE)
    assert entry.runtime_data.capture_state is CaptureState.INERT
    assert entry.runtime_data.capture_timer is None
    assert entry.runtime_data.designation_candidates == {}


@pytest.mark.asyncio
async def test_service_starts_exactly_thirty_second_capture(hass):
    entry = await _setup_entry(hass)
    cancel_timer = Mock()

    with patch(
        "custom_components.enocean_ptm216b.async_call_later",
        return_value=cancel_timer,
    ) as call_later:
        await hass.services.async_call(
            DOMAIN, SERVICE_START_DESIGNATION_CAPTURE, {}, blocking=True
        )

    assert entry.runtime_data.capture_state is CaptureState.CAPTURING
    assert entry.runtime_data.capture_timer is cancel_timer
    assert call_later.call_args.args[0] is hass
    assert call_later.call_args.args[1] == DESIGNATION_CAPTURE_SECONDS


@pytest.mark.asyncio
async def test_unload_removes_manual_capture_service(hass):
    entry = await _setup_entry(hass)
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

    assert await enocean_ptm216b.async_unload_entry(hass, entry)

    assert not hass.services.has_service(DOMAIN, SERVICE_START_DESIGNATION_CAPTURE)
    assert entry.runtime_data.capture_state is CaptureState.INERT
