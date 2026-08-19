"""Passive EnOcean PTM 216B Bluetooth advertisement observer."""

from __future__ import annotations

import logging

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant, callback

from . import button_pipeline
from .commissioning_store import CommissioningStore
from .const import ENOCEAN_MANUFACTURER_ID
from .runtime_data import Ptm216bRuntimeData
from .secret_store import IntegrationSecretStore

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "event"]

# Pre-Phase-5A (v0.4.0) commissioned switches predate the rocker-count
# concept; a two-rocker switch is the only shape that phase ever supported.
_MIGRATED_ROCKER_COUNT = 2


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """One-time migration: backfill a "switch" subentry per pre-5A record.

    Called automatically by Home Assistant before ``async_setup_entry``
    whenever ``entry.version``/``minor_version`` differ from the flow's.
    Home Assistant does NOT bump the entry version itself (verified in
    ``config_entries.ConfigEntry.async_migrate``: a ``True`` result only
    schedules a save of whatever this function already changed) -- so this
    function MUST call ``async_update_entry(entry, version=...)``, or it
    re-runs on every reload. It is also written to be idempotent (existing
    subentries are skipped) so that entries whose version was left at 1 by
    the original, non-bumping v0.5.0-v0.6.1 releases recover cleanly
    instead of crashing on ``already_configured``.

    Before Phase 5A (config subentries), a commissioned switch's only
    footprint was its ``commissioning_store.py`` record -- there were no
    subentries at all yet. This creates exactly one "switch" subentry per
    existing store record so the ongoing reconciliation pass in
    :func:`async_setup_entry` (which purges a store record whose handle no
    longer matches any subentry -- the decommission-via-subentry-deletion
    path) does not mistake "not migrated yet" for "user deleted it".
    """
    if entry.version >= 2:
        return True

    secret = await IntegrationSecretStore(hass).async_get_or_create()
    commissioning_store = CommissioningStore(hass)
    await commissioning_store.async_load()
    runtime = Ptm216bRuntimeData(
        _hmac_secret=secret, commissioning_store=commissioning_store
    )

    existing_unique_ids = {subentry.unique_id for subentry in entry.subentries.values()}
    for canonical_address, switch in commissioning_store.switches.items():
        handle = runtime.commissioned_device_handle(canonical_address)
        if handle in existing_unique_ids:
            continue
        hass.config_entries.async_add_subentry(
            entry,
            ConfigSubentry(
                data={
                    "handle": handle,
                    "name": switch.name,
                    "rockers": _MIGRATED_ROCKER_COUNT,
                },
                subentry_type="switch",
                title=switch.name,
                unique_id=handle,
            ),
        )

    hass.config_entries.async_update_entry(entry, version=2)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Register a passive callback; this integration never connects to BLE devices."""
    secret = await IntegrationSecretStore(hass).async_get_or_create()
    commissioning_store = CommissioningStore(hass)
    await commissioning_store.async_load()
    entry.runtime_data = Ptm216bRuntimeData(
        _hmac_secret=secret, commissioning_store=commissioning_store
    )
    await _async_reconcile_commissioning_store(
        entry.runtime_data, entry, commissioning_store
    )
    entry.async_on_unload(entry.add_update_listener(_async_subentries_changed))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    @callback
    def _handle_advertisement(
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        """Observe matching advertisements; commissioned switches also decode."""
        entry.runtime_data.advertisement_count += 1
        entry.runtime_data.record_advertisement_observation(
            service_info.address,
            service_info.manufacturer_data,
            service_info.connectable,
        )
        sensor = getattr(entry.runtime_data, "sensor", None)
        if sensor is not None:
            sensor.async_write_ha_state()
        _LOGGER.debug("Observed matching EnOcean BLE advertisement")

        # Entirely additive: a no-op for every address that is not currently
        # commissioned. See button_pipeline.py for the fail-closed pipeline
        # this drives for commissioned switches only.
        button_pipeline.handle_advertisement(
            entry.runtime_data,
            service_info.address,
            service_info.manufacturer_data,
            hass.async_create_task,
        )

    entry.async_on_unload(
        bluetooth.async_register_callback(
            hass,
            _handle_advertisement,
            {"manufacturer_id": ENOCEAN_MANUFACTURER_ID, "connectable": False},
            bluetooth.BluetoothScanningMode.PASSIVE,
        )
    )
    return True


async def _async_reconcile_commissioning_store(
    runtime: Ptm216bRuntimeData, entry: ConfigEntry, store: CommissioningStore
) -> None:
    """Purge a store record whose subentry no longer exists.

    Runs on every setup/reload, not just once -- this is the decommission
    side of Phase 5A's per-switch wizard: deleting a "switch" subentry (from
    the frontend, or via ``hass.config_entries.async_remove_subentry``)
    already cleans up its device/entity registrations automatically (see
    ``homeassistant.config_entries.ConfigEntries.async_remove_subentry`` ->
    ``async_clear_config_subentry``), but that mechanism knows nothing about
    this integration's *private* commissioning store -- there is no
    per-subentry "on removed" hook to call ``store.async_remove`` from
    directly. Comparing the store's addresses against the live subentries'
    handles here, every reload, is the idiomatic substitute (see
    ``homeassistant.components.mqtt``'s own subentry-change reaction for the
    same pattern). Safe to run unconditionally: by the time this runs,
    ``entry.version`` is already >= 2 (migration, which backfills a subentry
    for every pre-5A record, always runs first), so a store record with no
    matching subentry can only mean "the user removed it," never "not
    migrated yet."
    """
    live_handles = {
        subentry.data.get("handle") for subentry in entry.subentries.values()
    }
    stale_addresses = [
        address
        for address in list(store.switches)
        if runtime.commissioned_device_handle(address) not in live_handles
    ]
    for address in stale_addresses:
        await store.async_remove(address)


async def _async_subentries_changed(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload immediately so a subentry add/update/remove takes effect at once.

    Mirrors ``homeassistant.components.mqtt.__init__._async_config_entry_updated``.
    Reloading itself does not change the entry's data/subentries, so it does
    not re-fire this same listener -- no reload loop.
    """
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload callbacks/platforms and discard ephemeral capture state."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entry.runtime_data.cancel_designation_capture()
        entry.runtime_data.cancel_evidence_capture()
        entry.runtime_data.cancel_radio_census()
        entry.runtime_data.clear_press_timers()
    return unload_ok
