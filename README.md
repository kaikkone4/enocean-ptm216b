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

As of Phase 4 (see below), the integration decodes and authenticates telegrams from switches you explicitly commission, and emits a trusted button event only after every one of the following passes, in order:

- the advertisement matches the exact supported PTM 216B telegram shape
- its 32-bit signature (MIC) verifies against that switch's commissioned key
- its sequence counter is strictly greater than the durably persisted counter for that switch (replay/duplicate protection)
- its switch-status byte decodes to one of the six accepted button patterns (see
  "Six button states and 1-rocker aliasing" below)

Any other advertisement -- from an uncommissioned switch, or one that fails any gate above -- never produces an event, state change, counter advance, or diagnostic byte leakage. There is no unauthenticated fallback at any gate.

This remains true regardless of commissioning:

- no BLE connection, pairing, bonding, GATT access, reset, or active scanning -- reception stays passive and non-connectable only
- device-specific commissioning/security keys enter the integration only through the local trusted commissioning flow described below, and live only in a private, local-only Home Assistant storage file -- never in config-entry data, entity state/attributes, diagnostics, logs, or UI
- never paste keys, QR payloads, or NFC commissioning data into chat, issues, logs, or Git -- only into the commissioning form itself

This does not affect any existing Casambi pairing or lighting control.

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

At the time these primitives were built, none of them were wired into the
running integration; Phase 4 (below) is that wiring, for commissioned
switches only. The passive callback registered in `__init__.py` still always
counts advertisements and feeds designation/evidence capture, for every
matching advertisement, exactly as before. Encrypted-mode telegrams and
optional-data forms are still rejected pending future evidence; see
[docs/evidence-findings.md](docs/evidence-findings.md) and
[docs/decoder-test-preparation.md](docs/decoder-test-preparation.md) for the
full evidence trail this decoder is built on.

## Commissioning and button events (Phase 4)

This is the first phase where the integration emits trusted button events —
but only for switches you explicitly commission through a local, deliberate
flow. Every other advertisement is still just counted, exactly as in the
observation MVP.

### Fail-closed pipeline

For a commissioned switch's advertisement, every gate below must pass, in
this exact order, before anything is emitted or any durable state changes:

1. **Shape** — the advertisement's `manufacturer_data[0x03DA]` value must be
   the one supported 9-byte telegram shape. Anything else is rejected.
2. **MIC** — the telegram's 32-bit signature must cryptographically verify
   against that switch's commissioned key. A missing, malformed, or invalid
   MIC is rejected, indistinguishably from any other MIC failure, with no
   state change of any kind.
3. **Counter / replay** — the telegram's sequence counter must be strictly
   greater than the durably persisted counter for that switch. An equal
   counter is a duplicate (authenticated no-op); a lower counter is a replay
   reject. Both are rejected with no counter advance.
4. **Status** — only once shape, MIC, and counter all pass does the
   switch-status byte get decoded. It must resolve to one of the six
   accepted button patterns (A0, A1, B0, B1, A0+B0, or A1+B1 — see "Six
   button states and 1-rocker aliasing" below); reserved bits, zero button
   bits, or any other button-bit combination (the diagonals A0+B1/A1+B0, any
   3-bit combo, or the 4-bit combo) are rejected — even though the counter
   has, by this point, already durably advanced (that is the correct,
   deliberate order: counter/replay protection does not depend on whether
   the status byte turns out to be decodable).

Only a telegram that passes all four gates fires an event, on the one event
entity matching its decoded (and, for a one-rocker switch, aliased) button
pattern.

### First-trust policy: no unauthenticated fallback

A newly commissioned switch has no prior counter to compare against. Its
first cryptographically verified telegram (it still must pass the shape and
MIC gates — there is no unauthenticated fallback at any point) silently
initializes the persisted counter and produces **no event**. Every
subsequent verified telegram is then judged normally against that counter.
This is a one-time, per-switch, silent step — expect the very first press
after commissioning to produce no event at all.

### Where the address and key live

Commissioning a switch is a deliberate, documented exception to this
integration's normal "no addresses persisted" rule: a commissioned switch's
BLE address and its 16-byte device-specific security key are retained, but
**only** in a private, local-only Home Assistant storage file, never
anywhere else — not in config-entry data, entity state or attributes,
diagnostics, logs, or the UI. Removing a switch deletes both permanently.

**Never paste QR/label text, a photo of the label, an address, or a
security key into chat, a GitHub issue, or Git** — only into the Add-device
wizard itself, inside your own Home Assistant instance.

## Per-switch Add-device wizard (Phase 5A)

As of Phase 5A, each commissioned switch is its own **config subentry**
under this integration's device page — the same "sub-devices" mechanism
Home Assistant core integrations like MQTT and the various AI conversation
agents use. This replaced the old single **Reconfigure → Commission
switch**/**Decommission switch** menu entries from Phase 4; those two steps
no longer exist. **Reconfigure** now only ever offers the two diagnostic
tools, **Designation capture** and **Evidence capture**, unchanged.

### Adding a switch

From **Settings → Devices & services → EnOcean PTM 216B BLE**, use **Add
switch**. The wizard has two parts:

1. **Detection (optional, recommended).** Choose "Detect by pressing" and
   follow the on-screen phases — a quiet 10-second baseline, then two
   independent 30-second three-press windows on the button you want to
   detect — exactly the same bounded, auto-advancing capture the old
   Designation capture diagnostic used, just driven live inside the wizard
   instead of a separate sensor. It auto-advances between phases; there is
   nothing to click mid-detection. If it does not uniquely select a switch,
   you can retry or continue without detection. Detecting first means the
   next step's entered address is cross-checked against the switch you
   actually pressed, so a typo cannot silently commission the wrong device.
   Choosing "Skip detection" goes straight to the next step, with no such
   cross-check — the form says so.
2. **Key entry.** Provide the address and 16-byte security key one of three
   ways: upload a photo of the module's QR code/label (self-installs its
   decoder the first time this step opens — see below; if that install
   fails, the photo field is hidden), paste the QR/label text, or type the
   address and key manually. Give the switch a name and choose whether it
   has one or two rocker button pairs. Submit.

On success, the switch's device and event/diagnostic entities appear (or
reappear) automatically — no separate reload step.

### Rocker count

Most PTM 216B modules have two rocker button pairs (A0/A1 and B0/B1); some
have only one full-width rocker plate (A0/A1 only). Choosing "1" in the
wizard creates only the A0/A1 event entities; choosing "2" (the default)
creates all six — see "Six button states and 1-rocker aliasing" below for
what those six are and exactly how a one-rocker switch's B0/B1/combo
telegrams are silently aliased to A0/A1 rather than simply firing nothing.

### Removing a switch

Delete its subentry from the device page, the same way you would remove any
other Home Assistant sub-device. This removes its device and entities
immediately; its private store record (address, key, counter) is purged the
next time this integration reloads, which happens automatically right after
a removal.

### Migrating from v0.4.0

If you commissioned switches before Phase 5A, they are migrated
automatically, once, the first time you update: each existing switch gets
its own "switch" subentry (defaulting to two rockers, since the
rocker-count concept did not exist yet), with no address, key, or other
sensitive data in that subentry — only its name and non-reversible device
handle. Your switches, their names, keys, and sequence counters are
unaffected; nothing needs to be re-commissioned.

### QR-photo upload availability

Photo-QR decoding self-installs the first time the Add-device wizard's
key-entry step opens — including on Home Assistant OS. It uses
[`pyrxing`](https://github.com/tanagumo/pyrxing), a dependency-free
zxing-cpp-based reader that (unlike `zxing-cpp` itself) publishes wheels for
musllinux (Home Assistant OS's platform) as well as manylinux, macOS, and
Windows. Neither `pyrxing` nor `zxing-cpp` is listed as a hard requirement
of this integration — a platform this integration hasn't been checked
against, or an install with no outbound network access, must never fail
integration setup over an optional convenience feature.

The install happens lazily, at most once per Home Assistant start, right
before the key-entry form renders — never at startup, so it can never delay
or block anything else. If it succeeds, the photo field appears and stays
available for the rest of that run. If it fails (no matching wheel for your
platform/Python, or no network access), the photo field is simply hidden
with an explanatory note — QR/label text and manual entry keep working
exactly the same, and nothing is retried until Home Assistant restarts.

Manually installing `zxing-cpp` yourself remains a supported alternative
backend, e.g. on a glibc-based install (Home Assistant Container or
Supervised on Debian, or a dev environment):

```bash
pip install zxing-cpp
```

into the same Python environment Home Assistant runs in, then restart Home
Assistant. If `zxing-cpp` is present it is used instead of the
auto-installed `pyrxing`.

### What each commissioned switch exposes

- One **event** entity per button pattern — A0, A1 always, plus, for
  two-rocker switches, B0, B1, and the two combo patterns A0+B0, A1+B1 (six
  total; see "Six button states and 1-rocker aliasing" below) — each firing
  `press`, `release`, and, as of Phase 5B, the derived
  `short_press`/`long_press` -- see "Short/long press and device triggers"
  below. Only the one entity matching the decoded (and, for a one-rocker
  switch, aliased) pattern fires per accepted telegram.
- **Verified telegrams** (diagnostic sensor): counts only telegrams that
  passed every gate above (shape, MIC, counter, and, for a button this
  switch has an event entity for, status). Does not include first-trust
  initialization.
- **Rejected telegrams** (diagnostic sensor): counts everything else that
  reached a decision — shape rejects, MIC failures, duplicates, replay
  rejects, and status-decode rejects. Never exposes a reason, byte, address,
  or counter — only this aggregate count.

Both diagnostic sensors reset to zero on a Home Assistant restart; the
durable, restart-surviving state is the sequence counter itself, in the
private commissioning store.

### Safe user-visible test

1. From **Settings → Devices & services → EnOcean PTM 216B BLE**, choose
   **Add switch**.
2. Pick **Detect by pressing**. During the baseline phase keep all EnOcean
   test switches quiet; during each press phase, make three short presses
   on the one button you want to detect, about two seconds apart.
3. Once detection succeeds, provide the address and key (QR photo, pasted
   QR/label text, or manual entry), a name, and the correct rocker count,
   then submit.
4. Press each button the switch has — A0, A1, and (for a two-rocker switch)
   B0, B1 — a few times each.
5. The **first** verified press after commissioning produces **no** event —
   that is the first-trust policy, not a bug. Every press/release after that
   should appear as an event on the corresponding event entity.
6. Watch the **Verified telegrams** and **Rejected telegrams** sensors move
   as expected: verified increments once per real press/release that
   produces an event (on a one-rocker switch this includes every B0/B1/combo
   telegram too, since those are now aliased to the A0/A1 entity rather than
   firing nothing — see "Six button states and 1-rocker aliasing" below);
   rejected increments for anything discarded.
7. **Polarity check**: press and *hold* one button. The event fired at the
   moment you push down should be `press`; the one fired when you release
   should be `release`. If it is the other way around, please report it —
   the absolute press/release polarity is sourced from the manual, not yet
   proven against a live device (see
   [docs/evidence-findings.md](docs/evidence-findings.md)), and a one-line
   mapping flip would be needed to fix it.
8. Verify no address, key, raw payload byte, absolute counter, uploaded
   photo, or full identifier appears anywhere in entity state, attributes,
   logs, or diagnostics. Only the switch's chosen name and its
   non-reversible device handle are visible.
9. Optionally, delete the switch's subentry from the device page and
   confirm its device and entities disappear.

## Short/long press and device triggers (Phase 5B)

Every commissioned button now exposes four events instead of two: the
existing raw **`press`** (fires the instant a verified press is decoded)
and **`release`** (fires the instant a verified release is decoded),
plus two new derived ones:

- **`short_press`**: the button was released before the long-press
  threshold elapsed. Fires just before `release`, on the same release
  telegram.
- **`long_press`**: the button is *still held down* when the threshold
  elapses -- this fires immediately at that moment, **not** when the
  button is eventually released. This is a deliberate hold-time design:
  it lets a "dim while held" or "stop at target brightness" automation
  react the instant a long press is recognized, rather than only after
  the finger lifts.

Releasing a button that already fired `long_press` produces only
`release` -- never a `short_press` on top of it.

### Configuring the threshold

Each switch has its own **long-press threshold**, in milliseconds
(default 500, adjustable between 200 and 5000). Set it in the Add-device
wizard's key-entry step, or change it later -- without recommissioning,
address, or key re-entry -- from the switch's subentry **Reconfigure**
option on the device page, where its name and rocker count are also
editable.

### Radio-loss behavior

PTM 216B releases are the telegram most likely to be lost over the air
(see [docs/evidence-findings.md](docs/evidence-findings.md)). If a
press's release never arrives, the next verified press for that same
button silently resets its pending state first -- no spurious
`short_press`, no duplicate `long_press`, nothing retroactive. The only
practical effect of a lost release is that the orphaned press's own
short/long resolution is simply abandoned; the next real press/release
pair behaves normally.

### Automating from the device page

Every commissioned button's `press`, `release`, `short_press`, and
`long_press` are also offered directly as **device triggers** in the
automation editor -- pick the integration's device, then the button and
action, with no need to know about event entities. A one-rocker switch
offers triggers only for A0/A1, matching its actual event entities. The
underlying event entities (`event.<switch>_a0`, etc.) are unchanged and
still usable directly for anyone who prefers them.

### Safe user-visible test

1. On an already-commissioned switch, press and quickly release one
   button (well under half a second). Confirm only `press` then
   `short_press` then `release` fire, in that order -- no `long_press`.
2. Press and hold the same button past the threshold, still holding it.
   Confirm `long_press` fires *while still holding*, without waiting for
   release.
3. Release it. Confirm only `release` fires -- no second `short_press`.
4. From **Settings → Automations → Create automation → Add trigger →
   Device**, pick this switch's device, then a button and a
   `short_press`/`long_press`/`press`/`release` trigger, and confirm it
   is offered and fires correctly end-to-end.
5. Open the switch's subentry **Reconfigure**, lower the threshold, save,
   and repeat step 2 with a shorter hold -- confirm `long_press` now
   fires sooner, with no recommissioning prompt and no address/key field
   shown.

## Six button states and 1-rocker aliasing (Phase 5D)

A PTM 216B's switch-status byte can report six real, distinct button
patterns, not just the four single-button ones (A0, A1, B0, B1). The other
two -- **A0+B0** and **A1+B1** -- are genuine, bindable combo patterns: a
single energy bow (the physical spring mechanism behind one rocker) drives
both of that rocker's channels at once, in the same telegram. Every other
multi-bit combination (the two diagonals A0+B1/A1+B0, any 3-bit combo, or
the 4-bit combo) has no physical rocker action that could produce it and is
still rejected fail-closed, exactly like before.

| Pattern | Button bits (bit4..bit1, press/release bit0 excluded) | How it happens |
| --- | --- | --- |
| A0 | `0b00010` | rocker "A"'s "0" half pressed alone |
| A1 | `0b00100` | rocker "A"'s "1" half pressed alone |
| B0 | `0b01000` | rocker "B"'s "0" half pressed alone |
| B1 | `0b10000` | rocker "B"'s "1" half pressed alone |
| A0+B0 | `0b01010` | both "0" halves actuate in the same telegram |
| A1+B1 | `0b10100` | both "1" halves actuate in the same telegram |

Per User Manual Figure 16: bit1 = A0, bit2 = A1, bit3 = B0, bit4 = B1 (bit0
is the separate press/release toggle, unrelated to which button); see
`telegram.py`'s `_BUTTON_BIT_PATTERNS` for the source of truth.

### Combo patterns only register when both rockers actuate together

**A0+B0 and A1+B1 fire only when both halves of a rocker genuinely actuate
within the SAME telegram** -- this is a hardware property (one energy bow,
one telegram), not something this integration infers from timing across
two separate telegrams. On a **two-rocker switch**, this means:

- pressing A0 alone still fires only the A0 entity, never A0+B0
- a genuinely simultaneous press of both the A0 and B0 halves of one rocker
  fires only the A0+B0 entity -- not A0, and not B0
- A0+B0/A1+B1 get their own independent short/long-press timing and their
  own device-trigger subtype, exactly like any single-button pattern (see
  `press_timing.py`)

One documented edge case: if a combo press's release telegram instead
decodes as a plain single-button pattern (e.g. a genuinely simultaneous
press's A0+B0 telegram is followed by a release telegram that only carries
A0, because the user's finger lifted off one half microseconds before the
other), that release fires only the plain button's `release` -- no
`short_press` for it -- and the combo's own open press is left orphaned,
exactly like any other lost-release case (see "Radio-loss behavior"
above). This falls out of the same generic per-pattern state machine with
no special-case code; see `tests/test_press_timing.py`'s
combo/partial-release test.

### 1-rocker aliasing

A one-rocker switch (a single full-width rocker plate, chosen as "1" in the
Add-device wizard's rocker count -- see "Rocker count" above) only ever has
two logical buttons, A0 and A1 -- but its raw telegrams can still decode to
any of six patterns, because the same physical mechanism that produces
A0+B0/A1+B1 on a two-rocker switch's genuinely-simultaneous press is *always*
what happens on a one-rocker switch's single energy bow. This integration
silently aliases all three raw patterns for each logical button to the one
entity that exists:

- **A0, B0, or A0+B0** → fires the **A0** entity
- **A1, B1, or A1+B1** → fires the **A1** entity

This normalization happens in exactly one place --
`telegram.normalize_button_pattern`, applied by
`runtime_data.CommissionedSwitchRuntime.record_verified_and_fire` before
the pattern ever reaches `press_timing.py`, an event entity, or a device
trigger -- so it never matters exactly where on the wide plate the user
pressed, and every layer downstream of that one point only ever sees the
two logical patterns a one-rocker switch actually has.

### Safe user-visible test

1. On a **two-rocker** commissioned switch, press A0 alone a few times.
   Confirm only the A0 entity fires -- never A0+B0.
2. If you can press both halves of one rocker together (physically or via
   the module's own combo-actuation behavior), confirm the A0+B0 (or
   A1+B1) entity fires instead of A0 and B0 individually.
3. Add a device trigger for the A0+B0 (or A1+B1) subtype from the
   automation editor and confirm it fires end-to-end on a combo press.
4. On a **one-rocker** commissioned switch, press the plate's "A" side a
   few times. Confirm only the A0 entity fires, and there is no B0 or
   A0+B0 entity to find in the entity list at all.
5. Verify **Verified telegrams** still increments normally for a
   one-rocker switch's presses -- aliasing changes only which entity
   fires, never whether a telegram counts as verified.

## Hue-grade device page presentation (Phase 6)

A commissioned switch's device page now presents its button events the
same way the official Hue integration presents "Friends of Hue" switch
buttons: each event entity carries the standard **button** device class
(the button icon on the Events card), a translated name -- "Button A0" in
English, "Painike A0" in Finnish, and so on for every pattern including
the combo ones ("Button A0+B0" / "Painike A0+B0") -- and a translated
last-event description ("Short press"/"Lyhyt painallus" instead of the
raw `short_press`). The automation editor's device-trigger picker got the
same treatment: trigger subtypes read as "Button A0" / "Buttons A0+B0
together" (Finnish: "Painike A0" / "Painikkeet A0+B0 yhtä aikaa") instead
of bare pattern codes.

None of this touches `unique_id`, `entity_id`, the verification pipeline,
press-timing semantics, or subentry data shape -- only presentation.
`entity_id` is generated from `unique_id` once and never regenerated, so
it is unaffected either way. The DISPLAYED name, however, is not frozen:
Home Assistant recomputes it from the live translation on every restart
or reload for any entity the user has not manually renamed, so an
already-commissioned switch automatically starts showing "Button A0"
after upgrading -- no re-add, no manual rename needed. An entity the user
*has* manually renamed in the UI keeps that custom name, exactly HA's
normal rename-wins behavior.

### Safe user-visible test

1. On an already-commissioned switch (created before this upgrade),
   restart Home Assistant (or reload this integration's config entry).
   Open the device page's Events card and confirm each button event now
   shows a button icon and reads "Button A0" (or "Painike A0" in a
   Finnish-language installation) instead of the bare "A0".
2. Press a button and confirm the entity's last-event description reads
   as a translated phrase ("Short press"/"Lyhyt painallus") rather than
   the raw `short_press`.
3. From **Settings → Automations → Create automation → Add trigger →
   Device**, pick this switch's device and confirm the button picker
   reads "Button A0" / "Buttons A0+B0 together" (or the Finnish
   equivalents) rather than bare pattern codes, and that selecting one
   and saving still fires correctly end-to-end.
4. Rename one button entity manually in the UI, then restart Home
   Assistant again. Confirm that entity keeps your custom name while
   every other, never-renamed entity still shows the new "Button …" name.

## Test installation with HACS

Requires Home Assistant **2025.7.0** or newer (Phase 5A's per-switch
Add-device wizard uses config subentries, which reached general
availability in that release).

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
