"""Configuration flow for the passive EnOcean PTM 216B observer.

Commissioning (``async_step_commission_switch``) is the one deliberate,
local, user-driven flow where an address and a device-specific security key
are accepted at all -- see ``commissioning_store.py``'s module docstring for
the exact storage boundary this flow feeds. Nothing entered here is ever
echoed back into an error message, an abort reason, or a redisplayed form's
description placeholders: on any failure the flow only names a typed reason,
never the text the user typed.
"""

from __future__ import annotations

import re
from typing import Callable

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import selector
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN
from .identity import canonicalize_address
from .runtime_data import Ptm216bRuntimeData

_KEY_HEX_LENGTH = 32
_QR_ADDRESS_RE = re.compile(r"30S([0-9A-Fa-f]{12})", re.IGNORECASE)
_QR_KEY_RE = re.compile(r"Z([0-9A-Fa-f]{" + str(_KEY_HEX_LENGTH) + r"})", re.IGNORECASE)
_KEY_HEX_RE = re.compile(r"^[0-9A-Fa-f]{" + str(_KEY_HEX_LENGTH) + r"}$")

_COMMISSION_SCHEMA = vol.Schema(
    {
        vol.Optional("qr_payload", default=""): str,
        vol.Optional("address", default=""): str,
        vol.Optional("security_key", default=""): str,
        vol.Required("name"): str,
    }
)


def _parse_qr_payload(text: str) -> tuple[str | None, bytes | None]:
    """Tolerantly extract (address, key) from EnOcean label/QR text.

    Looks for the EnOcean label data-identifier tokens ``30S`` (followed by
    12 hex digits: the address) and ``Z`` (followed by 32 hex digits: the
    16-byte security key) anywhere in ``text``, case-insensitively. Tokens
    may be separated by ``+``, whitespace, or nothing at all -- this
    searches for each token independently rather than splitting on a fixed
    separator, so any of those layouts work. Returns ``(None, None)``
    components for whichever token is absent or does not parse; callers
    must not merge a partial QR result with the manual fallback fields (see
    :func:`_resolve_commissioning_input`).
    """
    address: str | None = None
    address_match = _QR_ADDRESS_RE.search(text)
    if address_match:
        try:
            address = canonicalize_address(address_match.group(1))
        except ValueError:
            address = None

    key_match = _QR_KEY_RE.search(text)
    key = bytes.fromhex(key_match.group(1)) if key_match else None

    return address, key


def _parse_manual_address(text: str) -> str | None:
    """Accept colon-separated or plain 12-hex manual address entry."""
    text = text.strip()
    if not text:
        return None
    try:
        return canonicalize_address(text)
    except ValueError:
        return None


def _parse_manual_key(text: str) -> bytes | None:
    """Accept a 32-hex-character (case-insensitive) manual security key."""
    text = text.strip()
    if not _KEY_HEX_RE.fullmatch(text):
        return None
    return bytes.fromhex(text)


def _resolve_commissioning_input(
    user_input: dict[str, object],
) -> tuple[str | None, bytes | None]:
    """Resolve one (canonical address, 16-byte key) pair from form input.

    Precedence: if ``qr_payload`` parses to BOTH an address and a key, it
    wins outright over the manual ``address``/``security_key`` fields --
    partial results from the two sources are never merged. Only when the QR
    payload is absent, or does not yield both fields, do the manual fields
    apply.
    """
    qr_payload = str(user_input.get("qr_payload") or "").strip()
    if qr_payload:
        address, key = _parse_qr_payload(qr_payload)
        if address is not None and key is not None:
            return address, key

    address = _parse_manual_address(str(user_input.get("address") or ""))
    key = _parse_manual_key(str(user_input.get("security_key") or ""))
    if address is not None and key is not None:
        return address, key
    return None, None


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
        """Offer a manual menu between capture, commissioning, and removal."""
        return self.async_show_menu(
            step_id="reconfigure",
            menu_options=[
                "designation_capture",
                "evidence_capture",
                "commission_switch",
                "decommission_switch",
            ],
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

    async def async_step_commission_switch(
        self, user_input: dict[str, object] | None = None
    ) -> FlowResult:
        """Trust one switch's address/key after cross-checking its designation.

        Requires a designation from this runtime session (Phase 1.5) that
        HMACs to the exact same address the user enters here -- this proves
        the QR/manual entry belongs to the physical switch that was just
        pressed, not a typo or someone else's label. See the module
        docstring for why nothing entered here is ever echoed back.
        """
        entry = self._get_reconfigure_entry()
        runtime: Ptm216bRuntimeData = entry.runtime_data
        if runtime.designated_identifier is None:
            return self.async_abort(reason="no_designated_device")

        errors: dict[str, str] = {}
        if user_input is not None:
            address, key = _resolve_commissioning_input(user_input)
            name = str(user_input.get("name", "")).strip()
            if address is None or key is None or not name:
                errors["base"] = "invalid_commissioning_data"
            else:
                candidate_identifier = runtime.compute_device_identifier(address)
                if candidate_identifier != runtime.designated_identifier:
                    return self.async_abort(reason="designation_mismatch")
                store = runtime.commissioning_store
                await store.async_add(address, key, name)
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="commissioning_complete")

        return self.async_show_form(
            step_id="commission_switch",
            data_schema=_COMMISSION_SCHEMA,
            errors=errors,
        )

    async def async_step_decommission_switch(
        self, user_input: dict[str, object] | None = None
    ) -> FlowResult:
        """Remove one commissioned switch: its store record and its device.

        The selector's option ``value`` is the non-reversible device handle,
        never the canonical address -- unlike a visible label, a selector's
        ``value`` is still serialized to the frontend as part of the form
        response, so the raw address must not appear there either. The
        handle -> address mapping is recomputed fresh from the store at the
        top of every call (both showing the form and handling its
        submission), so the submitted handle can always be matched back to
        its canonical address without persisting anything on the flow
        instance between steps.
        """
        entry = self._get_reconfigure_entry()
        runtime: Ptm216bRuntimeData = entry.runtime_data
        store = runtime.commissioning_store
        switches = store.switches if store is not None else {}
        if not switches:
            return self.async_abort(reason="no_commissioned_devices")

        handles_to_addresses = {
            runtime.commissioned_device_handle(address): address for address in switches
        }

        if user_input is not None:
            handle = str(user_input["switch"])
            canonical_address = handles_to_addresses.get(handle)
            if canonical_address is not None:
                await store.async_remove(canonical_address)
                registry = dr.async_get(self.hass)
                device_entry = registry.async_get_device(identifiers={(DOMAIN, handle)})
                if device_entry is not None:
                    registry.async_remove_device(device_entry.id)
                await self.hass.config_entries.async_reload(entry.entry_id)
            return self.async_abort(reason="decommissioning_complete")

        options = [
            selector.SelectOptionDict(value=handle, label=switches[address].name)
            for handle, address in handles_to_addresses.items()
        ]
        return self.async_show_form(
            step_id="decommission_switch",
            data_schema=vol.Schema(
                {
                    vol.Required("switch"): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=options)
                    )
                }
            ),
        )

    def _schedule(self, delay: float, finish: Callable[[], None]) -> Callable[[], None]:
        """Schedule a bounded-capture timer using Home Assistant's event loop."""
        return async_call_later(self.hass, delay, lambda _now: finish())
