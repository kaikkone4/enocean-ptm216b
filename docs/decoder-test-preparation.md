# PTM 216B decoder-test preparation (research only)

This note defines the evidence and test gates for a later decoder phase. It does
**not** authorize packet decoding, provisioning, radio work, Home Assistant
changes, Casambi changes, or event emission.

## Scope and non-invasive boundary

The current integration is a passive observer, not a PTM 216B decoder:

- `__init__.py` registers one Home Assistant Bluetooth callback with
  `manufacturer_id == 0x03DA`, `connectable == false`, and passive scanning.
- The callback increments an aggregate counter and, only during a manually
  started bounded designation flow, passes the callback's address transiently
  into a local HMAC identifier. It does not read `manufacturer_data`.
- Candidate state retains only an HMAC identifier and aggregate count; it does
  not retain an address, payload, timestamp, or key.
- No packet is authenticated, decrypted, replay-checked, or converted into a
  button event. Manufacturer ID matching alone does not establish that an
  advertisement is a PTM 216B data telegram.

Those boundaries must remain unchanged during this preparation phase. In
particular: no BLE connection, active scan, pairing, GATT, commissioning mode,
NFC, QR scanning, key handling, device reconfiguration, Home Assistant access,
or Casambi access.

## Documented facts

The detailed EnOcean PTM 216B User Manual v1.6 (November 2024) is the primary
telegram reference for this plan:

1. A normal actuation transmits multiple copies of the same telegram. In the
   default setup, the telegram is sent on advertising channels 37, 38, and 39
   in each advertising event, and two or three events are sent with a default
   20 ms interval (10 ms is configurable). [User Manual, sections 3.1-3.3,
   pp. 10-13][3]
2. A data-telegram advertising-data payload is 13-17 bytes, with fields
   `Length (1) | Type (1) | Manufacturer ID (2) | Sequence Counter (4) |
   Switch Status (1) | Optional Data (0/1/2/4) | Security Signature (4)`.
   Type is `0xFF`; the EnOcean manufacturer ID is `0x03DA`; the sequence
   counter increments for every data or commissioning telegram. [User Manual,
   section 4.6.1, pp. 18-19][3]
3. All data telegrams carry a 32-bit authentication signature. The manual
   specifies CCM (RFC 3610) with a 128-bit device-specific secret. The 13-byte
   nonce is the transmitted six-byte source address, four-byte sequence
   counter, and three zero bytes; source address and counter use little-endian
   representation in the nonce. Authenticated input covers Length, AD Type,
   Manufacturer ID, Sequence Counter, Switch Status, and Optional Data.
   [User Manual, sections 5.1-5.1.2, pp. 21-22][3]
4. When payload encryption is enabled, Switch Status and Optional Data are the
   authenticated-and-encrypted CCM input; the preceding fields remain
   authenticated data. [User Manual, section 5.2, p. 23][3]
5. The receiver verifies the signature, then accepts a telegram as original
   only if its sequence counter is higher than the most recently accepted
   counter, and then updates the retained counter. [User Manual, section
   5.1.3, p. 23][3]
6. Static source addresses are the default, but resolvable private addresses
   can be configured and rotate for each data telegram. Repeated advertising
   events for the same telegram retain the same private address. [User Manual,
   sections 4.4-4.4.2, pp. 15-17][3]
7. Commissioning telegrams are categorically unsafe as fixtures: their
   30-byte payload includes the device's 16-byte security secret and static
   address. [User Manual, section 4.6.3, p. 20][3]

The product page links the current official datasheet and user manual.[1] The
short datasheet confirms BLE advertising mode, an individual 48-bit device
identity, sequence-counter authentication, and an AES-128 security claim, but
it is not sufficiently detailed to define decoder behavior.[2]

## Unresolved items: parser remains blocked

The following are not assumptions. Each must be resolved by authoritative
clarification or non-sensitive evidence before implementation:

- **Home Assistant callback normalization.** Confirm exactly what
  `BluetoothServiceInfoBleak.manufacturer_data[0x03DA]` contains: whether the
  AD Length, AD Type, and two manufacturer-ID octets have already been removed,
  and in what order. The repository currently neither reads nor tests this
  value.
- **Address representation.** Confirm how the callback's printable address maps
  to the six over-air octets used in the CCM nonce. Never infer nonce byte order
  from colon-formatted display text.
- **Frame classification.** Confirm the callback-visible distinction between a
  data telegram and any other EnOcean manufacturer advertisement. The existing
  manufacturer-only matcher is intentionally broader than PTM 216B.
- **Duplicate boundary.** Confirm which callback observations correspond to the
  channel/event copies of one actuation and that all such copies carry the same
  address, sequence counter, protected body, and signature. Do not infer an
  action from observation count or timing.
- **Button bit mapping.** The manual documents Switch Status graphically in
  Figure 16 and says the encoding is configurable. A reviewed, text-level bit
  mapping for the actual supported profile is still required; it must not be
  guessed from other PTM models.
- **Optional-data modes.** The actual callback shape for optional-data lengths
  0, 1, 2, and 4 is unobserved. Absence of a captured mode is not proof that it
  is unsupported.
- **Encrypted-mode indicator conflict.** Section 4.4.1 says static-address bit
  31 toggles when encryption is used, while section 5.2.1 says bit 8. Obtain
  vendor clarification or a corrected manual before selecting this bit.
- **Length conflict.** Section 4.6.1 gives `0x0C/0x0D/0x0E/0x10`, while Figure
  20's extracted text appears to give `0x0C/0x0D/0x0F/0x10`. Do not accept the
  two-byte optional-data form until this is resolved from the original figure
  or by EnOcean.
- **Algorithm wording conflict.** The detailed manual specifies CCM for the
  32-bit signature, while the June 2025 datasheet calls security “AES128 (CBC)”.
  Do not silently reinterpret CBC as CCM. Require EnOcean clarification and
  synthetic known-answer agreement before supporting encrypted payloads.
- **Counter lifecycle.** Wraparound, factory-reset rollback, receiver state
  loss, and intentional recommissioning behavior are not defined sufficiently
  for automatic recovery. All must fail closed pending a separate trusted local
  recovery design.
- **Resolvable-private-address support.** Identity resolution requires trusted
  provisioning material. It is out of scope; a rotating address must not be
  treated as a newly trusted device or bypass replay state.

## Exact evidence required before parser code

No live capture is performed by this phase. A separately approved future
observation must use ordinary short presses only, remain passive, and retain
real bytes only ephemerally. It must produce a local review worksheet—not a Git
fixture—with the following facts for every observation:

1. **Callback envelope:** source field exactly as delivered to the callback;
   connectable flag; all manufacturer-data map keys; byte length of the value
   under `0x03DA`; and whether Length/Type/Manufacturer ID are present in that
   value. RSSI, host names, adapter IDs, and location are unnecessary and must
   not be collected.
2. **Canonical byte boundaries:** byte offsets (not real byte values in Git) for
   sequence counter, protected body, optional-data span, and 4-byte signature;
   observed total length; and the resulting candidate optional-data length.
3. **Over-air/API mapping:** for one synthetic or otherwise non-sensitive
   reference frame, a reviewed mapping from BLE source-address octets and AD
   structure to callback address and `manufacturer_data` bytes. If the platform
   does not expose enough information, stop; do not guess.
4. **Actuation matrix:** ordinary A0, A1, B0, and B1 press and release, at least
   three isolated cycles each. Record only structural outcomes: callback count,
   equality/difference relationships between copies, sequence-counter ordering,
   total lengths, and proposed switch-status bit relationships. Do not commit
   real addresses, counters, protected bytes, signatures, payload hashes, or
   timestamps.
5. **Duplicate matrix:** establish whether copies within one physical
   push/release share the full authenticated telegram and sequence counter, and
   whether push versus release consumes distinct counter values. This evidence
   is for de-duplication tests, not timing-based press inference.
6. **Mode coverage:** mark static/RPA, authentication-only/encrypted, and each
   optional-data length as `observed`, `synthetically covered`, or `unsupported
   pending evidence`. Do not reconfigure a device to fill gaps.

### Capture abort rules

A future observation tool must default to no persistence and bounded in-memory
inspection. It must immediately discard the entire observation and retain no
bytes if any frame could be a commissioning telegram, if frame classification
is ambiguous, or if unexpected lengths/fields appear. In particular, any
30-byte full advertising payload or commissioning Length value (`0x1D`, and
`0x1E` on pre-DC-06 products) is an abort condition. No commissioning entry
sequence may be performed. No QR, NFC, label, PIN, address/key pair, or real
security secret may be read, copied, logged, uploaded, or committed.

## Fixture and redaction policy

Repository fixtures must be **synthetic and cryptographically non-sensitive**:

- Never commit a real address, sequence counter, telegram, signature/MIC,
  ciphertext, payload hash, commissioning telegram, QR/NFC content, or device
  secret—even if truncated, masked, encrypted, or believed inactive.
- Do not “redact” a real authenticated telegram byte-by-byte. Altering it makes
  it cryptographically invalid, while preserving parts can still fingerprint a
  device. Delete it and generate a synthetic vector instead.
- A future fixture generator must use plainly labelled test-only identities and
  test-only cryptographic material generated independently of every physical
  device. The generator input and derivation must make that provenance
  reviewable. No production provisioning path may load fixture material.
- Keep two independent synthetic oracles: (a) generic RFC 3610 CCM
  known-answer vectors and (b) PTM-layout vectors produced by a reviewed
  independent serializer/crypto implementation. The decoder must not generate
  its own expected answers.
- Commit only the minimal canonical fields needed by unit tests, plus provenance
  (`synthetic: true`, generator/version, documented mode, expected accept/reject
  result). Do not include capture metadata.

Required synthetic cases include every documented optional-data length,
authentication-only and encrypted layouts, press/release bit candidates once
resolved, duplicate retransmission, increasing counters, and malformed/unknown
layouts. Negative vectors must independently mutate address, counter, payload,
signature, length/type/manufacturer ID, encryption indicator, and truncation.

## Fail-closed decoder contract

A later decoder is not allowed to emit a trusted event unless all gates pass in
this order:

1. Exact supported frame shape, AD type, manufacturer ID, field lengths, source
   mode, and encryption mode are unambiguous. Reject unknown/reserved values,
   trailing bytes, truncation, and conflicting length declarations.
2. A trusted local device context exists. Missing or ambiguous identity/security
   context is a hard rejection; there is no unauthenticated fallback.
3. Construct the nonce and CCM inputs only from the verified canonical byte
   representation. Authenticate the received 32-bit signature using a
   constant-time library primitive. Never parse or expose switch semantics from
   unauthenticated plaintext, and never expose decrypted bytes before MIC
   verification succeeds.
4. A missing, malformed, invalid, or unverifiable MIC/signature is a hard
   rejection with no event, state update, counter advance, or diagnostic byte
   leakage. AES-CCM errors must be indistinguishable in user-visible output.
5. After successful authentication, perform an atomic persistent replay check
   keyed to the trusted device identity. Emit at most once only when
   `received_counter > persisted_counter`; equal counters are duplicate copies
   and become authenticated no-ops; lower counters are replay/rollback rejects.
6. Persist the accepted counter durably **before** making the event observable.
   If compare-and-store, persistence, synchronization, or crash recovery cannot
   be guaranteed, emit nothing. Concurrent callbacks must not both accept the
   same counter.
7. Counter wrap, reset/rollback, missing replay state after restart, RPA
   resolution failure, or a changed security mode requires an explicit trusted
   local recovery/recommissioning design. Never auto-reset replay state.

Logs and diagnostics may expose only coarse reason categories and aggregate
counts. They must never include addresses, counters, raw/canonical bytes,
signatures, nonces, plaintext, ciphertext, secrets, or full pseudonymous IDs.

## Parser implementation entry criteria

Parser work may begin only after:

- the callback normalization and nonce-address mapping are proven;
- the manual conflicts above are resolved or the conflicting modes are
  explicitly unsupported and rejected;
- synthetic independent known-answer fixtures exist for the intended scope;
- negative MIC/AES-CCM and replay tests are written first;
- an atomic durable replay-state design has passed restart/concurrency review;
- commissioning and trusted local provisioning are designed separately, with
  no keys in config entries, logs, diagnostics, UI, issues, chat, or Git.

Until every applicable item is complete, the integration remains an aggregate
passive observer and must emit no button action.

## Sources

[1] https://www.enocean.com/en/product/ptm-216b — EnOcean PTM 216B product page
[2] https://www.enocean.com/wp-content/uploads/downloads-produkte/en/products/enocean_modules_24ghz_ble/ptm-216b/PTM-216B-Datasheet.pdf — EnOcean PTM 216B Datasheet
[3] https://www.enocean.com/wp-content/uploads/downloads-produkte/en/products/enocean_modules_24ghz_ble/ptm-216b/PTM-216B-User-Manual.pdf — EnOcean PTM 216B User Manual
