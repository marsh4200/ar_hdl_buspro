"""HDL Devices: the sidebar Device Manager.

A sidebar page that lists every HDL module this gateway knows about - from
a bus scan, from the configured devices, and from the modules seen answering
status reads - and lets an admin scan, add, rename, reverse a curtain and
remove devices without walking the options-flow menus.

It deliberately does NOT re-implement any of that logic. Scanning is the
same BusScanner the options flow uses; adding runs the options flow's own
scan-import step, and removing runs its own registry clean-up, so a device
added here is configured exactly as if it had been ticked in
Configure -> Scan bus. Controls on the page (open/close a curtain, switch a
channel) go through the normal Home Assistant entity services, so licence,
availability and the curtain "Reverse direction" option all apply.

Websocket commands (admin only):
  ar_hdl_buspro/dm/entries                      -> gateways
  ar_hdl_buspro/dm/state    {entry_id}          -> modules + scan status
  ar_hdl_buspro/dm/scan     {entry_id, duration}-> start a bus scan
  ar_hdl_buspro/dm/add      {entry_id, addresses}
  ar_hdl_buspro/dm/update   {entry_id, id, changes}
  ar_hdl_buspro/dm/remove   {entry_id, ids}
"""

# Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).
# All rights reserved. Proprietary and confidential.
# Licensed software - see LICENSE in the repository root. Unauthorised
# copying, redistribution, modification, or circumvention of the licence
# check in licensing.py is prohibited.
from __future__ import annotations

import asyncio
import logging
from collections import Counter
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CHANNEL,
    CONF_COVER_MODE,
    CONF_CURTAIN_NUMBER,
    CONF_DEVICE_ID,
    CONF_DEVICE_TYPE,
    CONF_DEVICES,
    CONF_DIMMER_CODES,
    CONF_DISCOVERED,
    CONF_GATEWAY_HOST,
    CONF_NAME,
    CONF_REVERSE_DIRECTION,
    CONF_SPLIT_CHANNELS,
    CONF_SUBNET_ID,
    CONF_SUB_NUMBER,
    CONF_TRAVEL_TIME,
    COVER_MODE_CURTAIN_MODULE,
    DEFAULT_SCAN_DURATION,
    DEFAULT_TRAVEL_TIME,
    DEVICE_TYPE_BINARY_SENSOR,
    DEVICE_TYPE_CLIMATE,
    DEVICE_TYPE_COVER,
    DEVICE_TYPE_KEYPAD_BUTTON,
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_RGB_LIGHT,
    DEVICE_TYPE_SENSOR,
    DEVICE_TYPE_SWITCH,
    DEVICE_TYPE_UNIVERSAL_SWITCH,
    DOMAIN,
    HA_VIRTUAL_ADDRESS,
    HDL_NO_ENTITY_ROLES,
    HDL_TYPE_NAMES,
    MAX_SCAN_DURATION,
    MIN_SCAN_DURATION,
    ROLE_KEYPAD,
)

_LOGGER = logging.getLogger(__name__)

PANEL_URL = "hdl-devices"
PANEL_ELEMENT = "ar-hdl-device-manager"
STATIC_URL = f"/{DOMAIN}_static"
_DATA_UI = f"{DOMAIN}_device_manager_ui"
_DATA_MANAGERS = f"{DOMAIN}_device_managers"
_STORE_VERSION = 1

# Role -> short label shown on the page.
ROLE_LABELS: dict[str, str] = {
    DEVICE_TYPE_LIGHT: "Dimmer",
    DEVICE_TYPE_SWITCH: "Relay",
    DEVICE_TYPE_RGB_LIGHT: "RGB light",
    DEVICE_TYPE_COVER: "Curtain",
    DEVICE_TYPE_SENSOR: "Sensor",
    DEVICE_TYPE_BINARY_SENSOR: "Input",
    DEVICE_TYPE_CLIMATE: "Climate",
    DEVICE_TYPE_UNIVERSAL_SWITCH: "Universal switch",
    ROLE_KEYPAD: "Panel",
    "gateway": "Gateway",
    "logic": "Logic",
}
# Role -> filter group on the page.
ROLE_GROUPS: dict[str, str] = {
    DEVICE_TYPE_LIGHT: "lights",
    DEVICE_TYPE_SWITCH: "lights",
    DEVICE_TYPE_RGB_LIGHT: "lights",
    DEVICE_TYPE_COVER: "curtain",
    DEVICE_TYPE_SENSOR: "sensor",
    DEVICE_TYPE_BINARY_SENSOR: "sensor",
    DEVICE_TYPE_CLIMATE: "panel",
    ROLE_KEYPAD: "panel",
}
# Entity domain for each configured device type.
TYPE_DOMAINS: dict[str, str] = {
    DEVICE_TYPE_LIGHT: "light",
    DEVICE_TYPE_RGB_LIGHT: "light",
    DEVICE_TYPE_SWITCH: "switch",
    DEVICE_TYPE_UNIVERSAL_SWITCH: "switch",
    DEVICE_TYPE_KEYPAD_BUTTON: "switch",
    DEVICE_TYPE_COVER: "cover",
    DEVICE_TYPE_SENSOR: "sensor",
    DEVICE_TYPE_BINARY_SENSOR: "binary_sensor",
    DEVICE_TYPE_CLIMATE: "climate",
}
# Fields the page may change on one configured device, with their coercion.
EDITABLE_FIELDS: dict[str, Any] = {
    CONF_NAME: lambda v: str(v).strip()[:100],
    CONF_REVERSE_DIRECTION: bool,
    CONF_TRAVEL_TIME: lambda v: max(1, min(600, int(v))),
}


def _addr(subnet: int, device: int) -> str:
    return f"{subnet}.{device}"


# ---------------------------------------------------------------------------
# Scan results kept across restarts
# ---------------------------------------------------------------------------
def _disc_to_json(disc) -> dict[str, Any]:
    data = asdict(disc)
    data["op_codes"] = sorted(disc.op_codes)
    data["button_targets"] = {
        str(b): {str(n): list(t) for n, t in targets.items()}
        for b, targets in (disc.button_targets or {}).items()
    }
    return data


def _disc_from_json(data: dict[str, Any]):
    from .discovery import DiscoveredDevice

    known = {f.name for f in fields(DiscoveredDevice)}
    kwargs = {k: v for k, v in data.items() if k in known}
    kwargs["op_codes"] = set(kwargs.get("op_codes") or [])
    kwargs["button_targets"] = {
        int(b): {int(n): tuple(t) for n, t in targets.items()}
        for b, targets in (kwargs.get("button_targets") or {}).items()
    }
    return DiscoveredDevice(**kwargs)


class DeviceManager:
    """Per-gateway state for the page: the last scan and a running scan."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self._store = Store(hass, _STORE_VERSION, f"{DOMAIN}.device_manager_{entry_id}")
        self.results: list[Any] = []
        self.scanned_at: str | None = None
        self.scan_task: asyncio.Task | None = None
        self.scan_started: str | None = None
        self.scan_duration = DEFAULT_SCAN_DURATION
        self.scan_error: str | None = None
        self._loaded = False

    async def async_load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            data = await self._store.async_load() or {}
        except Exception as err:  # noqa: BLE001 - an old file must not break the page
            _LOGGER.debug("Device manager: could not load the last scan: %s", err)
            return
        results = []
        for item in data.get("devices", []):
            try:
                results.append(_disc_from_json(item))
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Device manager: skipped a stored scan row: %s", err)
        self.results = results
        self.scanned_at = data.get("scanned_at")

    @property
    def scanning(self) -> bool:
        return self.scan_task is not None and not self.scan_task.done()

    def start_scan(self, gateway, duration: int) -> None:
        from .discovery import SCAN_TIMEOUT_MARGIN, BusScanner

        self.scan_duration = duration
        self.scan_error = None
        self.scan_started = dt_util.utcnow().isoformat()
        scanner = BusScanner(gateway.hdl)

        async def _run() -> None:
            try:
                found = await asyncio.wait_for(
                    scanner.scan(duration), timeout=duration + SCAN_TIMEOUT_MARGIN
                )
            except (asyncio.TimeoutError, RuntimeError, OSError) as err:
                _LOGGER.warning("AR HDL BUSPRO device manager scan failed: %s", err)
                self.scan_error = "The bus scan failed. Check the gateway is online and try again."
                return
            self.results = found
            self.scanned_at = dt_util.utcnow().isoformat()
            await self._store.async_save(
                {"scanned_at": self.scanned_at, "devices": [_disc_to_json(d) for d in found]}
            )

        self.scan_task = self.hass.async_create_task(_run(), name=f"{DOMAIN} device manager scan")


def _manager(hass: HomeAssistant, entry_id: str) -> DeviceManager:
    managers = hass.data.setdefault(_DATA_MANAGERS, {})
    if entry_id not in managers:
        managers[entry_id] = DeviceManager(hass, entry_id)
    return managers[entry_id]


# ---------------------------------------------------------------------------
# Reusing the options flow
# ---------------------------------------------------------------------------
def _flow(hass: HomeAssistant, entry: ConfigEntry):
    """An options-flow handler for `entry`, usable outside a flow manager."""
    from .config_flow import ARHDLOptionsFlow

    flow = ARHDLOptionsFlow(entry)
    flow.hass = hass
    flow.handler = entry.entry_id
    flow.flow_id = f"{DOMAIN}_device_manager"
    flow.context = {"source": "device_manager"}
    return flow


def _result_type(result) -> str:
    return str(getattr(result.get("type"), "value", result.get("type")))


def _entry_for(hass: HomeAssistant, entry_id: str) -> ConfigEntry | None:
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        return None
    return entry


def _gateway_for(hass: HomeAssistant, entry_id: str):
    data = hass.data.get(DOMAIN, {}).get(entry_id)
    return getattr(data, "gateway", None)


def _friendly_model(disc) -> str:
    from .classify import catalog_name

    if disc is None:
        return ""
    return (
        HDL_TYPE_NAMES.get(disc.type_code)
        or catalog_name(disc.type_code)
        or (disc.type_name if disc.type_name and disc.type_name != "Unknown" else "")
        or (f"type {disc.type_code}" if disc.type_code and disc.type_code != "0x0000" else "")
    )


def _entity_ids(ent_reg, entry_id: str, cfg: dict[str, Any]) -> list[str]:
    prefix = f"{entry_id}_{cfg.get('id')}"
    return sorted(
        e.entity_id
        for e in er.async_entries_for_config_entry(ent_reg, entry_id)
        if e.unique_id == prefix or e.unique_id.startswith(f"{prefix}_")
    )


def build_state(hass: HomeAssistant, entry: ConfigEntry, manager: DeviceManager) -> dict[str, Any]:
    """Everything the page shows for one gateway."""
    from .config_flow import _disc_channel_plan

    flow = _flow(hass, entry)
    dimmer_codes = flow._saved_dimmer_codes()  # noqa: SLF001 - same module family
    gateway = _gateway_for(hass, entry.entry_id)
    known = set(getattr(getattr(gateway, "hdl", None), "known_modules", set()) or set())
    ent_reg = er.async_get(hass)

    configured: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for cfg in entry.options.get(CONF_DEVICES, []):
        try:
            key = (int(cfg[CONF_SUBNET_ID]), int(cfg[CONF_DEVICE_ID]))
        except (KeyError, TypeError, ValueError):
            continue
        if key == HA_VIRTUAL_ADDRESS:
            continue  # keypad buttons answered by HA itself, not a module
        configured.setdefault(key, []).append(cfg)

    scanned = {(d.subnet_id, d.device_id): d for d in manager.results}
    addresses = sorted(set(configured) | set(scanned) | known)

    modules = []
    for key in addresses:
        disc = scanned.get(key)
        cfgs = configured.get(key, [])
        if disc is not None:
            role = flow._infer_device_type(disc, dimmer_codes)  # noqa: SLF001
        elif cfgs:
            role = Counter(c.get(CONF_DEVICE_TYPE) for c in cfgs).most_common(1)[0][0]
        else:
            role = ""
        no_entities = role == ROLE_KEYPAD or role in HDL_NO_ENTITY_ROLES.values()

        # How many entities a scan import would still add for this module.
        to_add = 0
        if disc is not None and not no_entities:
            have = {c.get(CONF_CHANNEL, c.get(CONF_CURTAIN_NUMBER)) for c in cfgs}
            plan = _disc_channel_plan(disc, role) if role in (DEVICE_TYPE_LIGHT, DEVICE_TYPE_SWITCH, DEVICE_TYPE_COVER) else None
            if plan:
                to_add = sum(1 for ch, _t in plan if ch not in have)
            elif not cfgs:
                to_add = 1

        entities = []
        for cfg in cfgs:
            dtype = cfg.get(CONF_DEVICE_TYPE)
            entities.append(
                {
                    "id": cfg.get("id"),
                    "type": dtype,
                    "type_label": ROLE_LABELS.get(dtype, dtype or ""),
                    "domain": TYPE_DOMAINS.get(dtype, ""),
                    "name": cfg.get(CONF_NAME) or "",
                    "channel": cfg.get(CONF_CHANNEL),
                    "sub_number": cfg.get(CONF_SUB_NUMBER),
                    "curtain_number": cfg.get(CONF_CURTAIN_NUMBER),
                    "cover_mode": cfg.get(CONF_COVER_MODE, COVER_MODE_CURTAIN_MODULE) if dtype == DEVICE_TYPE_COVER else None,
                    "reverse_direction": bool(cfg.get(CONF_REVERSE_DIRECTION, False)),
                    "travel_time": int(cfg.get(CONF_TRAVEL_TIME, DEFAULT_TRAVEL_TIME)) if dtype == DEVICE_TYPE_COVER else None,
                    "entity_ids": _entity_ids(ent_reg, entry.entry_id, cfg),
                }
            )
        entities.sort(key=lambda e: (e["type"] or "", e["channel"] or e["curtain_number"] or e["sub_number"] or 0, e["name"]))

        remark = getattr(disc, "remark", None) if disc is not None else None
        modules.append(
            {
                "address": _addr(*key),
                "subnet": key[0],
                "device": key[1],
                "name": remark or "",
                "model": _friendly_model(disc),
                "type_code": getattr(disc, "type_code", "") if disc is not None else "",
                "role": role,
                "role_label": ROLE_LABELS.get(role, role.replace("_", " ").capitalize() if role else "Unknown"),
                "group": ROLE_GROUPS.get(role, "other"),
                "in_ha": bool(cfgs),
                "to_add": to_add,
                "can_add": to_add > 0,
                "no_entities": no_entities,
                "in_scan": disc is not None,
                "answers_status": key in known,
                "channel_count": getattr(disc, "channel_count", None) if disc is not None else None,
                "entities": entities,
            }
        )

    return {
        "entry_id": entry.entry_id,
        "title": entry.title,
        "host": entry.data.get(CONF_GATEWAY_HOST, ""),
        "available": bool(getattr(gateway, "available", False)),
        "loaded": gateway is not None,
        "scan": {
            "running": manager.scanning,
            "started": manager.scan_started,
            "duration": manager.scan_duration,
            "last": manager.scanned_at,
            "error": manager.scan_error,
            "found": len(manager.results),
        },
        "modules": modules,
    }


async def async_add_modules(hass: HomeAssistant, entry: ConfigEntry, manager: DeviceManager, addresses: list[str]) -> int:
    """Import the given scanned modules exactly like Configure -> Scan bus."""
    by_addr = {d.address: d for d in manager.results}
    keys = [by_addr[a].key for a in addresses if a in by_addr]
    if not keys:
        raise ValueError("Scan the bus first: these devices are not in the last scan.")
    flow = _flow(hass, entry)
    flow._scan_results = list(manager.results)  # noqa: SLF001
    before = len(entry.options.get(CONF_DEVICES, []))
    result = await flow.async_step_scan_results(
        {
            CONF_DISCOVERED: keys,
            CONF_SPLIT_CHANNELS: True,
            CONF_DIMMER_CODES: ", ".join(sorted(flow._saved_dimmer_codes())),  # noqa: SLF001
        }
    )
    # A new AC / heating entity asks for setpoint limits next; the page
    # keeps the default range (same as leaving the boxes at 0).
    guard = 0
    while _result_type(result) == "form" and result.get("step_id") == "scan_climate_limits" and guard < 200:
        result = await flow.async_step_scan_climate_limits({})
        guard += 1
    if _result_type(result) != "create_entry":
        raise ValueError("Could not add these devices here. Use Configure, then Scan bus.")
    data = result["data"]
    added = len(data.get(CONF_DEVICES, [])) - before
    hass.config_entries.async_update_entry(entry, options=data)
    return added


def async_update_device(hass: HomeAssistant, entry: ConfigEntry, device_id: str, changes: dict[str, Any]) -> None:
    devices = [dict(d) for d in entry.options.get(CONF_DEVICES, [])]
    target = next((d for d in devices if d.get("id") == device_id), None)
    if target is None:
        raise ValueError("That device is no longer configured.")
    for key, value in changes.items():
        coerce = EDITABLE_FIELDS.get(key)
        if coerce is None:
            raise ValueError(f"{key} can't be changed here.")
        if key in (CONF_REVERSE_DIRECTION, CONF_TRAVEL_TIME) and target.get(CONF_DEVICE_TYPE) != DEVICE_TYPE_COVER:
            raise ValueError(f"{key} only applies to curtains.")
        value = coerce(value)
        if key == CONF_NAME and not value:
            raise ValueError("The name can't be empty.")
        target[key] = value
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_DEVICES: devices})


def async_remove_devices(hass: HomeAssistant, entry: ConfigEntry, ids: list[str]) -> int:
    chosen = set(ids)
    devices = list(entry.options.get(CONF_DEVICES, []))
    flow = _flow(hass, entry)
    removed = 0
    for target in devices:
        if target.get("id") in chosen:
            flow._purge_registry_for_device(target)  # noqa: SLF001 - frees entity ids
            removed += 1
    if removed:
        hass.config_entries.async_update_entry(
            entry,
            options={**entry.options, CONF_DEVICES: [d for d in devices if d.get("id") not in chosen]},
        )
    return removed


# ---------------------------------------------------------------------------
# Websocket API
# ---------------------------------------------------------------------------
def _err(connection, msg, text: str) -> None:
    connection.send_error(msg["id"], "invalid", text)


@websocket_api.websocket_command({vol.Required("type"): "ar_hdl_buspro/dm/entries"})
@websocket_api.require_admin
@callback
def ws_entries(hass: HomeAssistant, connection, msg) -> None:
    entries = [
        {
            "entry_id": e.entry_id,
            "title": e.title,
            "host": e.data.get(CONF_GATEWAY_HOST, ""),
            "available": bool(getattr(_gateway_for(hass, e.entry_id), "available", False)),
        }
        for e in hass.config_entries.async_entries(DOMAIN)
    ]
    connection.send_result(msg["id"], {"entries": entries})


@websocket_api.websocket_command({vol.Required("type"): "ar_hdl_buspro/dm/state", vol.Required("entry_id"): str})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_state(hass: HomeAssistant, connection, msg) -> None:
    entry = _entry_for(hass, msg["entry_id"])
    if entry is None:
        _err(connection, msg, "Unknown gateway.")
        return
    manager = _manager(hass, entry.entry_id)
    await manager.async_load()
    connection.send_result(msg["id"], build_state(hass, entry, manager))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ar_hdl_buspro/dm/scan",
        vol.Required("entry_id"): str,
        vol.Optional("duration", default=DEFAULT_SCAN_DURATION): vol.All(
            vol.Coerce(int), vol.Range(min=MIN_SCAN_DURATION, max=MAX_SCAN_DURATION)
        ),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_scan(hass: HomeAssistant, connection, msg) -> None:
    entry = _entry_for(hass, msg["entry_id"])
    if entry is None:
        _err(connection, msg, "Unknown gateway.")
        return
    gateway = _gateway_for(hass, entry.entry_id)
    if gateway is None or not gateway.available:
        _err(connection, msg, "The gateway is offline, so the bus can't be scanned.")
        return
    manager = _manager(hass, entry.entry_id)
    await manager.async_load()
    if not manager.scanning:
        manager.start_scan(gateway, msg["duration"])
    connection.send_result(msg["id"], {"running": True, "duration": manager.scan_duration})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ar_hdl_buspro/dm/add",
        vol.Required("entry_id"): str,
        vol.Required("addresses"): [str],
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_add(hass: HomeAssistant, connection, msg) -> None:
    entry = _entry_for(hass, msg["entry_id"])
    if entry is None:
        _err(connection, msg, "Unknown gateway.")
        return
    manager = _manager(hass, entry.entry_id)
    await manager.async_load()
    try:
        added = await async_add_modules(hass, entry, manager, msg["addresses"])
    except ValueError as err:
        _err(connection, msg, str(err))
        return
    except Exception as err:  # noqa: BLE001 - report, don't kill the socket
        _LOGGER.exception("AR HDL BUSPRO device manager: add failed")
        _err(connection, msg, f"Adding failed: {err}")
        return
    connection.send_result(msg["id"], {"added": added})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ar_hdl_buspro/dm/update",
        vol.Required("entry_id"): str,
        vol.Required("id"): str,
        vol.Required("changes"): dict,
    }
)
@websocket_api.require_admin
@callback
def ws_update(hass: HomeAssistant, connection, msg) -> None:
    entry = _entry_for(hass, msg["entry_id"])
    if entry is None:
        _err(connection, msg, "Unknown gateway.")
        return
    try:
        async_update_device(hass, entry, msg["id"], msg["changes"])
    except (ValueError, TypeError) as err:
        _err(connection, msg, str(err))
        return
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ar_hdl_buspro/dm/remove",
        vol.Required("entry_id"): str,
        vol.Required("ids"): [str],
    }
)
@websocket_api.require_admin
@callback
def ws_remove(hass: HomeAssistant, connection, msg) -> None:
    entry = _entry_for(hass, msg["entry_id"])
    if entry is None:
        _err(connection, msg, "Unknown gateway.")
        return
    removed = async_remove_devices(hass, entry, msg["ids"])
    connection.send_result(msg["id"], {"removed": removed})


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
async def async_setup_device_manager(hass: HomeAssistant, version: str) -> None:
    """Register the websocket commands and the sidebar page, once per HA run.

    Done from async_setup, not per entry, so the page stays put while an
    entry reloads after the page itself changed its options.
    """
    if hass.data.get(_DATA_UI):
        return
    hass.data[_DATA_UI] = True
    for handler in (ws_entries, ws_state, ws_scan, ws_add, ws_update, ws_remove):
        websocket_api.async_register_command(hass, handler)
    try:
        from homeassistant.components import panel_custom
        from homeassistant.components.http import StaticPathConfig

        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL, str(Path(__file__).parent / "www"), cache_headers=False)]
        )
        await panel_custom.async_register_panel(
            hass,
            frontend_url_path=PANEL_URL,
            webcomponent_name=PANEL_ELEMENT,
            sidebar_title="HDL Devices",
            sidebar_icon="mdi:view-list",
            module_url=f"{STATIC_URL}/device-manager.js?v={version}",
            require_admin=True,
            config={},
        )
    except Exception as err:  # noqa: BLE001 - the integration works without the page
        _LOGGER.error("Could not add the HDL Devices sidebar page (/%s): %s", PANEL_URL, err, exc_info=True)
