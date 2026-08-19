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
    entry.runtime_data.record_designation_candidate("AA:BB:CC:DD:EE:FF", 1.0)
    entry.add_to_hass(hass)
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

    assert await enocean_ptm216b.async_unload_entry(hass, entry)
    hass.config_entries.async_unload_platforms.assert_awaited_once_with(
        entry, ["sensor", "event"]
    )
    cancel_timer.assert_called_once_with()
    assert entry.runtime_data.capture_state is CaptureState.INERT
    assert entry.runtime_data.designation_candidates == {}
    assert entry.runtime_data.capture_timer is None


@pytest.mark.asyncio
async def test_unload_clears_a_running_evidence_capture(hass):
    from custom_components.enocean_ptm216b.evidence_capture import EvidenceState

    entry = MockConfigEntry(domain=DOMAIN)
    entry.runtime_data = Ptm216bRuntimeData(_hmac_secret=b"\x01" * 32)
    entry.runtime_data.designated_identifier = "a" * 64
    evidence_cancel = Mock()
    entry.runtime_data.start_evidence_capture(Mock(return_value=evidence_cancel))
    entry.add_to_hass(hass)
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

    assert await enocean_ptm216b.async_unload_entry(hass, entry)

    evidence_cancel.assert_called_once_with()
    assert entry.runtime_data.evidence_collector.state is EvidenceState.INERT


@pytest.mark.asyncio
async def test_unload_clears_a_running_radio_census_and_unregisters_its_callback(hass):
    from custom_components.enocean_ptm216b.radio_census import RadioCensusState

    entry = MockConfigEntry(domain=DOMAIN)
    entry.runtime_data = Ptm216bRuntimeData(_hmac_secret=b"\x01" * 32)
    census_cancel = Mock()
    entry.runtime_data.start_radio_census(
        Mock(return_value=Mock()), Mock(return_value=census_cancel)
    )
    entry.add_to_hass(hass)
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

    assert await enocean_ptm216b.async_unload_entry(hass, entry)

    census_cancel.assert_called_once_with()
    assert entry.runtime_data.radio_census.state is RadioCensusState.INERT


@pytest.mark.asyncio
async def test_unload_unregisters_a_completed_radio_census_s_callback_only_once(hass):
    """A completed census has already unregistered its own callback; unload
    must not try to call the (already-cleared) unregister function again.
    """
    from custom_components.enocean_ptm216b.radio_census import RadioCensusState

    entry = MockConfigEntry(domain=DOMAIN)
    entry.runtime_data = Ptm216bRuntimeData(_hmac_secret=b"\x01" * 32)
    census_cancel = Mock()
    schedule = Mock(return_value=Mock())
    entry.runtime_data.start_radio_census(schedule, Mock(return_value=census_cancel))
    schedule.call_args.args[1]()  # -> press
    schedule.call_args.args[1]()  # -> complete
    census_cancel.assert_called_once_with()
    entry.add_to_hass(hass)
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

    assert await enocean_ptm216b.async_unload_entry(hass, entry)

    census_cancel.assert_called_once_with()
    assert entry.runtime_data.radio_census.state is RadioCensusState.INERT
