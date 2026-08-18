import pytest

from custom_components.enocean_ptm216b.const import DOMAIN


@pytest.mark.asyncio
async def test_user_config_flow_creates_single_observer_entry(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )

    assert result["type"] == "create_entry"
    assert result["title"] == "PTM 216B observer"
    assert result["data"] == {}
