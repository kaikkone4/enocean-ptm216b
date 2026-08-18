"""Per-config-entry runtime state for the passive observer."""

from dataclasses import dataclass


@dataclass
class Ptm216bRuntimeData:
    """Ephemeral passive-observation state; no keys or raw payloads are retained."""

    advertisement_count: int = 0
