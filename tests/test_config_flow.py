import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.enocean_ptm216b.const import DOMAIN
from custom_components.enocean_ptm216b.evidence_capture import EvidenceState
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
async def test_reconfigure_shows_a_menu_of_capture_options(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.runtime_data = Ptm216bRuntimeData(_hmac_secret=b"\x01" * 32)
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": entry.entry_id},
    )

    assert result["type"] == "menu"
    assert result["step_id"] == "reconfigure"
    assert set(result["menu_options"]) == {
        "designation_capture",
        "evidence_capture",
        "commission_switch",
        "decommission_switch",
    }
    assert entry.runtime_data.capture_state is CaptureState.INERT
    assert entry.runtime_data.evidence_collector is None


@pytest.mark.asyncio
async def test_reconfigure_designation_capture_starts_bounded_capture(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.runtime_data = Ptm216bRuntimeData(_hmac_secret=b"\x01" * 32)
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": entry.entry_id},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "designation_capture"}
    )

    assert result["type"] == "abort"
    assert result["reason"] == "designation_capture_started"
    assert entry.runtime_data.capture_state is CaptureState.BASELINE
    assert entry.data == {}
    entry.runtime_data.cancel_designation_capture()


@pytest.mark.asyncio
async def test_reconfigure_evidence_capture_aborts_without_a_designated_device(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.runtime_data = Ptm216bRuntimeData(_hmac_secret=b"\x01" * 32)
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": entry.entry_id},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "evidence_capture"}
    )

    assert result["type"] == "abort"
    assert result["reason"] == "no_designated_device"
    assert entry.runtime_data.evidence_collector is None


@pytest.mark.asyncio
async def test_reconfigure_evidence_capture_starts_when_designated(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.runtime_data = Ptm216bRuntimeData(_hmac_secret=b"\x01" * 32)
    entry.runtime_data.designated_identifier = "a" * 64
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": entry.entry_id},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "evidence_capture"}
    )

    assert result["type"] == "abort"
    assert result["reason"] == "evidence_capture_started"
    assert entry.runtime_data.evidence_collector is not None
    assert entry.runtime_data.evidence_collector.state is EvidenceState.COLLECTING
    entry.runtime_data.cancel_evidence_capture()


@pytest.mark.asyncio
async def test_reconfigure_starting_designation_cancels_running_evidence(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.runtime_data = Ptm216bRuntimeData(_hmac_secret=b"\x01" * 32)
    entry.runtime_data.designated_identifier = "a" * 64
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": entry.entry_id},
    )
    await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "evidence_capture"}
    )
    assert entry.runtime_data.evidence_collector.state is EvidenceState.COLLECTING

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": entry.entry_id},
    )
    await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "designation_capture"}
    )

    assert entry.runtime_data.evidence_collector.state is EvidenceState.INERT
    assert entry.runtime_data.capture_state is CaptureState.BASELINE
    entry.runtime_data.cancel_designation_capture()
