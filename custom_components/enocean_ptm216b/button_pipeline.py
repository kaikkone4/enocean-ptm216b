"""Fail-closed pipeline: commissioned-switch advertisements -> button events.

Implements docs/decoder-test-preparation.md's "Fail-closed decoder contract"
for exactly the subset of advertisements that match a canonical address
already commissioned via ``config_flow.py``'s per-switch Add-device wizard
(``SwitchSubentryFlow``; see ``commissioning_store.py``). Every other
advertisement -- unrecognized
address, uncommissioned switch -- is untouched by this module; the existing
designation/evidence/observation call path in
``runtime_data.Ptm216bRuntimeData.record_advertisement_observation`` keeps
running exactly as before, alongside this pipeline, never instead of it.

Gate order, matching the gate doc exactly: exact 9-byte supported shape ->
MIC verified -> counter strictly greater than the durably persisted value ->
status decoded to exactly one button. No stage after a failing gate ever
runs. Shape and MIC verification are synchronous and share no mutable
per-switch state, so a shape- or MIC-rejected telegram is counted and
discarded with no async hop at all (gate doc item 4: no state update on MIC
failure). Only once a telegram's MIC verifies does this module hand off to
an async, per-switch-locked task, because the counter/replay gate mutates
durable state that concurrent callbacks for the SAME switch must serialize
against (item 6: "Concurrent callbacks must not both accept the same
counter") -- and because the Bluetooth callback that ultimately drives this
module is a synchronous ``@callback`` that cannot itself await.

First-trust policy: a commissioned switch's first-ever MIC-verified
telegram initializes its persisted counter without accepting or rejecting a
button action. This is NOT an unauthenticated fallback -- the MIC still had
to verify cryptographically -- but it also does not, by itself, prove
freshness the way every later telegram's counter check does (there is no
prior counter to compare against), so it cannot be treated as an
authenticated button *action* either. It is therefore counted as neither
"verified" nor "rejected"; see
``runtime_data.CommissionedSwitchRuntime.record_verified_and_fire`` and
``.record_rejected`` for the exact counting rule this pipeline drives.

Only coarse, non-identifying facts ever reach a listener or a log line here:
this module never logs an address, key, counter, or telegram byte -- the
counters it drives are plain per-switch integers on
``runtime_data.CommissionedSwitchRuntime``.
"""

from __future__ import annotations

from typing import Callable, Coroutine

from .commissioning_store import CommissioningStore
from .const import ENOCEAN_MANUFACTURER_ID
from .crypto import verify_telegram_mic
from .identity import canonicalize_address
from .replay_guard import ReplayOutcome, evaluate_sequence_counter
from .runtime_data import CommissionedSwitchRuntime, Ptm216bRuntimeData
from .telegram import (
    Ptm216bTelegram,
    StatusParseError,
    TelegramParseError,
    interpret_switch_status,
    parse_data_telegram,
)

CreateTask = Callable[[Coroutine[object, object, None]], object]


def handle_advertisement(
    runtime: Ptm216bRuntimeData,
    address: str,
    manufacturer_data: dict[int, bytes],
    create_task: CreateTask,
) -> None:
    """Entry point for every matching Bluetooth advertisement.

    A no-op whenever the address does not canonicalize, or canonicalizes to
    an address that is not currently commissioned -- in both cases nothing
    here runs, and the caller's existing
    ``record_advertisement_observation`` call is unaffected either way.

    Shape and MIC checks happen inline, synchronously, with no async hop:
    a shape-rejected or MIC-invalid telegram is counted as rejected and
    the function returns immediately. Only a MIC-verified telegram is handed
    to ``create_task`` (normally ``hass.async_create_task``) for the
    lock-serialized counter/status stage in
    :func:`_process_verified_telegram`.
    """
    store = runtime.commissioning_store
    if store is None:
        return
    try:
        canonical_address = canonicalize_address(address)
    except ValueError:
        return
    switch = store.get(canonical_address)
    if switch is None:
        return

    switch_runtime = runtime.commissioned_switch_runtime(canonical_address)

    value = manufacturer_data.get(ENOCEAN_MANUFACTURER_ID)
    if value is None:
        switch_runtime.record_rejected()
        return

    try:
        telegram = parse_data_telegram(value)
    except TelegramParseError:
        switch_runtime.record_rejected()
        return

    if not verify_telegram_mic(switch.key, canonical_address, telegram):
        switch_runtime.record_rejected()
        return

    create_task(
        _process_verified_telegram(store, switch_runtime, canonical_address, telegram)
    )


async def _process_verified_telegram(
    store: CommissioningStore,
    switch_runtime: CommissionedSwitchRuntime,
    canonical_address: str,
    telegram: Ptm216bTelegram,
) -> None:
    """Counter/replay + status-decode stage; only ever runs inside the lock.

    Re-reads the persisted counter from the store's in-memory cache fresh,
    *inside* the lock -- never from a snapshot taken before this task was
    scheduled -- because another concurrent task for the SAME switch may
    have advanced it while this one was waiting for the lock. This, plus
    the ``asyncio.Lock`` itself, is what makes two simultaneous callbacks
    delivering the same counter value resolve to exactly one ACCEPTED and
    one DUPLICATE/REPLAY_REJECTED, never two ACCEPTED.
    """
    async with switch_runtime.lock:
        switch = store.get(canonical_address)
        if switch is None:
            # Decommissioned while this task was scheduled/waiting on the lock.
            return

        if switch.counter is None:
            # First-trust initialization: durable, but neither an accepted
            # nor a rejected button action -- see module docstring.
            store.set_counter(canonical_address, telegram.sequence_counter)
            await store.async_save()
            return

        outcome = evaluate_sequence_counter(
            canonical_address,
            telegram.sequence_counter,
            lambda _identifier: store.get_counter(canonical_address),
            lambda _identifier, counter: store.set_counter(canonical_address, counter),
        )

        if outcome is not ReplayOutcome.ACCEPTED:
            # DUPLICATE or REPLAY_REJECTED: in-memory state did not change,
            # so no save is needed.
            switch_runtime.record_rejected()
            return

        # Durable-save-before-event: the counter advance must be persisted
        # before any event becomes observable.
        await store.async_save()

        try:
            button_state = interpret_switch_status(telegram.switch_status)
        except StatusParseError:
            # The counter has already durably advanced -- that is correct
            # per the gate doc's fixed gate order: status is intentionally
            # the last, independent gate.
            switch_runtime.record_rejected()
            return

        switch_runtime.record_verified_and_fire(button_state)
