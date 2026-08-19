"""Tests for event.py: per-commissioned-switch button-pattern event entities."""

from __future__ import annotations

from unittest.mock import Mock

from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.enocean_ptm216b.commissioning_store import CommissioningStore
from custom_components.enocean_ptm216b.const import DOMAIN
from custom_components.enocean_ptm216b.event import (
    Ptm216bButtonEventEntity,
    async_setup_entry,
)
from custom_components.enocean_ptm216b.identity import canonicalize_address
from custom_components.enocean_ptm216b.press_timing import (
    DEFAULT_LONG_PRESS_THRESHOLD_MS,
    PressAction,
)
from custom_components.enocean_ptm216b.runtime_data import Ptm216bRuntimeData
from custom_components.enocean_ptm216b.telegram import ButtonPattern, Ptm216bButtonState

from conftest import RecordingAddEntities

SECRET = b"\x01" * 32
ADDRESS = "AA:BB:CC:DD:EE:FF"
CANONICAL_ADDRESS = canonicalize_address(ADDRESS)
SYNTHETIC_KEY = bytes(range(16))


def _make_entry() -> Mock:
    entry = Mock(entry_id="entry-id")
    entry.runtime_data = Ptm216bRuntimeData(_hmac_secret=SECRET)
    return entry


async def _commissioned_entry(
    hass,
    *,
    name: str = "Living room switch",
    rockers: int = 2,
    threshold_ms: int | None = None,
) -> MockConfigEntry:
    """Build an entry with a store record and a matching "switch" subentry,
    the same shape ``__init__.py``'s reconciliation and this platform's
    ``async_setup_entry`` both expect after Phase 5A.

    ``threshold_ms=None`` (the default) omits ``long_press_threshold_ms``
    from subentry data entirely, mirroring a switch commissioned before
    Phase 5B -- exercising ``async_setup_entry``'s ``.get(..., default)``
    fallback.
    """
    store = CommissioningStore(hass)
    await store.async_load()
    await store.async_add(CANONICAL_ADDRESS, SYNTHETIC_KEY, name)
    runtime = Ptm216bRuntimeData(_hmac_secret=SECRET, commissioning_store=store)
    handle = runtime.commissioned_device_handle(CANONICAL_ADDRESS)

    subentry_data = {"handle": handle, "name": name, "rockers": rockers}
    if threshold_ms is not None:
        subentry_data["long_press_threshold_ms"] = threshold_ms

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        subentries_data=[
            {
                "data": subentry_data,
                "subentry_type": "switch",
                "title": name,
                "unique_id": handle,
            }
        ],
    )
    entry.runtime_data = runtime
    entry.add_to_hass(hass)
    return entry


def test_event_entity_unique_id_and_device_info_never_expose_the_address():
    entry = _make_entry()
    handle = entry.runtime_data.commissioned_device_handle(CANONICAL_ADDRESS)

    entity = Ptm216bButtonEventEntity(
        entry, CANONICAL_ADDRESS, "Living room switch", ButtonPattern.A0
    )

    assert entity.unique_id == f"entry-id_{handle}_A0"
    assert entity.device_info["identifiers"] == {(DOMAIN, handle)}
    assert entity.device_info["manufacturer"] == "EnOcean"
    assert entity.device_info["model"] == "PTM 216B"
    assert entity.device_info["name"] == "Living room switch"
    assert CANONICAL_ADDRESS not in entity.unique_id
    assert CANONICAL_ADDRESS not in str(entity.device_info["identifiers"])
    assert CANONICAL_ADDRESS not in repr(entity.device_info)


def test_event_entity_declares_all_four_press_action_event_types():
    entry = _make_entry()
    entity = Ptm216bButtonEventEntity(
        entry, CANONICAL_ADDRESS, "Switch", ButtonPattern.B1
    )

    assert entity.event_types == ["press", "release", "short_press", "long_press"]
    assert entity.name == "B1"


def test_event_entity_name_for_a_combo_pattern_is_its_plus_joined_value():
    entry = _make_entry()
    entity = Ptm216bButtonEventEntity(
        entry, CANONICAL_ADDRESS, "Switch", ButtonPattern.A0_B0
    )

    assert entity.name == "A0+B0"
    handle = entry.runtime_data.commissioned_device_handle(CANONICAL_ADDRESS)
    assert entity.unique_id == f"entry-id_{handle}_A0+B0"


async def test_event_entity_registers_and_unregisters_with_the_switch_runtime():
    entry = _make_entry()
    entity = Ptm216bButtonEventEntity(
        entry, CANONICAL_ADDRESS, "Switch", ButtonPattern.A0
    )
    switch_runtime = entry.runtime_data.commissioned_switch_runtime(CANONICAL_ADDRESS)

    await entity.async_added_to_hass()
    # Bound-method identity (`is`) is not guaranteed across attribute
    # accesses in Python; equality (same __self__/__func__) is the correct
    # check here.
    assert (
        switch_runtime.press_tracker._listeners[ButtonPattern.A0]
        == entity._handle_button_event
    )

    await entity.async_will_remove_from_hass()
    assert ButtonPattern.A0 not in switch_runtime.press_tracker._listeners


def test_handle_button_event_press_triggers_and_writes_state():
    entry = _make_entry()
    entity = Ptm216bButtonEventEntity(
        entry, CANONICAL_ADDRESS, "Switch", ButtonPattern.A0
    )
    entity.async_write_ha_state = Mock()

    entity._handle_button_event(PressAction.PRESS)

    assert entity.state_attributes["event_type"] == "press"
    entity.async_write_ha_state.assert_called_once_with()


def test_handle_button_event_release_triggers_and_writes_state():
    entry = _make_entry()
    entity = Ptm216bButtonEventEntity(
        entry, CANONICAL_ADDRESS, "Switch", ButtonPattern.A0
    )
    entity.async_write_ha_state = Mock()

    entity._handle_button_event(PressAction.RELEASE)

    assert entity.state_attributes["event_type"] == "release"
    entity.async_write_ha_state.assert_called_once_with()


def test_handle_button_event_short_press_triggers_and_writes_state():
    entry = _make_entry()
    entity = Ptm216bButtonEventEntity(
        entry, CANONICAL_ADDRESS, "Switch", ButtonPattern.A0
    )
    entity.async_write_ha_state = Mock()

    entity._handle_button_event(PressAction.SHORT_PRESS)

    assert entity.state_attributes["event_type"] == "short_press"
    entity.async_write_ha_state.assert_called_once_with()


def test_handle_button_event_long_press_triggers_and_writes_state():
    entry = _make_entry()
    entity = Ptm216bButtonEventEntity(
        entry, CANONICAL_ADDRESS, "Switch", ButtonPattern.A0
    )
    entity.async_write_ha_state = Mock()

    entity._handle_button_event(PressAction.LONG_PRESS)

    assert entity.state_attributes["event_type"] == "long_press"
    entity.async_write_ha_state.assert_called_once_with()


def test_handle_button_event_without_hass_skips_bus_fire_but_still_triggers():
    """A bare, unregistered entity (as constructed in every test above --
    ``self.hass``/``self.device_entry`` are never set) must never try to
    fire a bus event; it still triggers/writes state exactly as before.
    """
    entry = _make_entry()
    entity = Ptm216bButtonEventEntity(
        entry, CANONICAL_ADDRESS, "Switch", ButtonPattern.A0
    )
    entity.async_write_ha_state = Mock()
    assert entity.hass is None
    assert entity.device_entry is None

    entity._handle_button_event(PressAction.PRESS)  # must not raise

    assert entity.state_attributes["event_type"] == "press"


async def test_async_setup_entry_creates_six_entities_per_commissioned_switch(hass):
    """Phase 5D: a two-rocker switch gets all six ButtonPattern entities --
    the four single-button ones plus the two combo patterns A0+B0/A1+B1.
    """
    entry = await _commissioned_entry(hass, rockers=2)

    recorder = RecordingAddEntities()
    await async_setup_entry(hass, entry, recorder)

    assert len(recorder.added) == 6
    assert {entity._pattern for entity in recorder.added} == set(ButtonPattern)
    assert all(
        isinstance(entity, Ptm216bButtonEventEntity) for entity in recorder.added
    )
    (subentry_id,) = entry.subentries
    assert recorder.subentry_ids == [subentry_id] * 6


async def test_async_setup_entry_creates_only_a0_a1_for_a_single_rocker_switch(hass):
    entry = await _commissioned_entry(hass, rockers=1)

    recorder = RecordingAddEntities()
    await async_setup_entry(hass, entry, recorder)

    assert len(recorder.added) == 2
    assert {entity._pattern for entity in recorder.added} == {
        ButtonPattern.A0,
        ButtonPattern.A1,
    }


async def test_async_setup_entry_creates_nothing_when_no_switches_commissioned(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    store = CommissioningStore(hass)
    await store.async_load()
    entry.runtime_data = Ptm216bRuntimeData(
        _hmac_secret=SECRET, commissioning_store=store
    )
    entry.add_to_hass(hass)

    recorder = RecordingAddEntities()
    await async_setup_entry(hass, entry, recorder)

    assert recorder.added == []


async def test_only_the_matching_patterns_entity_fires(hass):
    entry = await _commissioned_entry(hass, rockers=2)

    recorder = RecordingAddEntities()
    await async_setup_entry(hass, entry, recorder)
    entities = {entity._pattern: entity for entity in recorder.added}
    for entity in entities.values():
        entity.async_write_ha_state = Mock()
        await entity.async_added_to_hass()

    switch_runtime = entry.runtime_data.commissioned_switch_runtime(CANONICAL_ADDRESS)
    switch_runtime.record_verified_and_fire(
        Ptm216bButtonState(pattern=ButtonPattern.B1, is_press=True)
    )

    assert entities[ButtonPattern.B1].state_attributes["event_type"] == "press"
    entities[ButtonPattern.B1].async_write_ha_state.assert_called_once_with()
    for pattern in (
        ButtonPattern.A0,
        ButtonPattern.A1,
        ButtonPattern.B0,
        ButtonPattern.A0_B0,
        ButtonPattern.A1_B1,
    ):
        assert entities[pattern].state is None
        entities[pattern].async_write_ha_state.assert_not_called()


async def test_combo_pattern_telegram_fires_its_own_combo_entity(hass):
    """A0+B0 is its own distinct entity on a two-rocker switch -- a
    genuinely simultaneous press fires only the combo entity, not A0 or B0.
    """
    entry = await _commissioned_entry(hass, rockers=2)

    recorder = RecordingAddEntities()
    await async_setup_entry(hass, entry, recorder)
    entities = {entity._pattern: entity for entity in recorder.added}
    for entity in entities.values():
        entity.async_write_ha_state = Mock()
        await entity.async_added_to_hass()

    switch_runtime = entry.runtime_data.commissioned_switch_runtime(CANONICAL_ADDRESS)
    switch_runtime.record_verified_and_fire(
        Ptm216bButtonState(pattern=ButtonPattern.A0_B0, is_press=True)
    )

    assert entities[ButtonPattern.A0_B0].state_attributes["event_type"] == "press"
    for pattern in (ButtonPattern.A0, ButtonPattern.B0):
        entities[pattern].async_write_ha_state.assert_not_called()


async def test_1_rocker_aliasing_fires_the_logical_a0_and_a1_entities_only(hass):
    """A one-rocker switch's three raw press-side patterns for each logical
    button (A0/B0/A0+B0 -> A0, A1/B1/A1+B1 -> A1) all fire the SAME logical
    entity -- proving ``telegram.normalize_button_pattern`` is actually
    wired through ``runtime_data.CommissionedSwitchRuntime.
    record_verified_and_fire`` end-to-end, not just unit-tested in
    isolation (see test_telegram.py for the pure aliasing-table tests).
    """
    entry = await _commissioned_entry(hass, rockers=1)

    recorder = RecordingAddEntities()
    await async_setup_entry(hass, entry, recorder)
    entities = {entity._pattern: entity for entity in recorder.added}
    for entity in entities.values():
        entity.async_write_ha_state = Mock()
        await entity.async_added_to_hass()

    switch_runtime = entry.runtime_data.commissioned_switch_runtime(CANONICAL_ADDRESS)
    assert switch_runtime.rockers == 1

    for raw_pattern in (ButtonPattern.A0, ButtonPattern.B0, ButtonPattern.A0_B0):
        entities[ButtonPattern.A0].async_write_ha_state.reset_mock()
        switch_runtime.record_verified_and_fire(
            Ptm216bButtonState(pattern=raw_pattern, is_press=True)
        )
        assert entities[ButtonPattern.A0].state_attributes["event_type"] == "press"
        entities[ButtonPattern.A0].async_write_ha_state.assert_called_once_with()

    for raw_pattern in (ButtonPattern.A1, ButtonPattern.B1, ButtonPattern.A1_B1):
        entities[ButtonPattern.A1].async_write_ha_state.reset_mock()
        switch_runtime.record_verified_and_fire(
            Ptm216bButtonState(pattern=raw_pattern, is_press=True)
        )
        assert entities[ButtonPattern.A1].state_attributes["event_type"] == "press"
        entities[ButtonPattern.A1].async_write_ha_state.assert_called_once_with()

    # Every aliased telegram still counted as verified even though only two
    # logical entities exist for six raw patterns.
    assert switch_runtime.verified_count == 6


# ---------------------------------------------------------------------------
# Phase 5B: press_tracker threshold wiring + short_press/long_press routing
# ---------------------------------------------------------------------------


async def test_async_setup_entry_wires_the_default_threshold_when_field_is_absent(
    hass,
):
    """A pre-Phase-5B subentry has no ``long_press_threshold_ms`` field at
    all; ``async_setup_entry`` must still configure the tracker with the
    default rather than leaving it unset/erroring.
    """
    entry = await _commissioned_entry(hass, threshold_ms=None)

    recorder = RecordingAddEntities()
    await async_setup_entry(hass, entry, recorder)

    switch_runtime = entry.runtime_data.commissioned_switch_runtime(CANONICAL_ADDRESS)
    assert switch_runtime.press_tracker.threshold_ms == DEFAULT_LONG_PRESS_THRESHOLD_MS
    assert switch_runtime.press_tracker.scheduler is not None


async def test_async_setup_entry_wires_a_custom_threshold_from_subentry_data(hass):
    entry = await _commissioned_entry(hass, threshold_ms=1500)

    recorder = RecordingAddEntities()
    await async_setup_entry(hass, entry, recorder)

    switch_runtime = entry.runtime_data.commissioned_switch_runtime(CANONICAL_ADDRESS)
    assert switch_runtime.press_tracker.threshold_ms == 1500


async def test_short_press_and_long_press_route_through_to_the_entity(hass):
    """End-to-end (minus real Bluetooth wiring): a verified press followed
    by the tracker's own hold timer firing reaches the button entity as
    short_press/long_press, exactly like raw press/release already did.
    """
    entry = await _commissioned_entry(hass, threshold_ms=500)
    recorder = RecordingAddEntities()
    await async_setup_entry(hass, entry, recorder)
    entities = {entity._pattern: entity for entity in recorder.added}
    for entity in entities.values():
        entity.async_write_ha_state = Mock()
        await entity.async_added_to_hass()

    switch_runtime = entry.runtime_data.commissioned_switch_runtime(CANONICAL_ADDRESS)
    switch_runtime.record_verified_and_fire(
        Ptm216bButtonState(pattern=ButtonPattern.A0, is_press=True)
    )
    assert entities[ButtonPattern.A0].state_attributes["event_type"] == "press"

    # Fire the tracker's own hold timer directly -- this test does not
    # depend on real wall-clock time, only on the scheduler actually being
    # wired (see test_async_setup_entry_wires_the_default_threshold_when_
    # field_is_absent for that wiring itself).
    open_press = switch_runtime.press_tracker._open[ButtonPattern.A0]
    assert open_press.cancel_timer is not None  # a real cancel handle was stored
    switch_runtime.press_tracker._fire_long_press(ButtonPattern.A0, open_press)

    assert entities[ButtonPattern.A0].state_attributes["event_type"] == "long_press"

    switch_runtime.record_verified_and_fire(
        Ptm216bButtonState(pattern=ButtonPattern.A0, is_press=False)
    )
    assert entities[ButtonPattern.A0].state_attributes["event_type"] == "release"


# ---------------------------------------------------------------------------
# Phase 5D: unique_id stability across the upgrade + orphan-entity cleanup
# ---------------------------------------------------------------------------


async def test_a0_a1_b0_b1_unique_ids_are_byte_for_byte_stable_across_the_upgrade(
    hass,
):
    """ButtonPattern.A0.value == "A0" etc. are identical strings to the old
    Button enum's values, so every pre-existing single-button entity's
    unique_id -- and therefore its entity_id, area assignment, and any
    automation/dashboard reference to it -- is completely unaffected by
    this upgrade. Only the two new combo entities get new unique_ids.
    """
    entry = await _commissioned_entry(hass, rockers=2)
    handle = entry.runtime_data.commissioned_device_handle(CANONICAL_ADDRESS)

    recorder = RecordingAddEntities()
    await async_setup_entry(hass, entry, recorder)
    entities = {entity._pattern: entity for entity in recorder.added}

    assert entities[ButtonPattern.A0].unique_id == f"{entry.entry_id}_{handle}_A0"
    assert entities[ButtonPattern.A1].unique_id == f"{entry.entry_id}_{handle}_A1"
    assert entities[ButtonPattern.B0].unique_id == f"{entry.entry_id}_{handle}_B0"
    assert entities[ButtonPattern.B1].unique_id == f"{entry.entry_id}_{handle}_B1"
    assert entities[ButtonPattern.A0_B0].unique_id == f"{entry.entry_id}_{handle}_A0+B0"
    assert entities[ButtonPattern.A1_B1].unique_id == f"{entry.entry_id}_{handle}_A1+B1"


async def test_orphan_event_entities_are_pruned_when_rockers_changes_from_two_to_one(
    hass,
):
    """When a switch's "rockers" subentry field changes from 2 to 1 (via
    Reconfigure), the resulting reload re-runs ``async_setup_entry`` and it
    now only builds two entities (A0/A1) instead of six --
    ``async_add_entities`` is purely additive and does NOT, by itself,
    remove the four now-stale registry entries (B0, B1, A0+B0, A1+B1).
    This is NOT the same mechanism as Home Assistant's own subentry-
    *deletion* cleanup (see test_subentry_removal.py): the subentry here
    still exists, only its entity set shrinks, so that mechanism never
    fires and event.py's own ``_prune_stale_event_entities`` is what
    removes them.

    This test seeds the entity registry directly with the six entries a
    real first ``rockers=2`` setup would have produced (this repo's own
    established convention for entity/device-registry state in tests that
    do not go through a full, Bluetooth-dependent config-entry setup -- see
    test_subentry_removal.py's identical use of
    ``device_registry.async_get_or_create`` for the device side), then
    calls ``event.async_setup_entry`` again with the subentry's data
    updated to ``rockers=1`` -- the same platform-forwarding entry point a
    real ``hass.config_entries.async_reload`` drives via
    ``async_forward_entry_setups`` (see ``__init__.py``'s
    ``_async_subentries_changed``) -- exactly mirroring how every other
    test in this file already exercises this platform directly rather than
    through the full, dbus_fast-dependent component setup (see
    test_device_trigger.py's own docstring for that same constraint).
    """
    entry = await _commissioned_entry(hass, rockers=2)
    (subentry_id,) = entry.subentries
    handle = entry.runtime_data.commissioned_device_handle(CANONICAL_ADDRESS)

    registry = er.async_get(hass)
    for pattern in ButtonPattern:
        registry.async_get_or_create(
            "event",
            DOMAIN,
            f"{entry.entry_id}_{handle}_{pattern.value}",
            config_entry=entry,
            config_subentry_id=subentry_id,
        )
    live_before = {
        e.unique_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    assert live_before == {
        f"{entry.entry_id}_{handle}_{pattern.value}" for pattern in ButtonPattern
    }

    subentry = entry.subentries[subentry_id]
    hass.config_entries.async_update_subentry(
        entry, subentry, data={**subentry.data, "rockers": 1}
    )

    recorder = RecordingAddEntities()
    await async_setup_entry(hass, entry, recorder)

    live_after = {
        e.unique_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    assert live_after == {
        f"{entry.entry_id}_{handle}_A0",
        f"{entry.entry_id}_{handle}_A1",
    }


async def test_orphan_cleanup_never_touches_a_different_subentrys_entities(hass):
    """Two commissioned switches (two subentries) under the same config
    entry: pruning stale entities for one must never remove the other's
    live entities, even though both share ``config_entry_id``.
    """
    entry = await _commissioned_entry(hass, name="Switch one", rockers=2)
    (subentry_one_id,) = entry.subentries
    handle_one = entry.runtime_data.commissioned_device_handle(CANONICAL_ADDRESS)

    other_address = canonicalize_address("11:22:33:44:55:66")
    store = entry.runtime_data.commissioning_store
    await store.async_add(other_address, bytes(range(16, 32)), "Switch two")
    other_handle = entry.runtime_data.commissioned_device_handle(other_address)
    from homeassistant.config_entries import ConfigSubentry

    other_subentry = ConfigSubentry(
        data={"handle": other_handle, "name": "Switch two", "rockers": 1},
        subentry_type="switch",
        title="Switch two",
        unique_id=other_handle,
    )
    hass.config_entries.async_add_subentry(entry, other_subentry)
    (subentry_two_id,) = [sid for sid in entry.subentries if sid != subentry_one_id]

    # Seed the registry as a real first setup would have left it: switch
    # one's six correct entries PLUS one stale leftover unique_id (as if
    # switch one itself had shrunk from six patterns to five on some
    # earlier reload) -- this is what should get pruned. Switch two's two
    # correct entries must survive completely untouched, even though
    # pruning runs once per subentry within the SAME config entry.
    registry = er.async_get(hass)
    for pattern in ButtonPattern:
        registry.async_get_or_create(
            "event",
            DOMAIN,
            f"{entry.entry_id}_{handle_one}_{pattern.value}",
            config_entry=entry,
            config_subentry_id=subentry_one_id,
        )
    stale_unique_id = f"{entry.entry_id}_{handle_one}_stale-leftover"
    registry.async_get_or_create(
        "event",
        DOMAIN,
        stale_unique_id,
        config_entry=entry,
        config_subentry_id=subentry_one_id,
    )
    for pattern in (ButtonPattern.A0, ButtonPattern.A1):
        registry.async_get_or_create(
            "event",
            DOMAIN,
            f"{entry.entry_id}_{other_handle}_{pattern.value}",
            config_entry=entry,
            config_subentry_id=subentry_two_id,
        )

    recorder = RecordingAddEntities()
    await async_setup_entry(hass, entry, recorder)

    live_unique_ids = {
        e.unique_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    expected = {
        f"{entry.entry_id}_{handle_one}_{pattern.value}" for pattern in ButtonPattern
    } | {
        f"{entry.entry_id}_{other_handle}_A0",
        f"{entry.entry_id}_{other_handle}_A1",
    }
    assert live_unique_ids == expected
    assert stale_unique_id not in live_unique_ids


async def test_verified_telegram_on_single_rocker_switch_fires_the_aliased_logical_entity(
    hass,
):
    """Before Phase 5D, a rockers==1 switch's B0/B1 telegrams simply had no
    entity to fire, since B0/B1 were never created for it. As of Phase 5D,
    ``normalize_button_pattern`` aliases every raw B0 telegram to logical
    A0 (and B1 to A1) BEFORE it reaches an entity listener -- see
    telegram.py's module docstring -- so it now fires the A0 entity
    instead of nothing. This is the intended, documented 1-rocker aliasing
    behavior (see README.md's "Six button states and 1-rocker aliasing"
    section), not a regression.
    """
    entry = await _commissioned_entry(hass, rockers=1)

    recorder = RecordingAddEntities()
    await async_setup_entry(hass, entry, recorder)
    for entity in recorder.added:
        entity.async_write_ha_state = Mock()
        await entity.async_added_to_hass()
    entities = {entity._pattern: entity for entity in recorder.added}

    switch_runtime = entry.runtime_data.commissioned_switch_runtime(CANONICAL_ADDRESS)
    switch_runtime.record_verified_and_fire(
        Ptm216bButtonState(pattern=ButtonPattern.B0, is_press=True)
    )

    assert switch_runtime.verified_count == 1
    assert entities[ButtonPattern.A0].state_attributes["event_type"] == "press"
    entities[ButtonPattern.A0].async_write_ha_state.assert_called_once_with()
