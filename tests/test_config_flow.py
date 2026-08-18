import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.enocean_ptm216b.const import DOMAIN
from custom_components.enocean_ptm216b.runtime_data import (
    CaptureState,
    Ptm216bRuntimeData,
)


@pytest.mark.asyncio
async def test_user_config_flow_creates_single_observer_entry(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )

    assert result["type"] == "create_entry"
    assert result["title"] == "PTM 216B observer"
    assert result["data"] == {}


@pytest.mark.asyncio
async def test_reconfigure_requires_manual_confirmation_before_capture(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.runtime_data = Ptm216bRuntimeData(_hmac_secret=b"\x01" * 32)
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": entry.entry_id},
    )

    assert result["type"] == "form"
    assert result["step_id"] == "reconfigure"
    assert entry.runtime_data.capture_state is CaptureState.INERT

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] == "abort"
    assert result["reason"] == "designation_capture_started"
    assert entry.runtime_data.capture_state is CaptureState.CAPTURING
    assert entry.data == {}
    entry.runtime_data.cancel_designation_capture()
