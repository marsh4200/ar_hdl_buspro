"""The AR HDL BUSPRO integration."""

# Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).
# All rights reserved. Proprietary and confidential.
# Licensed software - see LICENSE in the repository root. Unauthorised
# copying, redistribution, modification, or circumvention of the licence
# check in licensing.py is prohibited.
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_ADDRESS,
    ATTR_OPERATE_CODE,
    ATTR_PAYLOAD,
    ATTR_SCENE_ADDRESS,
    ATTR_STATUS,
    ATTR_SWITCH_NUMBER,
    BINARY_KIND_MOTION,
    CONF_BINARY_KIND,
    CONF_DEVICE_HW_KIND,
    CONF_DEVICE_ID,
    CONF_DEVICE_TYPE,
    CONF_DEVICES,
    CONF_SCAN_INTERVAL,
    CONF_SUBNET_ID,
    DEFAULT_MOTION_SCAN_INTERVAL,
    DEVICE_HW_GENERIC,
    DEVICE_TYPE_BINARY_SENSOR,
    DEVICE_TYPE_SENSOR,
    LEGACY_BUNDLE_SCAN_INTERVAL,
    CONF_GATEWAY_HOST,
    CONF_GATEWAY_PORT,
    CONF_LOCAL_IP,
    DOMAIN,
    LEGACY_DOMAIN,
    MANUFACTURER,
    PLATFORMS,
    SERVICE_ACTIVATE_SCENE,
    SERVICE_SEND_MESSAGE,
    SERVICE_SET_UNIVERSAL_SWITCH,
)
from .gateway import ARHDLGateway
from .licensing import (
    DATA_LICENSE,
    RENEW_INTERVAL,
    SIGNAL_LICENSE_CHANGED,
    STATUS_LICENSED,
    STATUS_TRIAL,
    STATUS_TRIAL_EXPIRED,
    STATUS_TRIAL_NOT_STARTED,
    async_get_manager,
)

_LOGGER = logging.getLogger(__name__)

# This integration is configured exclusively through the UI.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class ARHDLData:
    """Runtime data stored on the ConfigEntry."""

    gateway: ARHDLGateway


# How often to re-evaluate the licence while running. The demo window can
# run out mid-session, and an expiry date can pass overnight, so entity
# availability has to move without waiting for a restart.
LICENSE_RECHECK_INTERVAL = timedelta(hours=1)
LICENSE_ISSUE_ID = "license_inactive"


# ---------------------------------------------------------------------------
# Service schemas
# ---------------------------------------------------------------------------
SERVICE_ACTIVATE_SCENE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ADDRESS): vol.All(
            cv.ensure_list, [cv.positive_int], vol.Length(min=2, max=2)
        ),
        vol.Required(ATTR_SCENE_ADDRESS): vol.All(
            cv.ensure_list, [cv.positive_int], vol.Length(min=2, max=2)
        ),
    }
)

SERVICE_SEND_MESSAGE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ADDRESS): vol.All(
            cv.ensure_list, [cv.positive_int], vol.Length(min=2, max=2)
        ),
        vol.Required(ATTR_OPERATE_CODE): vol.All(
            cv.ensure_list, [cv.positive_int], vol.Length(min=2, max=2)
        ),
        vol.Required(ATTR_PAYLOAD): vol.All(cv.ensure_list, [cv.positive_int]),
    }
)

SERVICE_UNIVERSAL_SWITCH_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ADDRESS): vol.All(
            cv.ensure_list, [cv.positive_int], vol.Length(min=2, max=2)
        ),
        vol.Required(ATTR_SWITCH_NUMBER): cv.positive_int,
        vol.Required(ATTR_STATUS): vol.In([0, 1]),
    }
)


# ---------------------------------------------------------------------------
# Setup / unload
# ---------------------------------------------------------------------------
async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the AR HDL BUSPRO integration (no-op; UI configuration only)."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up AR HDL BUSPRO from a config entry."""
    # Evaluate the licence before touching the bus. An unlicensed install is
    # still set up -- entities appear but report unavailable -- so the owner
    # can see exactly what they have and enter a key from the options flow
    # without rebuilding their whole configuration.
    manager = await async_get_manager(hass)
    # Mirror the trial anchor into this entry's data now that it exists, so
    # the window cannot be restarted by clearing .storage alone.
    await manager.async_sync_anchor(entry)
    _async_apply_license_state(hass)

    _async_repair_motion_entities(hass, entry)

    host = entry.data[CONF_GATEWAY_HOST]
    port = entry.data[CONF_GATEWAY_PORT]
    local_ip = entry.data.get(CONF_LOCAL_IP, "")

    gateway = ARHDLGateway(hass, entry.entry_id, host, port, local_ip)

    if not await gateway.async_connect():
        raise ConfigEntryNotReady(
            f"Could not connect to AR HDL BUSPRO gateway at {host}:{port}"
        )

    # Stop cleanly on HA shutdown. NOTE: this must be an async handler that
    # awaits the disconnect -- a plain lambda returning the coroutine would
    # never be awaited, so the socket would never actually close.
    async def _on_hass_stop(_event) -> None:
        await gateway.async_disconnect()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _on_hass_stop)
    )

    # Reload entry when options change so platform device lists refresh.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = ARHDLData(gateway=gateway)

    # Register the gateway as a device in the device registry. Every other
    # device links to it via_device_id (see build_device_info in entity.py)
    # rather than the deprecated via_device identifiers tuple, so stash the
    # id the registry hands back right here.
    device_registry = dr.async_get(hass)
    gateway_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"gateway_{entry.entry_id}")},
        manufacturer=MANUFACTURER,
        name=entry.title or f"AR HDL BUSPRO Gateway ({host})",
        model="HDL Buspro Gateway",
        configuration_url=f"http://{host}",
    )
    gateway.device_id = gateway_device.id

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _register_services(hass)

    @callback
    def _recheck_license(_now) -> None:
        _async_apply_license_state(hass)

    entry.async_on_unload(
        async_track_time_interval(
            hass, _recheck_license, LICENSE_RECHECK_INTERVAL
        )
    )

    # Lock at the exact moment the trial runs out rather than at the next
    # hourly recheck: entities go unavailable and the send-path gate stops
    # all bus traffic from that minute.
    if manager.state.status == STATUS_TRIAL and manager.trial_ends is not None:
        remaining = (manager.trial_ends - dt_util.utcnow()).total_seconds()
        if remaining > 0:
            entry.async_on_unload(
                async_call_later(hass, remaining + 1, _recheck_license)
            )

    # Background renewal against the licence server, when one is configured.
    # Keys issued for online installs are short-lived and rolled forward
    # here; a site that cannot reach the server keeps running on the key it
    # already holds until that key expires, which is what makes the grace
    # period work without a separate clock to tamper with.
    async def _renew_license(_now) -> None:
        await manager.async_renew_if_due()
        _async_apply_license_state(hass)

    entry.async_on_unload(
        async_track_time_interval(hass, _renew_license, RENEW_INTERVAL)
    )

    # One attempt shortly after startup, so a site that was offline when it
    # last tried catches up without waiting for the first interval.
    async def _renew_soon(_event) -> None:
        await manager.async_renew_if_due()
        _async_apply_license_state(hass)

    entry.async_on_unload(
        async_call_later(hass, timedelta(minutes=2), _renew_soon)
    )

    return True


@callback
def _async_apply_license_state(hass: HomeAssistant) -> None:
    """Re-evaluate the licence, nudge entities, and raise/clear the repair.

    Safe to call repeatedly: creating an issue that already exists and
    deleting one that doesn't are both no-ops in the issue registry.
    """
    manager = hass.data.get(DATA_LICENSE)
    if manager is None:
        return

    state = manager.evaluate()
    async_dispatcher_send(hass, SIGNAL_LICENSE_CHANGED, state.active)

    if state.status == STATUS_LICENSED:
        ir.async_delete_issue(hass, DOMAIN, LICENSE_ISSUE_ID)
        return

    ir.async_create_issue(
        hass,
        DOMAIN,
        LICENSE_ISSUE_ID,
        is_fixable=False,
        severity=(
            ir.IssueSeverity.WARNING
            if state.status == STATUS_TRIAL
            else ir.IssueSeverity.ERROR
        ),
        translation_key={
            STATUS_TRIAL: "license_trial",
            STATUS_TRIAL_EXPIRED: "license_trial_ended",
            STATUS_TRIAL_NOT_STARTED: "license_not_started",
        }.get(state.status, "license_inactive"),
        translation_placeholders={
            "server_id": state.server_id,
            "days_left": str(state.trial_days_left),
        },
        learn_more_url="https://activatelicense.arsmarthome.co.za",
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        data: ARHDLData = hass.data[DOMAIN].pop(entry.entry_id)
        await data.gateway.async_disconnect()

        # If this was the last entry, deregister services.
        if not hass.data[DOMAIN]:
            for service in (
                SERVICE_ACTIVATE_SCENE,
                SERVICE_SEND_MESSAGE,
                SERVICE_SET_UNIVERSAL_SWITCH,
            ):
                if hass.services.has_service(DOMAIN, service):
                    hass.services.async_remove(DOMAIN, service)

    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle removal of an entry."""
    # Nothing persisted outside HA itself; entry-scoped state has already
    # been unloaded by async_unload_entry. This hook exists for future
    # cleanup needs (cached calibration data, etc.).
    _LOGGER.debug("AR HDL BUSPRO entry %s removed", entry.entry_id)


@callback
def _async_repair_motion_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Give motion entities the protocol profile of their physical sensor.

    Bus-scan imports before 5.0.5 tagged a multisensor's temperature / lux /
    humidity entities with the sensor's hw kind (sensors_in_one for the
    7-in-1, 8in1 for the 8-in-1) but left the motion entity untagged, so it
    ran as "generic" and polled the wrong operate code. The reference buspro
    integration never has this split: one model -> one profile, shared by
    every entity on that sensor. This copies the sibling's hw kind onto each
    untagged motion entity and moves an untouched 60 s import default to the
    motion poll interval. Idempotent; runs before the update listener is
    attached, so it does not trigger a reload.
    """
    devices = entry.options.get(CONF_DEVICES)
    if not devices:
        return

    profile_by_addr: dict[tuple, str] = {}
    for d in devices:
        if d.get(CONF_DEVICE_TYPE) != DEVICE_TYPE_SENSOR:
            continue
        kind = d.get(CONF_DEVICE_HW_KIND)
        if kind and kind != DEVICE_HW_GENERIC:
            profile_by_addr.setdefault(
                (d.get(CONF_SUBNET_ID), d.get(CONF_DEVICE_ID)), kind
            )

    changed = 0
    new_devices = []
    for d in devices:
        if (
            d.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_BINARY_SENSOR
            and d.get(CONF_BINARY_KIND) == BINARY_KIND_MOTION
            and d.get(CONF_DEVICE_HW_KIND, DEVICE_HW_GENERIC) == DEVICE_HW_GENERIC
        ):
            kind = profile_by_addr.get((d.get(CONF_SUBNET_ID), d.get(CONF_DEVICE_ID)))
            if kind:
                d = dict(d)
                d[CONF_DEVICE_HW_KIND] = kind
                if int(d.get(CONF_SCAN_INTERVAL, 0)) == LEGACY_BUNDLE_SCAN_INTERVAL:
                    d[CONF_SCAN_INTERVAL] = DEFAULT_MOTION_SCAN_INTERVAL
                changed += 1
        new_devices.append(d)

    if changed:
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_DEVICES: new_devices}
        )
        _LOGGER.info(
            "AR HDL BUSPRO: matched %d motion entit(y/ies) to their sensor's profile",
            changed,
        )


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


# ---------------------------------------------------------------------------
# Migration: old `buspro` entries -> new `ar_hdl_buspro` entries, and schema upgrades
# ---------------------------------------------------------------------------
async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config entries to the new schema.

    Version 1 (legacy `buspro`): {"host": str, "port": int}
    Version 2 (current `ar_hdl_buspro`): {"gateway_host": str, "gateway_port": int, ...}
    """
    _LOGGER.info(
        "Migrating AR HDL BUSPRO config entry from version %s.%s",
        entry.version,
        entry.minor_version,
    )

    new_data = dict(entry.data)

    if entry.version < 2:
        # Translate legacy keys.
        if "host" in new_data and CONF_GATEWAY_HOST not in new_data:
            new_data[CONF_GATEWAY_HOST] = new_data.pop("host")
        if "port" in new_data and CONF_GATEWAY_PORT not in new_data:
            new_data[CONF_GATEWAY_PORT] = new_data.pop("port")
        new_data.setdefault(CONF_LOCAL_IP, "")

        hass.config_entries.async_update_entry(
            entry,
            data=new_data,
            version=2,
        )

    _LOGGER.info("AR HDL BUSPRO migration to version %s succeeded", entry.version)
    return True


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------
def _register_services(hass: HomeAssistant) -> None:
    """Register services for the integration (idempotent)."""
    if hass.services.has_service(DOMAIN, SERVICE_ACTIVATE_SCENE):
        return

    def _first_gateway() -> ARHDLGateway | None:
        """Return any registered gateway (services are global today)."""
        bucket = hass.data.get(DOMAIN, {})
        for data in bucket.values():
            if isinstance(data, ARHDLData):
                return data.gateway
        return None

    async def _activate_scene(call: ServiceCall) -> None:
        gw = _first_gateway()
        if gw is None:
            _LOGGER.error("No AR HDL BUSPRO gateway available for activate_scene")
            return
        # noinspection PyUnresolvedReferences
        from .pybuspro.devices.scene import Scene

        address = tuple(call.data[ATTR_ADDRESS])
        scene_address = tuple(call.data[ATTR_SCENE_ADDRESS])
        scene = Scene(gw.hdl, address, scene_address, "AR HDL BUSPRO Scene")
        await scene.run()

    async def _send_message(call: ServiceCall) -> None:
        gw = _first_gateway()
        if gw is None:
            _LOGGER.error("No AR HDL BUSPRO gateway available for send_message")
            return
        # noinspection PyUnresolvedReferences
        from .pybuspro.devices.generic import Generic

        address = tuple(call.data[ATTR_ADDRESS])
        payload = list(call.data[ATTR_PAYLOAD])
        operate_code = tuple(call.data[ATTR_OPERATE_CODE])
        generic = Generic(gw.hdl, address, payload, operate_code, "AR HDL BUSPRO Message")
        await generic.run()

    async def _set_universal_switch(call: ServiceCall) -> None:
        gw = _first_gateway()
        if gw is None:
            _LOGGER.error("No AR HDL BUSPRO gateway available for set_universal_switch")
            return
        # noinspection PyUnresolvedReferences
        from .pybuspro.devices.universal_switch import UniversalSwitch

        address = tuple(call.data[ATTR_ADDRESS])
        switch_number = call.data[ATTR_SWITCH_NUMBER]
        status = call.data[ATTR_STATUS]
        switch = UniversalSwitch(gw.hdl, address, switch_number)
        if status == 1:
            await switch.set_on()
        else:
            await switch.set_off()

    hass.services.async_register(
        DOMAIN,
        SERVICE_ACTIVATE_SCENE,
        _activate_scene,
        schema=SERVICE_ACTIVATE_SCENE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_MESSAGE,
        _send_message,
        schema=SERVICE_SEND_MESSAGE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_UNIVERSAL_SWITCH,
        _set_universal_switch,
        schema=SERVICE_UNIVERSAL_SWITCH_SCHEMA,
    )

    # Tell linters PLATFORMS is referenced; Platform import keeps Platform usable elsewhere.
    _ = Platform  # noqa: F841
