"""Configuration flow for the passive EnOcean PTM 216B observer."""

from __future__ import annotations

from typing import Callable

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
        """Offer a manual menu between designation and evidence capture."""
        return self.async_show_menu(
            step_id="reconfigure",
            menu_options=["designation_capture", "evidence_capture"],
        )

    async def async_step_designation_capture(
        self, user_input: dict[str, object] | None = None
    ) -> FlowResult:
        """Manually start a bounded, runtime-only designation capture."""
        entry = self._get_reconfigure_entry()
        runtime: Ptm216bRuntimeData = entry.runtime_data
        runtime.start_designation_capture(self._schedule)
        return self.async_abort(reason="designation_capture_started")

    async def async_step_evidence_capture(
        self, user_input: dict[str, object] | None = None
    ) -> FlowResult:
        """Manually start bounded structural evidence capture, if designated."""
        entry = self._get_reconfigure_entry()
        runtime: Ptm216bRuntimeData = entry.runtime_data
        if not runtime.start_evidence_capture(self._schedule):
            return self.async_abort(reason="no_designated_device")
        return self.async_abort(reason="evidence_capture_started")

    def _schedule(self, delay: float, finish: Callable[[], None]) -> Callable[[], None]:
        """Schedule a bounded-capture timer using Home Assistant's event loop."""
        return async_call_later(self.hass, delay, lambda _now: finish())
