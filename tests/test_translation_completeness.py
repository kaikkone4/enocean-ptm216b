"""Translation-completeness tests for the Phase 6 Hue-grade event/trigger
strings.

Data-driven off the actual enums that determine what event types and device
triggers this integration can ever produce (``press_timing.PressAction`` and
``telegram.ButtonPattern``) -- NOT a hardcoded list of keys -- so a future
addition to either enum (a new press action, a new button pattern) makes
this test fail immediately instead of silently shipping an untranslated
raw key to the device page or automation editor. This is exactly the drift
these tests are meant to catch; see event.py's and device_trigger.py's own
module docstrings for how those enums drive entity/trigger creation.

Also enforces this repo's own established convention (see every other
string in strings.json/translations/en.json): ``strings.json`` and
``translations/en.json`` must stay byte-for-byte identical in content.
"""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.enocean_ptm216b.press_timing import PressAction
from custom_components.enocean_ptm216b.telegram import ButtonPattern

COMPONENT_DIR = (
    Path(__file__).resolve().parents[1] / "custom_components" / "enocean_ptm216b"
)

_EVENT_TYPES = [action.value for action in PressAction]
_TRIGGER_SUBTYPES = [pattern.value for pattern in ButtonPattern]


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _strings() -> dict:
    return _load(COMPONENT_DIR / "strings.json")


def _en() -> dict:
    return _load(COMPONENT_DIR / "translations" / "en.json")


def _fi() -> dict:
    return _load(COMPONENT_DIR / "translations" / "fi.json")


def test_strings_json_and_en_json_are_byte_for_byte_identical():
    """This repo's own convention (see every existing key): strings.json IS
    the English translation; translations/en.json mirrors it exactly so
    the two can never drift out of sync.
    """
    assert _strings() == _en()


def test_every_press_action_event_type_has_an_entity_event_button_state_label():
    """``entity.event.button.state_attributes.event_type.state`` is what
    renders the translated last-event description on the device page's
    Events card (e.g. Finnish "Lyhyt painallus" instead of the raw
    "short_press") -- every value ``press_timing.PressAction`` can ever
    fire must have a key here, in both languages.
    """
    for data, label in ((_strings(), "strings.json"), (_fi(), "fi.json")):
        state_labels = data["entity"]["event"]["button"]["state_attributes"][
            "event_type"
        ]["state"]
        for event_type in _EVENT_TYPES:
            assert event_type in state_labels, (
                f"{label} is missing entity.event.button.state_attributes."
                f"event_type.state.{event_type!r}"
            )
            assert state_labels[event_type].strip(), (
                f"{label}'s label for event_type {event_type!r} is empty"
            )


def test_entity_event_button_name_has_the_button_placeholder():
    """``entity.event.button.name`` must reference ``{button}`` -- this is
    exactly what ``event.py``'s ``_attr_translation_placeholders =
    {"button": pattern.value}`` substitutes into, matching Hue's own
    ``{button_id}`` convention for its button event entities.
    """
    for data, label in ((_strings(), "strings.json"), (_fi(), "fi.json")):
        name = data["entity"]["event"]["button"]["name"]
        assert "{button}" in name, (
            f"{label}'s entity.event.button.name has no {{button}} placeholder"
        )


def test_every_button_pattern_has_a_device_automation_trigger_subtype():
    """Every ``telegram.ButtonPattern`` value (the six raw+combo patterns a
    two-rocker switch's entities/triggers are built from -- see
    ``event.py``'s and ``device_trigger.py``'s own module docstrings) must
    have a friendly ``device_automation.trigger_subtype`` label, in both
    languages, so the automation editor never shows a raw "A0+B0" token
    where a "Buttons A0+B0 together"-style label belongs.
    """
    for data, label in ((_strings(), "strings.json"), (_fi(), "fi.json")):
        subtypes = data["device_automation"]["trigger_subtype"]
        for pattern_value in _TRIGGER_SUBTYPES:
            assert pattern_value in subtypes, (
                f"{label} is missing device_automation.trigger_subtype."
                f"{pattern_value!r}"
            )
            assert subtypes[pattern_value].strip(), (
                f"{label}'s trigger_subtype label for {pattern_value!r} is empty"
            )


def test_every_press_action_has_a_device_automation_trigger_type():
    """Companion check to the subtype test above -- every action
    ``device_trigger.py``'s ``_TRIGGER_TYPES`` can offer needs a
    ``device_automation.trigger_type`` label too (pre-existing, but kept
    data-driven here so it stays covered alongside the newer subtype
    check rather than silently relying on a separate, hand-written list).
    """
    for data, label in ((_strings(), "strings.json"), (_fi(), "fi.json")):
        trigger_types = data["device_automation"]["trigger_type"]
        for event_type in _EVENT_TYPES:
            assert event_type in trigger_types, (
                f"{label} is missing device_automation.trigger_type.{event_type!r}"
            )
            assert trigger_types[event_type].strip(), (
                f"{label}'s trigger_type label for {event_type!r} is empty"
            )


def test_trigger_subtype_and_state_label_key_sets_have_no_stray_entries():
    """Guards the other direction too: no leftover/typo'd key for a pattern
    or event type that no longer exists (or never did), in either
    language -- keeps the translation files exactly in sync with the
    enums, not just a superset of them.
    """
    for data, label in ((_strings(), "strings.json"), (_fi(), "fi.json")):
        subtypes = set(data["device_automation"]["trigger_subtype"])
        assert subtypes == set(_TRIGGER_SUBTYPES), (
            f"{label}'s device_automation.trigger_subtype keys "
            f"{subtypes} do not exactly match ButtonPattern values "
            f"{set(_TRIGGER_SUBTYPES)}"
        )

        trigger_types = set(data["device_automation"]["trigger_type"])
        assert trigger_types == set(_EVENT_TYPES), (
            f"{label}'s device_automation.trigger_type keys {trigger_types} "
            f"do not exactly match PressAction values {set(_EVENT_TYPES)}"
        )

        state_labels = set(
            data["entity"]["event"]["button"]["state_attributes"]["event_type"]["state"]
        )
        assert state_labels == set(_EVENT_TYPES), (
            f"{label}'s entity.event.button.state_attributes.event_type.state "
            f"keys {state_labels} do not exactly match PressAction values "
            f"{set(_EVENT_TYPES)}"
        )
