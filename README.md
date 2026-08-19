# EnOcean PTM 216B BLE for Home Assistant

A **passive-only** Home Assistant custom integration for observing EnOcean PTM 216B battery-free BLE pushbutton advertisements through Home Assistant's existing Bluetooth adapters and ESPHome Bluetooth proxies.

## Status: observation MVP

The first release is deliberately limited to verifying that PTM 216B advertisements reach Home Assistant:

- passive Bluetooth advertisement observation only
- no BLE connection, pairing, bonding, GATT access, reset, commissioning, or active scanning
- no button events, device triggers, or light/Casambi control yet
- no raw advertisement payloads or keys stored in states, logs, diagnostics, or Git

The integration exposes one diagnostic counter: **Observed advertisements**. It increments when Home Assistant receives a non-connectable EnOcean manufacturer advertisement. It does not prove that a packet is a PTM 216B telegram or authenticate its content.

## Safety and security boundary

A future decoder must validate the PTM's authenticated/encrypted telegram data before it emits an action:

- fail closed on missing, invalid, or unverifiable MIC/authentication data
- persistent replay protection before any trusted event
- device-specific commissioning/security keys only through a local trusted provisioning flow
- never paste keys, QR payloads, or NFC commissioning data into chat, issues, logs, or Git

Until that decoder exists, this integration is observation-only. It does not affect any existing Casambi pairing or lighting control.

## Manual designation capture (Phase 1.5)

Manual designation is available only from the integration's **Reconfigure** flow. Setup and passive observations never start it, and the integration registers no action/service for capture or device control.

One deliberate request starts three bounded phases; a single 30-second sample can never select a device:

1. **`baseline` (10 seconds):** keep the switch quiet. Any matching candidate fails the attempt closed.
2. **`press` (30 seconds):** make three short normal presses on one specified button, about two seconds apart. Exactly one address-derived HMAC candidate must produce at least three passive observations.
3. **`confirmation` (a separate 30 seconds):** repeat the same instruction. Exactly one candidate must again produce at least three observations, and its full in-memory HMAC digest must match the first window.

Selection occurs only after both independent press windows uniquely match. No candidate, too few observations, any ambiguity, confirmation mismatch, or a rotating address produces `no_selection`. Every phase transition clears its candidate map; terminal timeout, restart, cancellation, and unload clear all maps, the first-window identifier, scheduler, and timer. The selected digest is runtime-only and is also cleared on restart, cancellation, or unload.

The **Designation capture** diagnostic sensor exposes only:

- state: `inert`, `baseline`, `press`, or `confirmation`
- `observation_count`: aggregate valid-address observations in the current/latest phase
- `designation_outcome`: `selected` or `no_selection`

Its exact privacy and radio limits are:

- candidate records are ephemeral and contain only a full local HMAC identifier plus an aggregate count; no address, payload, or timing is retained
- the integration-local 32-byte HMAC secret is retained only in Home Assistant's private integration Store and loaded into runtime memory; it is never placed in config-entry data, entity state, diagnostics, logs, Git, or UI
- raw BLE addresses, raw payloads, full pseudonymous identifiers, candidate maps, and timing data are never persisted or exposed in entity state/attributes, logs, diagnostics, config-entry data, or UI
- the separate `DesignatedSessionCounter` remains unwired and emits nothing
- Bluetooth remains passive and non-connectable only: no active scanning, connection, pairing, bonding, GATT access, packet decoding, authentication, or replay protection
- no button actions, services, events, triggers, automations, or press/session inference are emitted

### Safe user-visible test

1. Install this PR build, restart Home Assistant, and add **EnOcean PTM 216B BLE** once.
2. Open the integration, choose **Reconfigure**, and deliberately confirm once. Do not use Developer Tools → Actions; no capture action is registered.
3. Watch **Designation capture**. During `baseline` keep all EnOcean test switches quiet.
4. When state becomes `press`, make three short normal presses on one specified button, two seconds apart, then stop.
5. Wait for state `confirmation`, then repeat the same three-press sequence on the same button.
6. After the confirmation window, verify `inert` and `selected`. Any baseline traffic, ambiguity, too few observations, or changed/rotating address must instead end as `no_selection`.
7. Verify no address, payload, secret, full identifier, candidate map, or timing appears anywhere. Do not enter or upload commissioning material.

## Telegram structure evidence capture (Phase 2)

Structure evidence capture is available only from the integration's **Reconfigure**
flow, and only after a device has already been designated (Phase 1.5). The
Reconfigure flow now shows a menu with two deliberate choices: **Designation
capture** (unchanged) and **Evidence capture**. Choosing evidence capture before
any device is designated in this runtime session aborts with `no_designated_device`
and starts nothing. Starting either flow cancels the other if it is currently
running.

Evidence capture inspects, in memory only, what Home Assistant's Bluetooth callback
actually delivers for the already-designated switch during a single bounded
90-second window. It never decodes a telegram, authenticates anything, verifies a
signature, or emits a button action — see
[docs/decoder-test-preparation.md](docs/decoder-test-preparation.md) for the exact
evidence contract and abort rules this feature implements.

The **Evidence capture** diagnostic sensor exposes only:

- state: `inert`, `collecting`, `complete`, `no_data`, or `aborted`
- while `collecting`: `callbacks_accepted` only
- while `complete`: `callbacks_accepted`, `manufacturer_data_keys` (sorted list of
  manufacturer-data map keys seen), `value_lengths` (sorted unique byte lengths),
  `prefix_detected_consistent` (`true`/`false`/`"mixed"`), `le_deltas` and
  `be_deltas` (sequence-counter deltas between consecutive callbacks — **never
  absolute counter values**), `counter_monotonic_le` / `counter_monotonic_be`,
  `status_xor_values` (each callback's switch-status byte XORed with the first
  callback's — **never an absolute status byte**), `duplicate_identical_count`,
  and `any_connectable_seen`
- `inert`, `no_data`, and `aborted` expose no attributes beyond the state itself

It never exposes a BLE address, a raw payload byte, an absolute sequence counter,
an absolute switch-status value, a signature, a secret, or a full 64-character
identifier — in entity state/attributes, logs, diagnostics, or config-entry data.

### Capture abort rules

The window fails closed and discards everything the moment any accepted
manufacturer-data value under key `0x03DA` is 24 bytes or longer (a possible
commissioning telegram carrying the device's security secret) or shorter than 9
bytes (too short to contain a counter and switch-status byte). On abort the state
becomes `aborted`, every raw byte and structural record collected so far is
discarded immediately, and the sensor exposes nothing but that state. Reaching 64
structural records ends the window early as `complete` instead of waiting the full
90 seconds. Restart, unload, and cancelling either capture flow clear all evidence
state.

### Safe user-visible test

1. Install this PR build, restart Home Assistant, and complete Phase 1.5
   designation for your test switch first (see above). Evidence capture is
   unavailable until a device is designated.
2. Open the integration, choose **Reconfigure**, and pick **Evidence capture**
   from the menu. Do not use Developer Tools → Actions; no capture action is
   registered.
3. During the 90-second `collecting` window, press each rocker button — A0, A1,
   B0, and B1 — three times, press-and-release, with about two seconds between
   presses.
4. Wait for the window to end (`complete` or `no_data`) and inspect the **Evidence
   capture** sensor's attributes.
5. Verify no address, raw payload byte or hex, absolute counter, absolute switch
   status, signature, secret, or full identifier appears anywhere. Do not enter or
   upload commissioning material.

The integration still emits no button events, services, or actions, and Bluetooth
remains passive and non-connectable only throughout this capture.

## Decoder foundation (Phase 3)

Pure, unwired decoding primitives now exist alongside the passive observer:

- `telegram.py` — a strict, fail-closed parser for the one telegram shape
  Phase 2 evidence capture actually observed live (9-byte
  authentication-only `manufacturer_data[0x03DA]`), plus a switch-status
  interpreter for the A0/A1/B0/B1 button mapping. Every other length,
  including the documented-but-unobserved optional-data forms, is rejected
  with a typed reason rather than guessed at.
- `crypto.py` — CCM (RFC 3610) MIC verification per User Manual section
  5.1, built on the `cryptography` library's `AESCCM` primitive.
- `replay_guard.py` — pure accept/duplicate/replay decision logic for
  sequence counters, with an injectable persistence interface for a later
  Home Assistant `Store`-backed implementation.

None of this is wired into the running integration. The callback registered
in `__init__.py` is unchanged: it still only counts advertisements and feeds
designation/evidence capture. The integration emits no button events, stores
no keys, and remains a passive observer exactly as before. Encrypted-mode
telegrams and optional-data forms are rejected pending future evidence; see
[docs/evidence-findings.md](docs/evidence-findings.md) and
[docs/decoder-test-preparation.md](docs/decoder-test-preparation.md) for the
full evidence trail and what remains open before any of this can be wired up.

## Test installation with HACS

1. In Home Assistant, open **HACS → Integrations → ⋮ → Custom repositories**.
2. Add `https://github.com/kaikkone4/enocean-ptm216b` with category **Integration**.
3. Install **EnOcean PTM 216B BLE** and restart Home Assistant.
4. Go to **Settings → Devices & services → Add integration**.
5. Add **EnOcean PTM 216B BLE**.
6. Open the created device/entity and watch **Observed advertisements** while pressing the designated PTM 216B test switch.

Do not enter or upload any commissioning material for the observation MVP.

## Development

```bash
uv venv --python 3.13 .venv-test
uv pip install --python .venv-test/bin/python -r requirements-test.txt
.venv-test/bin/pytest -q tests -o asyncio_mode=auto
.venv-test/bin/python -m ruff check .
.venv-test/bin/python -m ruff format . --check
```

## License

MIT.
