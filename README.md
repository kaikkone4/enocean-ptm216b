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

## Designated test-session counter (Phase 1.5)

The repository contains a pure, in-memory `DesignatedSessionCounter` and a manually-started 30-second designation-capture foundation. During an explicitly active capture only, the existing passive Bluetooth callback transiently converts each callback address into a full SHA-256 HMAC identifier using the integration-local private secret. Ephemeral candidate records retain only observation count plus first/last monotonic times, keyed by that full digest.

This slice deliberately implements **no candidate selection**. At expiry it fails closed: every candidate is discarded and the designated identifier remains unset. There is still no UI or service that starts capture and no physical-press test is enabled yet.

Its exact limits are:

- outside an explicitly active capture, callback addresses do not create candidates
- capture lasts exactly **30 seconds** as enforced by the existing injected scheduler
- candidate state contains only the full HMAC digest, aggregate count, and first/last monotonic times, and remains in per-entry ephemeral memory
- raw BLE addresses and manufacturer payloads are not retained, logged, decoded, or copied into candidate state
- the HMAC secret and pseudonymous identifiers are not placed in config-entry data, Home Assistant entity state, diagnostics, logs, Git, or UI; the secret remains in Home Assistant's private integration storage and runtime memory
- expiry, cancellation, restart, and unload discard all candidate aggregates; expiry never auto-selects a designation
- the separate `DesignatedSessionCounter` remains unwired; with no designated identifier, it counts nothing
- it performs no active scanning, BLE connection, pairing, bonding, GATT access, packet decoding, authentication, or replay protection
- it emits no Home Assistant actions, events, triggers, or designation-related entities/state updates; the existing aggregate advertisement diagnostic counter is unchanged

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
