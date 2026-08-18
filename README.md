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

Home Assistant registers the deliberately invoked `enocean_ptm216b.start_designation_capture` action. That action is never invoked automatically; setup and passive observations never start capture. Each invocation starts a fresh **30-second** window.

At expiry the integration selects a runtime-only designation only when **exactly one** candidate was observed at least **three** times. Three is a conservative noise floor: it rejects one-off and duplicate ambient noise while using only a passive advertisement count. It does not inspect payload content or timing, group packets into presses, or infer button events. Zero candidates, one or two observations, or any second candidate always produce `no_selection`; candidates are then cleared.

The **Designation capture** diagnostic sensor exposes only:

- state: `active` or `inert`
- `observation_count`: aggregate valid-address observations in the current/latest window
- `designation_outcome`: `selected` or `no_selection`

Its exact privacy and radio limits are:

- candidate records are ephemeral and contain only a full local HMAC identifier plus an aggregate count
- the integration-local 32-byte HMAC secret is retained in Home Assistant's private integration Store and loaded into runtime memory; it is never placed in config-entry data, entity state, diagnostics, logs, Git, or UI
- raw BLE addresses, raw payloads, and full pseudonymous identifiers are never persisted or exposed in entity state/attributes, logs, diagnostics, service data, config-entry data, or UI
- expiry clears all candidates; cancellation, restart, and unload clear candidates, designation, aggregate capture count, and outcome
- the separate `DesignatedSessionCounter` remains unwired and emits nothing
- Bluetooth remains passive and non-connectable only: no active scanning, connection, pairing, bonding, GATT access, packet decoding, authentication, or replay protection
- no button actions, events, triggers, or press/session inference are emitted

### Safe user-visible test

1. Install this PR build, restart Home Assistant, and add **EnOcean PTM 216B BLE** once.
2. Open **Developer Tools → Actions** and select `enocean_ptm216b.start_designation_capture`. Leave action data empty and run it deliberately once.
3. Open the integration's **Designation capture** diagnostic sensor. It must change from `inert` to `active`; `observation_count` starts at `0`.
4. For the isolated happy-path test, keep other EnOcean advertisers quiet and cause the one intended test device to advertise at least three times during the 30-second window. The integration only counts passive advertisements; it does not interpret these as presses.
5. After 30 seconds, verify state `inert` and outcome `selected`. If no device, fewer than three observations, or more than one candidate was observed, verify the fail-closed outcome `no_selection` instead.
6. Verify no address, payload, key/secret, or identifier is shown anywhere. Do not enter or upload commissioning material.

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
