import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.enocean_ptm216b import qr_decode  # noqa: E402


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(request):
    """Enable custom integrations in the Home Assistant test environment."""
    try:
        request.getfixturevalue("enable_custom_integrations")
    except pytest.FixtureLookupError:
        pass


@pytest.fixture(autouse=True)
def _reset_qr_decoder_install_state(monkeypatch):
    """Keep the lazy pyrxing installer out of every test that isn't about it.

    qr_decode.async_ensure_qr_decoder remembers a failed lazy-install
    attempt for the rest of the process (see its docstring: one attempt per
    Home Assistant start). Without this fixture, the FIRST test in the
    whole session to reach the Add-device wizard's key-entry step would
    trigger one real pip install via Home Assistant's requirements manager
    -- slow, network-dependent, and it would poison every later test's
    "already attempted" state for the rest of the run. This resets that
    memory before each test and stubs the installer to fail fast (as if no
    wheel were installable), so tests that don't specifically exercise
    qr_decode's install path see a deterministic, instant "unavailable"
    rather than a real network call. Tests that DO test the install path
    (tests/test_qr_decode.py) patch qr_decode.async_process_requirements
    themselves, on top of this fixture, for the case they want.
    """
    monkeypatch.setattr(qr_decode, "_install_attempted", False)

    async def _fail_fast(hass, name, requirements):
        raise qr_decode.RequirementsNotFound(name, requirements)

    monkeypatch.setattr(qr_decode, "async_process_requirements", _fail_fast)


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
