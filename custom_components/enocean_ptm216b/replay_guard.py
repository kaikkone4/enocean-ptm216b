"""Pure replay/duplicate decision logic for PTM 216B sequence counters.

Implements User Manual section 5.1.3 and docs/decoder-test-preparation.md,
"Fail-closed decoder contract", items 5-7: accept only a strictly higher
counter than the persisted one, treat an equal counter as an authenticated
duplicate no-op, reject a lower counter as a replay, and never auto-reset
persisted state. This module has no persistence of its own -- the caller
injects a getter/setter pair (matching this repo's
``evidence_capture.EvidenceScheduler``-style callback-injection convention)
so a later phase can back it with Home Assistant's ``Store`` without
touching this pure logic. It is completely unwired: nothing in this phase
calls it from a Bluetooth callback.
"""

from __future__ import annotations

from enum import Enum
from typing import Callable

# device identifier -> persisted counter, or None if this device has never
# had a counter accepted before.
ReplayCounterGetter = Callable[[str], "int | None"]
# device identifier, newly accepted counter -> persist it.
ReplayCounterSetter = Callable[[str, int], None]


class ReplayOutcome(Enum):
    """Typed decision for one (device identifier, received counter) pair."""

    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    REPLAY_REJECTED = "replay_rejected"
    NO_PERSISTED_STATE_REJECTED = "no_persisted_state_rejected"


def evaluate_sequence_counter(
    device_identifier: str,
    received_counter: int,
    get_persisted_counter: ReplayCounterGetter,
    set_persisted_counter: ReplayCounterSetter,
) -> ReplayOutcome:
    """Decide accept/duplicate/replay for one already-MIC-verified telegram.

    Must only be called after :func:`crypto.verify_telegram_mic` has
    already returned ``True`` for this telegram -- this function trusts the
    counter it is given and performs no authentication itself.

    Rules, in order:

    - No persisted counter for this device -> ``NO_PERSISTED_STATE_REJECTED``.
      This is deliberate fail-closed behavior: first-trust/commissioning
      (how a device's initial counter gets persisted at all) is a separate,
      later decision and is explicitly NOT implemented here. There is no
      unauthenticated "trust on first use" fallback.
    - ``received_counter > persisted`` -> ``ACCEPTED``, and
      ``set_persisted_counter`` is called (advancing the persisted counter)
      BEFORE this function returns, so the advance is durable before
      acceptance is observable to any caller -- per the gate doc's
      "Persist the accepted counter durably before making the event
      observable."
    - ``received_counter == persisted`` -> ``DUPLICATE``: an authenticated
      no-op (e.g. a retransmitted channel/event copy of the same
      actuation). Not an error; the setter is NOT called and the persisted
      counter does not advance.
    - ``received_counter < persisted`` -> ``REPLAY_REJECTED``. The setter is
      NOT called; there is no auto-reset of persisted state under any of
      these four outcomes, ever -- counter wraparound, rollback, and
      recovery are explicitly out of scope for this pure phase (see
      docs/decoder-test-preparation.md, "Unresolved items", "Counter
      lifecycle").
    """
    persisted_counter = get_persisted_counter(device_identifier)

    if persisted_counter is None:
        return ReplayOutcome.NO_PERSISTED_STATE_REJECTED

    if received_counter > persisted_counter:
        set_persisted_counter(device_identifier, received_counter)
        return ReplayOutcome.ACCEPTED

    if received_counter == persisted_counter:
        return ReplayOutcome.DUPLICATE

    return ReplayOutcome.REPLAY_REJECTED
