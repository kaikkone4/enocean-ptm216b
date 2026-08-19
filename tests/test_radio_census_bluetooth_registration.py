"""Unit test for the radio census's own unfiltered Bluetooth callback
registration -- the exact matcher shape and scanning mode this integration
verified against the installed homeassistant.components.bluetooth package
(see config_flow.py's ``_register_unfiltered_bluetooth_callback`` docstring
for the full reasoning). Deliberately does not use the ``hass`` pytest
fixture -- registering a real bluetooth component is unrelated to what this
test verifies and would only couple it to environment-dependent bluetooth
setup.
"""

from unittest.mock import Mock, patch

from custom_components.enocean_ptm216b.config_flow import ConfigFlow

ADDRESS = "AA:BB:CC:DD:EE:FF"


def test_matcher_is_connectable_false_with_no_other_field():
    """This is the one shape homeassistant.components.bluetooth's manager
    treats as "match every advertisement, connectable or not" -- an empty
    or absent matcher instead defaults connectable to True and would drop
    every non-connectable advertisement.
    """
    flow = ConfigFlow()
    flow.hass = Mock()

    with patch(
        "custom_components.enocean_ptm216b.config_flow.bluetooth.async_register_callback",
        return_value=Mock(),
    ) as register_callback:
        flow._register_unfiltered_bluetooth_callback(Mock())

    register_callback.assert_called_once()
    _hass, _callback, matcher, mode = register_callback.call_args.args
    assert matcher == {"connectable": False}
    assert mode.name == "PASSIVE"


def test_registration_returns_the_manager_s_own_cancel_callable():
    flow = ConfigFlow()
    flow.hass = Mock()
    cancel = Mock()

    with patch(
        "custom_components.enocean_ptm216b.config_flow.bluetooth.async_register_callback",
        return_value=cancel,
    ):
        returned = flow._register_unfiltered_bluetooth_callback(Mock())

    assert returned is cancel


def test_the_wrapped_callback_translates_service_info_into_the_handler_call():
    flow = ConfigFlow()
    flow.hass = Mock()
    handler = Mock()

    with patch(
        "custom_components.enocean_ptm216b.config_flow.bluetooth.async_register_callback",
        return_value=Mock(),
    ) as register_callback:
        flow._register_unfiltered_bluetooth_callback(handler)

    on_advertisement = register_callback.call_args.args[1]
    service_info = Mock(
        address=ADDRESS,
        manufacturer_data={0x03C3: b"\x01" * 159},
        service_uuids=["0000180a-0000-1000-8000-00805f9b34fb"],
        connectable=True,
    )

    on_advertisement(service_info, Mock())

    handler.assert_called_once_with(
        ADDRESS,
        {0x03C3: b"\x01" * 159},
        {"0000180a-0000-1000-8000-00805f9b34fb"},
        True,
    )
