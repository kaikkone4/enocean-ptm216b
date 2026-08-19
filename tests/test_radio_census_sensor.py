from unittest.mock import Mock

from homeassistant.helpers.entity import EntityCategory

from custom_components.enocean_ptm216b.radio_census import MAX_TRACKED_MANUFACTURER_IDS
from custom_components.enocean_ptm216b.runtime_data import Ptm216bRuntimeData
from custom_components.enocean_ptm216b.sensor import Ptm216bRadioCensusSensor

SECRET = b"\x01" * 32
ADDRESS = "AA:BB:CC:DD:EE:FF"
ENOCEAN_ID = 0x03DA
CASAMBI_ID = 0x03C3


def _entry() -> Mock:
    entry = Mock(entry_id="entry-id")
    entry.runtime_data = Ptm216bRuntimeData(_hmac_secret=SECRET)
    return entry


def _frame(manufacturer_id: int) -> dict:
    return {manufacturer_id: b"\x01" * 9}


def _run_to_complete(entry: Mock, schedule: Mock) -> None:
    schedule.call_args.args[1]()  # -> press
    schedule.call_args.args[1]()  # -> complete


def test_sensor_is_inert_with_no_attributes_before_any_census():
    entry = _entry()
    sensor = Ptm216bRadioCensusSensor(entry)

    assert sensor.native_value == "inert"
    assert sensor.extra_state_attributes == {}
    assert sensor.entity_category is EntityCategory.DIAGNOSTIC


def test_sensor_reports_only_the_live_phase_total_while_baseline():
    entry = _entry()
    entry.runtime_data.start_radio_census(
        Mock(return_value=Mock()), Mock(return_value=Mock())
    )
    sensor = Ptm216bRadioCensusSensor(entry)

    entry.runtime_data._handle_radio_census_advertisement(
        ADDRESS, _frame(ENOCEAN_ID), set(), False
    )

    assert sensor.native_value == "baseline"
    assert sensor.extra_state_attributes == {"phase_advertisement_count": 1}
    entry.runtime_data.cancel_radio_census()


def test_sensor_reports_only_the_live_phase_total_while_press():
    entry = _entry()
    schedule = Mock(return_value=Mock())
    entry.runtime_data.start_radio_census(schedule, Mock(return_value=Mock()))
    schedule.call_args.args[1]()  # -> press
    sensor = Ptm216bRadioCensusSensor(entry)

    entry.runtime_data._handle_radio_census_advertisement(
        ADDRESS, _frame(ENOCEAN_ID), set(), False
    )

    assert sensor.native_value == "press"
    assert sensor.extra_state_attributes == {"phase_advertisement_count": 1}
    entry.runtime_data.cancel_radio_census()


def test_sensor_reports_the_ranked_summary_and_convenience_fields_when_complete():
    entry = _entry()
    schedule = Mock(return_value=Mock())
    entry.runtime_data.start_radio_census(schedule, Mock(return_value=Mock()))
    handler = entry.runtime_data._handle_radio_census_advertisement
    sensor = Ptm216bRadioCensusSensor(entry)

    handler(ADDRESS, _frame(ENOCEAN_ID), set(), False)  # baseline traffic
    schedule.call_args.args[1]()  # -> press
    handler(ADDRESS, _frame(ENOCEAN_ID), set(), False)
    handler(ADDRESS, _frame(ENOCEAN_ID), set(), False)
    handler(ADDRESS, _frame(CASAMBI_ID), set(), True)
    schedule.call_args.args[1]()  # -> complete

    assert sensor.native_value == "complete"
    attrs = sensor.extra_state_attributes
    assert attrs["truncated"] is False
    assert attrs["displayed_entries_truncated"] is False
    assert attrs["enocean_0x03da_press_payload_changes"] == 2
    assert attrs["casambi_0x03c3_press_payload_changes"] == 1
    entries = attrs["entries"]
    # Ranked descending by press_payload_changes: EnOcean (2) before Casambi (1).
    assert list(entries.keys())[:2] == ["0x03da", "0x03c3"]
    assert entries["0x03da"]["baseline_payload_changes"] == 1
    assert entries["0x03da"]["press_payload_changes"] == 2


def test_convenience_fields_default_to_zero_when_absent():
    entry = _entry()
    schedule = Mock(return_value=Mock())
    entry.runtime_data.start_radio_census(schedule, Mock(return_value=Mock()))
    sensor = Ptm216bRadioCensusSensor(entry)

    _run_to_complete(entry, schedule)

    attrs = sensor.extra_state_attributes
    assert attrs["enocean_0x03da_press_payload_changes"] == 0
    assert attrs["casambi_0x03c3_press_payload_changes"] == 0
    assert attrs["entries"] == {}


def test_displayed_entries_truncated_flags_when_more_than_the_display_cap():
    entry = _entry()
    schedule = Mock(return_value=Mock())
    entry.runtime_data.start_radio_census(schedule, Mock(return_value=Mock()))
    handler = entry.runtime_data._handle_radio_census_advertisement
    sensor = Ptm216bRadioCensusSensor(entry)

    for manufacturer_id in range(25):
        handler(ADDRESS, _frame(manufacturer_id), set(), False)
    _run_to_complete(entry, schedule)

    attrs = sensor.extra_state_attributes
    assert attrs["displayed_entries_truncated"] is True
    assert len(attrs["entries"]) == 20
    # The tracking cap itself was never hit (25 well under MAX_TRACKED_...).
    assert attrs["truncated"] is False


def test_truncated_flag_reflects_the_collector_s_own_tracking_cap():
    entry = _entry()
    schedule = Mock(return_value=Mock())
    entry.runtime_data.start_radio_census(schedule, Mock(return_value=Mock()))
    handler = entry.runtime_data._handle_radio_census_advertisement
    sensor = Ptm216bRadioCensusSensor(entry)

    for manufacturer_id in range(MAX_TRACKED_MANUFACTURER_IDS + 1):
        handler(ADDRESS, _frame(manufacturer_id), set(), False)
    _run_to_complete(entry, schedule)

    assert sensor.extra_state_attributes["truncated"] is True


async def test_sensor_subscribes_and_unsubscribes_the_runtime_listener():
    entry = _entry()
    sensor = Ptm216bRadioCensusSensor(entry)
    sensor.async_write_ha_state = Mock()

    await sensor.async_added_to_hass()
    assert entry.runtime_data.radio_census_state_listener is sensor.async_write_ha_state

    await sensor.async_will_remove_from_hass()
    assert entry.runtime_data.radio_census_state_listener is None


async def test_sensor_redraws_when_the_press_timer_ends_the_window():
    entry = _entry()
    sensor = Ptm216bRadioCensusSensor(entry)
    sensor.async_write_ha_state = Mock()
    await sensor.async_added_to_hass()
    schedule = Mock(return_value=Mock())
    entry.runtime_data.start_radio_census(schedule, Mock(return_value=Mock()))
    sensor.async_write_ha_state.reset_mock()

    schedule.call_args.args[1]()  # -> press
    schedule.call_args.args[1]()  # -> complete

    sensor.async_write_ha_state.assert_called()
    assert sensor.native_value == "complete"
