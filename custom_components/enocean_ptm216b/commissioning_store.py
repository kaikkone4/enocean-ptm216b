"""Private, local-only storage for user-commissioned PTM 216B switches.

This is a DELIBERATE, documented exception to the "no addresses persisted"
rule that governs every other module in this integration
(``identity.py``, ``designated_sessions.py``, ``evidence_capture.py``): a
commissioned switch is user-owned and explicitly trusted through a local
commissioning flow the user themselves runs (see ``config_flow.py``'s
``commission_switch``/``decommission_switch`` steps), so its BLE address and
its 16-byte device-specific security key must be retained -- otherwise no
future advertisement from that switch could ever be matched back to its
key for MIC verification. Both live ONLY here, in this private Home
Assistant ``Store`` and the in-memory cache that mirrors it -- NEVER in
config-entry data, entity state/attributes, diagnostics, logs, or UI. See
README.md, "Commissioning and button events (Phase 4)", for the exact
boundary this module enforces.

The stored shape, per canonical address (``identity.canonicalize_address``
output), is ``{"key": <base64 of 16 raw key bytes>, "name": <str>,
"counter": <int | None>}``. ``counter`` is the durably persisted PTM 216B
sequence counter for replay protection (see ``replay_guard.py`` and
``button_pipeline.py``); it starts ``None`` until the first cryptographically
verified telegram for that switch initializes it (first-trust -- see
``button_pipeline.py``'s module docstring).

Neither this class nor :class:`CommissioningKeyLengthError` ever includes a
key or address in ``repr()``: :class:`CommissioningStore` is a plain object
with no custom ``__repr__`` (so the default ``object.__repr__`` -- class
name and id only -- is all it ever exposes), :class:`CommissionedSwitch`
marks its sensitive fields ``repr=False``, and the canonical address itself
is never stored as a field on either dataclass -- only as a private
dictionary key inside :class:`CommissioningStore`.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_STORAGE_KEY = "enocean_ptm216b.commissioned_switches"
_STORAGE_VERSION = 1
_KEY_LENGTH = 16


class CommissioningKeyLengthError(Exception):
    """Fail-closed precondition failure: the supplied key is not 16 bytes.

    Mirrors :class:`crypto.KeyLengthError` -- never includes the key itself,
    only its (non-sensitive) length, so it is always safe to log.
    """

    def __init__(self, length: int) -> None:
        self.length = length
        super().__init__(f"key must be exactly {_KEY_LENGTH} bytes (got {length})")


@dataclass
class CommissionedSwitch:
    """One commissioned switch's in-memory-cached record.

    The canonical address is deliberately NOT a field here -- it only ever
    exists as the dictionary key in :attr:`CommissioningStore.switches` --
    so this object's own ``repr()`` can never include it. ``key`` and
    ``counter`` are both ``repr=False``: ``key`` is the raw 16-byte device
    secret, and ``counter`` is the PTM 216B sequence counter, which the
    fail-closed decoder contract treats as sensitive-by-convention exactly
    like every other module in this repo (see
    ``evidence_capture.EvidenceSummary``, which exposes counter *deltas*
    but never an absolute counter, for the same reasoning).
    """

    key: bytes = field(repr=False)
    name: str
    counter: int | None = field(default=None, repr=False)


class CommissioningStore:
    """Load, cache, and durably persist commissioned switches.

    ``async_load`` must be awaited once during setup, before any lookup;
    every other method operates on (and, where noted, persists) the
    resulting in-memory cache. The counter mutation path is deliberately
    minimal and synchronous/explicit: :meth:`set_counter` only ever updates
    the in-memory cache, and the caller (``button_pipeline.py``) is always
    responsible for ``await``-ing :meth:`async_save` before treating an
    accepted counter as durable -- mirroring ``replay_guard.py``'s
    getter/setter injection convention, where the pure decision function
    updates in-memory state synchronously and the caller owns persistence.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, dict[str, object]]] = Store(
            hass, _STORAGE_VERSION, _STORAGE_KEY, private=True
        )
        self._cache: dict[str, CommissionedSwitch] = {}

    @property
    def switches(self) -> dict[str, CommissionedSwitch]:
        """Return the live in-memory cache, keyed by canonical address."""
        return self._cache

    async def async_load(self) -> None:
        """Load the private store into the in-memory cache; call once at setup."""
        stored = await self._store.async_load()
        self._cache = {}
        if stored is None:
            return
        for address, record in stored.items():
            self._cache[address] = CommissionedSwitch(
                key=base64.b64decode(record["key"], validate=True),
                name=record["name"],
                counter=record.get("counter"),
            )

    def get(self, canonical_address: str) -> CommissionedSwitch | None:
        """Return the cached record for one canonical address, or ``None``."""
        return self._cache.get(canonical_address)

    def get_counter(self, canonical_address: str) -> int | None:
        """Return the cached persisted counter, or ``None`` if never set."""
        switch = self._cache.get(canonical_address)
        return None if switch is None else switch.counter

    def set_counter(self, canonical_address: str, counter: int) -> None:
        """Advance the in-memory counter only.

        The caller MUST ``await`` :meth:`async_save` before treating this
        advance as durable/observable -- see the class docstring and
        ``button_pipeline.py``'s durable-save-before-event handling.
        """
        self._cache[canonical_address].counter = counter

    async def async_add(self, canonical_address: str, key: bytes, name: str) -> None:
        """Commission one switch: trust its key locally and persist durably.

        The counter starts at ``None`` -- first-trust initialization is the
        runtime pipeline's job (from the first cryptographically *verified*
        telegram), never an unauthenticated starting value chosen here.
        """
        if len(key) != _KEY_LENGTH:
            raise CommissioningKeyLengthError(len(key))
        self._cache[canonical_address] = CommissionedSwitch(
            key=key, name=name, counter=None
        )
        await self.async_save()

    async def async_remove(self, canonical_address: str) -> None:
        """Decommission one switch: discard its key/counter and persist durably."""
        self._cache.pop(canonical_address, None)
        await self.async_save()

    async def async_save(self) -> None:
        """Persist the full in-memory cache -- including any counter change."""
        payload = {
            address: {
                "key": base64.b64encode(switch.key).decode("ascii"),
                "name": switch.name,
                "counter": switch.counter,
            }
            for address, switch in self._cache.items()
        }
        await self._store.async_save(payload)
