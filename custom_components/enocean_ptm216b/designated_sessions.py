"""Pure runtime counting for a designated pseudonymous test device."""

from __future__ import annotations

SESSION_INACTIVITY_GAP_SECONDS = 1.0


class DesignatedSessionCounter:
    """Count observations and bursts for one pseudonymous identifier."""

    def __init__(self, designated_identifier: str | None = None) -> None:
        self._designated_identifier = designated_identifier
        self._last_observation_time: float | None = None
        self.observation_count = 0
        self.session_count = 0

    def observe(self, identifier: str, monotonic_time: float) -> None:
        """Record a designated observation using an injected monotonic timestamp."""
        if identifier != self._designated_identifier:
            return

        self.observation_count += 1
        if (
            self._last_observation_time is None
            or monotonic_time - self._last_observation_time
            >= SESSION_INACTIVITY_GAP_SECONDS
        ):
            self.session_count += 1
        self._last_observation_time = monotonic_time
