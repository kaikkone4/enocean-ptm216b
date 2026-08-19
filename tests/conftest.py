import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(request):
    """Enable custom integrations in the Home Assistant test environment."""
    try:
        request.getfixturevalue("enable_custom_integrations")
    except pytest.FixtureLookupError:
        pass


class RecordingAddEntities:
    """A minimal stand-in for ``AddEntitiesCallback`` that also accepts
    ``config_subentry_id`` (the real callback does; ``list.extend`` does
    not) and records which subentry each call's entities were added under.
    """

    def __init__(self) -> None:
        self.added: list = []
        self.subentry_ids: list[str | None] = []

    def __call__(
        self, new_entities, update_before_add=False, *, config_subentry_id=None
    ):
        entities = list(new_entities)
        self.added.extend(entities)
        self.subentry_ids.extend([config_subentry_id] * len(entities))
