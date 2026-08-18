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
