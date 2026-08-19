"""Device triggers: bind automations to a button's press/release/short/long.

Standard ``homeassistant.components.device_automation`` device_trigger
pattern (auto-discovered by module name -- nothing else needs to register
this module). Entirely downstream of ``event.py``'s
``Ptm216bButtonEventEntity._handle_button_event``, which fires
``const.EVENT_ENOCEAN_PTM216B`` on the event bus carrying only
``{device_id, button, action}`` -- no address, key, or counter material,
matching this repo's fail-closed/no-key convention throughout. This module
never touches ``button_pipeline.py``'s verification gates or
``press_timing.py``'s state machine; it only offers/attaches triggers for
the four actions those already emit (``press``, ``release``,
``short_press``, ``long_press``).

Per HA device-trigger convention, ``type`` is the action (per
``press_timing.PressAction``) and ``subtype`` is the button
(A0/A1/B0/B1) -- a one-rocker switch (see ``config_flow.py``'s Add-device
wizard) offers only A0/A1 triggers, matching exactly the button set
``event.py`` creates entities for.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.device_automation import (
    DEVICE_TRIGGER_BASE_SCHEMA,
    InvalidDeviceAutomationConfig,
)
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_EVENT,
    CONF_PLATFORM,
    CONF_TYPE,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .const import ATTR_ACTION, ATTR_BUTTON, DOMAIN, EVENT_ENOCEAN_PTM216B
from .press_timing import PressAction
from .telegram import Button

CONF_SUBTYPE = "subtype"

_SINGLE_ROCKER_BUTTONS = (Button.A0, Button.A1)
_TRIGGER_TYPES = [action.value for action in PressAction]
_TRIGGER_SUBTYPES = [button.value for button in Button]

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(_TRIGGER_TYPES),
        vol.Required(CONF_SUBTYPE): vol.In(_TRIGGER_SUBTYPES),
    }
)


def _rocker_buttons_for_device(
    hass: HomeAssistant, device_id: str
) -> tuple[Button, ...] | None:
    """Return one commissioned switch device's rocker buttons, or ``None``.

    ``None`` means "not a commissioned switch of this integration" -- an
    unknown device, a device belonging to a different integration, or a
    device whose subentry has since been removed. Matches purely via the
    device registry's ``(DOMAIN, handle)`` identifier and the owning
    config entry's "switch" subentries -- never via an address, mirroring
    ``event.py``'s own entity-creation filtering exactly.
    """
    device_registry = dr.async_get(hass)
    device = device_registry.async_get(device_id)
    if device is None:
        return None
    handle = next(
        (identifier[1] for identifier in device.identifiers if identifier[0] == DOMAIN),
        None,
    )
    if handle is None:
        return None
    for entry_id in device.config_entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            continue
        for subentry in entry.subentries.values():
            if (
                subentry.subentry_type == "switch"
                and subentry.data.get("handle") == handle
            ):
                return (
                    _SINGLE_ROCKER_BUTTONS
                    if subentry.data.get("rockers") == 1
                    else tuple(Button)
                )
    return None


async def async_get_triggers(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, Any]]:
    """List short_press/long_press/press/release triggers for one switch device.

    Returns one trigger per (button, action) pair for this device's actual
    rocker set -- four actions x two buttons (A0/A1 only) for a one-rocker
    switch, four actions x four buttons for a two-rocker switch. Returns
    an empty list for any device this integration does not recognize as a
    currently commissioned switch.
    """
    buttons = _rocker_buttons_for_device(hass, device_id)
    if buttons is None:
        return []
    return [
        {
            CONF_PLATFORM: "device",
            CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: action,
            CONF_SUBTYPE: button.value,
        }
        for button in buttons
        for action in _TRIGGER_TYPES
    ]


async def async_validate_trigger_config(
    hass: HomeAssistant, config: ConfigType
) -> ConfigType:
    """Validate a trigger's type/subtype against this device's actual rocker set.

    ``TRIGGER_SCHEMA`` alone already rejects an unknown action or an
    out-of-range button label; this additionally rejects a syntactically
    valid B0/B1 subtype for a one-rocker switch, which has no such entity.
    """
    config = TRIGGER_SCHEMA(config)
    device_id = config[CONF_DEVICE_ID]
    buttons = _rocker_buttons_for_device(hass, device_id)
    if buttons is None or config[CONF_SUBTYPE] not in {b.value for b in buttons}:
        raise InvalidDeviceAutomationConfig(
            f"Trigger {config[CONF_TYPE]}/{config[CONF_SUBTYPE]} is not valid "
            f"for device_id '{device_id}'"
        )
    return config


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Attach via the generic event trigger, filtered on device_id/action/button."""
    return await event_trigger.async_attach_trigger(
        hass,
        event_trigger.TRIGGER_SCHEMA(
            {
                event_trigger.CONF_PLATFORM: CONF_EVENT,
                event_trigger.CONF_EVENT_TYPE: EVENT_ENOCEAN_PTM216B,
                event_trigger.CONF_EVENT_DATA: {
                    CONF_DEVICE_ID: config[CONF_DEVICE_ID],
                    ATTR_ACTION: config[CONF_TYPE],
                    ATTR_BUTTON: config[CONF_SUBTYPE],
                },
            }
        ),
        action,
        trigger_info,
        platform_type="device",
    )
