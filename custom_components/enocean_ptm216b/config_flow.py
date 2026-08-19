"""Configuration flow for the passive EnOcean PTM 216B observer.

The per-switch **Add-device wizard** (:class:`SwitchSubentryFlow`, a config
*subentry* flow -- see ``homeassistant.config_entries.ConfigSubentryFlow``)
is the one deliberate, local, user-driven flow where an address and a
device-specific security key are accepted at all -- see
``commissioning_store.py``'s module docstring for the exact storage boundary
this flow feeds. Nothing entered here is ever echoed back into an error
message, an abort reason, or a redisplayed form's description placeholders:
on any failure the flow only names a typed reason, never the text the user
typed. Parsing helpers live in ``commissioning_input.py`` so both this
wizard and (in the future) any other entry point can share them without
duplicating the precedence rules.
"""

from __future__ import annotations

import asyncio
from typing import Callable

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.event import async_call_later

from .commissioning_input import resolve_commissioning_input_with_photo
from .const import DOMAIN
from .press_timing import (
    DEFAULT_LONG_PRESS_THRESHOLD_MS,
    MAX_LONG_PRESS_THRESHOLD_MS,
    MIN_LONG_PRESS_THRESHOLD_MS,
)
from .qr_decode import is_qr_decode_available
from .runtime_data import (
    DESIGNATION_BASELINE_SECONDS,
    DESIGNATION_CAPTURE_SECONDS,
    CaptureState,
    DesignationOutcome,
    Ptm216bRuntimeData,
)

_ROCKER_OPTIONS = [
    selector.SelectOptionDict(value="1", label="1 (A0/A1 only)"),
    selector.SelectOptionDict(value="2", label="2 (A0/A1/B0/B1)"),
]


def _long_press_threshold_selector() -> selector.NumberSelector:
    """Build the shared long-press-threshold field: sane bounds, enforced by
    the flow manager's own schema validation (a value outside
    [MIN_LONG_PRESS_THRESHOLD_MS, MAX_LONG_PRESS_THRESHOLD_MS] redisplays
    the form with a field error, never silently clamped).
    """
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=MIN_LONG_PRESS_THRESHOLD_MS,
            max=MAX_LONG_PRESS_THRESHOLD_MS,
            step=50,
            mode=selector.NumberSelectorMode.BOX,
            unit_of_measurement="ms",
        )
    )


# Mirrors runtime_data.py's own phase durations exactly -- this wizard never
# changes designation capture's timing, only polls it for display.
_PHASE_DURATIONS: dict[CaptureState, float] = {
    CaptureState.BASELINE: DESIGNATION_BASELINE_SECONDS,
    CaptureState.PRESS: DESIGNATION_CAPTURE_SECONDS,
    CaptureState.CONFIRMING: DESIGNATION_CAPTURE_SECONDS,
}


def _key_entry_schema(*, qr_available: bool) -> vol.Schema:
    """Build the key-entry form schema, omitting the photo field if unusable."""
    fields: dict[vol.Marker, object] = {}
    if qr_available:
        fields[vol.Optional("qr_image")] = selector.FileSelector(
            selector.FileSelectorConfig(accept="image/*")
        )
    fields[vol.Optional("qr_payload", default="")] = str
    fields[vol.Optional("address", default="")] = str
    fields[vol.Optional("security_key", default="")] = str
    fields[vol.Required("name")] = str
    fields[vol.Optional("rockers", default="2")] = selector.SelectSelector(
        selector.SelectSelectorConfig(options=_ROCKER_OPTIONS)
    )
    fields[
        vol.Optional("long_press_threshold_ms", default=DEFAULT_LONG_PRESS_THRESHOLD_MS)
    ] = _long_press_threshold_selector()
    return vol.Schema(fields)


def _reconfigure_schema() -> vol.Schema:
    """Build the reconfigure form schema: name/rockers/threshold only.

    Deliberately has no ``qr_image``/``qr_payload``/``address``/
    ``security_key`` field -- reconfigure never touches a switch's
    commissioned identity (see ``commissioning_store.py``'s key/address
    boundary), only its editable non-secret subentry fields.
    """
    return vol.Schema(
        {
            vol.Required("name"): str,
            vol.Optional("rockers", default="2"): selector.SelectSelector(
                selector.SelectSelectorConfig(options=_ROCKER_OPTIONS)
            ),
            vol.Optional(
                "long_press_threshold_ms", default=DEFAULT_LONG_PRESS_THRESHOLD_MS
            ): _long_press_threshold_selector(),
        }
    )


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Create the single passive PTM 216B observer entry."""

    VERSION = 2

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
        """Offer a manual menu between the two diagnostic capture tools.

        Commissioning/decommissioning moved to the per-switch Add-device
        wizard (config subentries, see :class:`SwitchSubentryFlow`) in
        Phase 5A; this menu now only ever starts a bounded diagnostic
        capture, never anything that touches a key or address.
        """
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

    @classmethod
    def async_get_supported_subentry_types(
        cls, config_entry: config_entries.ConfigEntry
    ) -> dict[str, type[config_entries.ConfigSubentryFlow]]:
        """Register the per-switch Add-device wizard as a "switch" subentry."""
        return {"switch": SwitchSubentryFlow}


class SwitchSubentryFlow(config_entries.ConfigSubentryFlow):
    """Per-switch Add-device wizard: detect (optional) -> key entry -> done.

    Runs against the SAME :class:`~runtime_data.Ptm216bRuntimeData` instance
    as the parent entry (via ``self._get_entry().runtime_data``), so the
    detection step below drives the exact same
    :meth:`~runtime_data.Ptm216bRuntimeData.start_designation_capture` the
    diagnostic "Designation capture" reconfigure step already drives --
    starting detection here cancels/replaces any other in-progress
    designation exactly like that method's own docstring already documents.
    That is intentional and shared runtime state, not a bug.

    Subentry ``data`` holds ONLY non-secret fields (``handle``, ``name``,
    ``rockers``, ``long_press_threshold_ms``) -- the 16-byte key, canonical
    address, and replay counter stay in ``commissioning_store.py``, linked
    back to this subentry only by ``handle`` (see ``identity.device_handle``).

    :meth:`async_step_reconfigure` (Phase 5B) lets name/rockers/threshold be
    edited later without recommissioning -- see its own docstring.
    """

    def __init__(self) -> None:
        self._detect_phase: CaptureState | None = None
        self._detect_task: asyncio.Task | None = None

    def _schedule(self, delay: float, finish: Callable[[], None]) -> Callable[[], None]:
        """Schedule a bounded-capture timer using Home Assistant's event loop."""
        return async_call_later(self.hass, delay, lambda _now: finish())

    async def async_step_user(
        self, user_input: dict[str, object] | None = None
    ) -> FlowResult:
        """Offer detection (recommended) or a manual, undetected key entry."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["detect", "key_entry_manual"],
        )

    async def async_step_detect(
        self, user_input: dict[str, object] | None = None
    ) -> FlowResult:
        """Drive bounded designation capture via live, auto-advancing progress.

        Called repeatedly by the flow manager: once to start, then again
        every time the ``progress_task`` below completes -- see
        ``homeassistant.data_entry_flow.FlowManager._async_handle_step``.
        Never requires a mid-detection click; each call just re-reads
        ``runtime.capture_state``/``designation_outcome`` and either shows
        the next phase's progress or moves on.
        """
        entry = self._get_entry()
        runtime: Ptm216bRuntimeData = entry.runtime_data

        if self._detect_task is None:
            runtime.start_designation_capture(self._schedule)

        state = runtime.capture_state
        if state is CaptureState.INERT:
            self._cancel_detect_task()
            self._detect_phase = None
            # A step whose previous result was SHOW_PROGRESS may ONLY
            # transition to SHOW_PROGRESS or SHOW_PROGRESS_DONE -- the real
            # FlowManager (homeassistant/data_entry_flow.py::_async_configure)
            # raises ValueError otherwise. async_show_progress_done's
            # next_step_id is what actually dispatches to the next step
            # handler (via FlowManager.async_configure's own loop) -- calling
            # that handler directly here, even via `await`, is exactly the
            # illegal transition the manager rejects.
            if runtime.designation_outcome is DesignationOutcome.SELECTED:
                return self.async_show_progress_done(next_step_id="key_entry_detected")
            return self.async_show_progress_done(next_step_id="detect_failed")

        if self._detect_phase != state or self._detect_task is None:
            self._cancel_detect_task()
            self._detect_phase = state
            # A short, self-contained wait mirroring this phase's real
            # duration -- purely so the frontend polls and this step gets
            # re-invoked; it does not drive the actual capture state
            # machine, which advances on its own via `runtime`'s own timer.
            self._detect_task = self.hass.async_create_task(
                asyncio.sleep(_PHASE_DURATIONS[state])
            )
        elif self._detect_task.done():
            # Our display-refresh task finished slightly before the real
            # capture timer advanced `runtime.capture_state`; wait a short
            # beat and re-check rather than showing a stale phase.
            self._detect_task = self.hass.async_create_task(asyncio.sleep(0.1))

        return self.async_show_progress(
            progress_action=f"detect_{state.value}",
            progress_task=self._detect_task,
        )

    def _cancel_detect_task(self) -> None:
        """Cancel and drop this wizard's own display-refresh task, if any."""
        if self._detect_task is not None and not self._detect_task.done():
            self._detect_task.cancel()
        self._detect_task = None

    async def async_step_detect_failed(
        self, user_input: dict[str, object] | None = None
    ) -> FlowResult:
        """Offer retry-detection or manual key entry after `no_selection`."""
        return self.async_show_menu(
            step_id="detect_failed",
            menu_options=["detect", "key_entry_manual"],
        )

    def async_remove(self) -> None:
        """Cancel a still-running detection if this flow is abandoned."""
        self._cancel_detect_task()
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            return
        runtime: Ptm216bRuntimeData = entry.runtime_data
        if runtime.capture_state is not CaptureState.INERT:
            runtime.cancel_designation_capture()

    async def async_step_reconfigure(
        self, user_input: dict[str, object] | None = None
    ) -> FlowResult:
        """Edit name, rocker count, and long-press threshold (Phase 5B).

        Dispatched automatically when this flow is started with
        ``context={"source": "reconfigure", "subentry_id": ...}`` (see
        ``ConfigSubentryFlowManager.async_create_flow``, which sets
        ``init_step = context["source"]``). Never shows or accepts an
        address/security-key field -- this step cannot change or re-enter a
        switch's commissioned identity, only its editable non-secret
        subentry fields (see ``commissioning_store.py``'s key/address
        boundary). A changed ``rockers``/``long_press_threshold_ms`` takes
        effect on the automatic reload ``async_update_and_abort`` triggers
        -- no separate reload step needed.
        """
        subentry = self._get_reconfigure_subentry()
        schema = self.add_suggested_values_to_schema(
            _reconfigure_schema(), subentry.data
        )

        errors: dict[str, str] = {}
        if user_input is not None:
            name = str(user_input.get("name", "")).strip()
            rockers = int(str(user_input.get("rockers", "2")))
            threshold_ms = int(
                user_input.get(
                    "long_press_threshold_ms", DEFAULT_LONG_PRESS_THRESHOLD_MS
                )
            )
            if not name:
                errors["base"] = "invalid_commissioning_data"
            else:
                return self.async_update_and_abort(
                    self._get_entry(),
                    subentry,
                    title=name,
                    data={
                        **subentry.data,
                        "name": name,
                        "rockers": rockers,
                        "long_press_threshold_ms": threshold_ms,
                    },
                )

        return self.async_show_form(
            step_id="reconfigure", data_schema=schema, errors=errors
        )

    async def async_step_key_entry_detected(
        self, user_input: dict[str, object] | None = None
    ) -> FlowResult:
        """Key-entry step after a successful detection; cross-checks the address."""
        return await self._async_step_key_entry(user_input, detected=True)

    async def async_step_key_entry_manual(
        self, user_input: dict[str, object] | None = None
    ) -> FlowResult:
        """Key-entry step without detection; no typo cross-check is possible."""
        return await self._async_step_key_entry(user_input, detected=False)

    async def _async_step_key_entry(
        self, user_input: dict[str, object] | None, *, detected: bool
    ) -> FlowResult:
        step_id = "key_entry_detected" if detected else "key_entry_manual"
        entry = self._get_entry()
        runtime: Ptm216bRuntimeData = entry.runtime_data
        qr_available = is_qr_decode_available()

        errors: dict[str, str] = {}
        if user_input is not None:
            address, key = await resolve_commissioning_input_with_photo(
                self.hass, user_input
            )
            name = str(user_input.get("name", "")).strip()
            rockers = int(str(user_input.get("rockers", "2")))
            threshold_ms = int(
                user_input.get(
                    "long_press_threshold_ms", DEFAULT_LONG_PRESS_THRESHOLD_MS
                )
            )
            if address is None or key is None or not name:
                errors["base"] = "invalid_commissioning_data"
            elif runtime.commissioning_store.get(address) is not None:
                # Adding a subentry with a unique_id (the handle) that
                # already exists would otherwise raise inside the flow
                # manager's finish-flow step -- fail closed with a normal
                # form error instead of an unhandled exception.
                errors["base"] = "switch_already_commissioned"
            else:
                if detected:
                    candidate_identifier = runtime.compute_device_identifier(address)
                    if candidate_identifier != runtime.designated_identifier:
                        return self.async_abort(reason="designation_mismatch")

                store = runtime.commissioning_store
                await store.async_add(address, key, name)
                handle = runtime.commissioned_device_handle(address)
                # Returning CREATE_ENTRY, not calling async_add_subentry
                # ourselves: ConfigSubentryFlowManager.async_finish_flow
                # adds the subentry from this result automatically. The
                # parent entry's update listener (see __init__.py's
                # async_setup_entry) reacts to that subentry addition by
                # reloading the entry so the new switch's entities appear --
                # no explicit reload needed here.
                return self.async_create_entry(
                    title=name,
                    data={
                        "handle": handle,
                        "name": name,
                        "rockers": rockers,
                        "long_press_threshold_ms": threshold_ms,
                    },
                    unique_id=handle,
                )

        return self.async_show_form(
            step_id=step_id,
            data_schema=_key_entry_schema(qr_available=qr_available),
            errors=errors,
            description_placeholders={
                "qr_status": (
                    ""
                    if qr_available
                    else "Photo upload is unavailable in this Home Assistant "
                    "installation (the optional zxing-cpp library is not "
                    "installed); use QR/label text or manual entry instead."
                )
            },
        )
