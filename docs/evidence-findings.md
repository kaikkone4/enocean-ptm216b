# Phase 2 evidence findings (Phase 3 gate resolution)

This note records the **structural** findings from a real Phase 2 evidence
capture run against a reference device, and maps each one to the specific
blocking item it resolves in
[docs/decoder-test-preparation.md](decoder-test-preparation.md), "Unresolved
items: parser remains blocked". It contains no raw captured bytes, addresses,
counters, or signatures — only lengths, byte-order/offset facts, delta
structure, and XOR bit relations, consistent with the evidence contract in
"Exact evidence required before parser code" and the "Fixture and redaction
policy".

## Resolved items

### Home Assistant callback normalization

**Resolves:** "Home Assistant callback normalization. Confirm exactly what
`BluetoothServiceInfoBleak.manufacturer_data[0x03DA]` contains: whether the AD
Length, AD Type, and two manufacturer-ID octets have already been removed,
and in what order."

`manufacturer_data[0x03DA]` as delivered by Home Assistant is exactly **9
bytes** on the reference device: sequence counter (4 bytes, little-endian) +
switch status (1 byte) + security signature/MIC (4 bytes). Home
Assistant/bleak has already stripped the AD Length byte, AD Type byte, and
the two Manufacturer-ID octets from the value — none of that 4-byte prefix
is present. No optional data was observed on this device/firmware. This is
the exact byte layout `telegram.parse_data_telegram` now accepts, and the
exact prefix `crypto.py` reconstructs (Length `0x0C`, Type `0xFF`,
Manufacturer ID `DA 03` little-endian) to rebuild the CCM authenticated
input.

### Duplicate boundary

**Resolves:** "Duplicate boundary. Confirm which callback observations
correspond to the channel/event copies of one actuation and that all such
copies carry the same address, sequence counter, protected body, and
signature. Do not infer an action from observation count or timing."

Home Assistant delivers already-deduplicated distinct telegrams to the
registered callback: zero identical consecutive values and zero delta-0
observations were seen across the capture window. This means Home
Assistant/bleak's own advertisement deduplication has already collapsed the
repeated channel/event copies of one actuation before the callback fires, so
`replay_guard.py`'s `DUPLICATE` outcome (`received_counter == persisted`)
models a same-counter *retransmission that reaches the callback* generically
and correctly, without assuming Home Assistant will ever actually deliver
one — it is a fail-closed rule, not a rule tuned to an assumption this
evidence disproved.

### Duplicate matrix (push vs. release counter consumption)

**Resolves:** "Duplicate matrix. establish whether copies within one
physical push/release share the full authenticated telegram and sequence
counter, and whether push versus release consumes distinct counter values."

The sequence counter increments by exactly 1 per telegram, and press and
release each consume one counter value (i.e. a press+release pair spans two
counter values, not one shared value). Combined with the duplicate-boundary
finding above, this confirms there is no shared-counter push/release pairing
to reconstruct — each callback is one distinct, individually-countable
telegram.

### Button bit mapping

**Resolves:** "Button bit mapping. The manual documents Switch Status
graphically in Figure 16 and says the encoding is configurable. A reviewed,
text-level bit mapping for the actual supported profile is still required;
it must not be guessed from other PTM models."

Switch-status bit structure on the reference device matches User Manual
Figure 16: bit0 = press/release toggle, bit1 = A0, bit2 = A1, bit3 = B0,
bit4 = B1. This was confirmed via observed XOR relations between switch-status
bytes across actuations, not by reading absolute byte values:

- within-button press/release toggle: XOR `0b00001`
- A0 vs. A1: XOR `0b00110`
- A0 vs. B0: XOR `0b01010`
- A0 vs. B1: XOR `0b10010`

These four relations are exactly what `telegram.interpret_switch_status`
encodes as its accepted single-button-bit values, and what
`docs/decoder-test-preparation.md`'s own evidence-capture sensor
(`status_xor_values`) was designed to expose without ever exposing an
absolute status byte.

## Resolved live after Phase 4 commissioning (2026-08-19)

### Button bit mapping — absolute bit0 polarity: LIVE-PROVEN

The manual states bit0 = 1 means "press". Originally this capture only
proved the *relative* toggle (bit0 flips by exactly 1 between a button's
press and its release). After Phase 4 commissioning of the reference
switch, a press-and-hold test on live, MIC-verified telegrams confirmed the
event fired at push-down is `press` — the manual's polarity is correct as
implemented in `telegram.Ptm216bButtonState.is_press`. No structural or
byte-level material was recorded for this proof; it is a user-observed
event-ordering fact.

### Address representation / nonce byte order: LIVE-PROVEN

`crypto._over_air_address_bytes` implements the LE-reversal of
`identity.canonicalize_address`'s display-order output, per User Manual
section 5.1.2. In this phase it was proven only synthetically — via RFC
3610 official vectors (Oracle A) and an independent from-scratch CCM
implementation (Oracle B), see `tests/test_crypto.py` and
`tests/ccm_reference.py`. After Phase 4 commissioning, MIC verification
succeeded on live telegrams from the reference device (Verified counter
advancing, events firing), which proves the reversal direction — and,
incidentally, that the factory security key from the device label remained
valid on a Casambi-paired PTM 216B.

## Phase 5D: multi-bit switch-status patterns (manual-sourced, pending live confirmation)

As of Phase 5D, `telegram.interpret_switch_status` accepts two additional
button-bit patterns beyond the four single-bit ones already live-proven
above: `0b01010` (A0+B0) and `0b10100` (A1+B1) — the same-letter two-bit
combinations that follow directly from User Manual Figure 16's
independently-documented bit1..bit4 assignment (bit1 = A0, bit2 = A1, bit3 =
B0, bit4 = B1, each meaningful on its own), rather than a newly introduced
bit mapping. Accepting them is a relaxation of the previous "reject any
multi-bit status byte" rule to accept exactly the two same-letter
combinations that a single energy bow — a one-rocker switch's full-width
plate, or a genuinely simultaneous two-rocker press — can produce in one
telegram; every other multi-bit combination (the two diagonals A0+B1/A1+B0,
any 3-bit combo, or the 4-bit combo) remains rejected fail-closed, since no
physical rocker action produces them.

This rests on the same already-live-proven bit mapping as the four
single-button patterns (see "Button bit mapping" above), but the
combination itself is still **manual-sourced, pending live confirmation**:
no live capture of an actual A0+B0 or A1+B1 telegram — from either a
one-rocker switch or a genuinely simultaneous two-rocker press — has yet
been recorded against a reference device. Add a live-proof entry here
(mirroring the "Resolved live after Phase 4 commissioning" section above)
once one is captured.

## Still open / explicitly unsupported
- **Optional-data modes (10/11/13-byte forms).** Still unobserved on the
  reference device. `telegram.parse_data_telegram` continues to reject every
  length other than the observed 9-byte authentication-only form, per
  `ParseRejectionReason.UNSUPPORTED_LENGTH`.
- **Encrypted mode.** Still unobserved and still unsupported; no telegram
  shape in this phase decodes or exposes any encrypted-payload bytes. The
  encrypted-mode indicator-bit conflict noted in
  docs/decoder-test-preparation.md remains unresolved and out of scope.
- **`connectable` flag.** `service_info.connectable` was observed `True` via
  the reference ESP32 Bluetooth proxy even for these non-connectable
  advertisements. This is a proxy/transport-layer observation, irrelevant to
  this phase's pure decoding logic; no logic in `telegram.py`, `crypto.py`,
  or `replay_guard.py` filters or asserts on a connectable flag.
