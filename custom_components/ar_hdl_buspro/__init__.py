"""The AR HDL BUSPRO integration."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import (
    ATTR_ADDRESS,
    ATTR_OPERATE_CODE,
    ATTR_PAYLOAD,
    ATTR_SCENE_ADDRESS,
    ATTR_STATUS,
    ATTR_SWITCH_NUMBER,
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

_LOGGER = logging.getLogger(__name__)

# This integration is configured exclusively through the UI.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class ARHDLData:
    """Runtime data stored on the ConfigEntry."""

    gateway: ARHDLGateway


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

    # Register the gateway as a device in the device registry.
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"gateway_{entry.entry_id}")},
        manufacturer=MANUFACTURER,
        name=entry.title or f"AR HDL BUSPRO Gateway ({host})",
        model="HDL Buspro Gateway",
        configuration_url=f"http://{host}",
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _register_services(hass)

    return True


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
