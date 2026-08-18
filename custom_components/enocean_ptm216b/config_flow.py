"""Configuration flow for the passive EnOcean PTM 216B observer."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN
from .runtime_data import Ptm216bRuntimeData


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Create the single passive PTM 216B observer entry."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, object] | None = None
    ) -> FlowResult:
        """Create a manual observer entry without pairing or provisioning."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title="PTM 216B observer", data={})

    async def async_step_reconfigure(
        self, user_input: dict[str, object] | None = None
    ) -> FlowResult:
        """Manually start a bounded, runtime-only designation capture."""
        if user_input is None:
            return self.async_show_form(step_id="reconfigure")

        entry = self._get_reconfigure_entry()
        runtime: Ptm216bRuntimeData = entry.runtime_data
        runtime.start_designation_capture(
            lambda delay, finish: async_call_later(
                self.hass, delay, lambda _now: finish()
            )
        )
        return self.async_abort(reason="designation_capture_started")
