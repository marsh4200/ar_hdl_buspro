"""AR HDL Manager: the sidebar page for keypad LED colours.

Kept apart from the integration's own devices: the manager has its own
keypad scan and its own saved keypad list (one list per hub), and adding a
keypad here creates no entities and changes nothing in the hub's options.

Frontend: frontend/ar-hdl-manager.js, a sidebar panel for admins. It talks
to the websocket commands below.
"""

# Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).
# All rights reserved. Proprietary and confidential.
# Licensed software - see LICENSE in the repository root.
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import DOMAIN, HDL_NO_ENTITY_ROLES, HDL_TYPE_NAMES
from .classify import catalog_entry, catalog_name
from .keypad_colours import (
    SCHEME_DLP,
    ColourError,
    KeypadColours,
    dlp_led_pages,
    led_button_count,
    led_scheme_for,
)
from .licensing import get_unlock
from .keypad_led import OP_PANEL_CONTROL, PANEL_CONTROL_BUTTON_STATUS
from .pybuspro.core.telegram import Telegram

_LOGGER = logging.getLogger(__name__)

PANEL_URL_PATH = "ar-hdl-manager"
PANEL_ELEMENT = "ar-hdl-manager-panel"
PANEL_TITLE = "AR HDL Manager"
PANEL_ICON = "mdi:palette-swatch-outline"
STATIC_URL = f"/{DOMAIN}_static"
FRONTEND_DIR = Path(__file__).parent / "frontend"
FRONTEND_FILE = "ar-hdl-manager.js"

STORE_VERSION = 1
DATA_MANAGER = f"{DOMAIN}_manager"
DATA_MANAGER_SETUP = f"{DOMAIN}_manager_setup"

SCAN_SECONDS_DEFAULT = 8
SCAN_SECONDS_MAX = 60
MAX_BUTTONS = 64


class HubManager:
    """One hub's saved keypads and its colour client."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self._store: Store = Store(hass, STORE_VERSION, f"{DOMAIN}.manager_{entry_id}")
        self.keypads: dict[str, dict[str, Any]] = {}
        self._loaded = False
        self._colours: KeypadColours | None = None
        self._colours_hdl = None
        self.scan_lock = asyncio.Lock()
        self.last_scan: dict[str, dict[str, Any]] = {}

    async def async_load(self) -> None:
        if self._loaded:
            return
        data = await self._store.async_load() or {}
        for kp in data.get("keypads", []):
            if isinstance(kp, dict) and kp.get("key"):
                self.keypads[str(kp["key"])] = kp
        self._loaded = True

    async def async_save(self) -> None:
        await self._store.async_save({"keypads": self.sorted_keypads()})

    async def async_remove_store(self) -> None:
        await self._store.async_remove()

    def sorted_keypads(self) -> list[dict[str, Any]]:
        return sorted(
            self.keypads.values(),
            key=lambda k: (int(k.get("subnet", 0)), int(k.get("device", 0))),
        )

    def gateway(self):
        data = self.hass.data.get(DOMAIN, {}).get(self.entry_id)
        return getattr(data, "gateway", None)

    def hdl(self):
        """The live bus client, or None while the hub is offline."""
        gateway = self.gateway()
        if gateway is None or not gateway.available:
            return None
        return gateway.hdl

    def blocked(self) -> str | None:
        """Why the bus can't be used right now, in words; None if it can."""
        if get_unlock(self.hass) is None:
            return "The AR HDL BUSPRO licence is not active on this install."
        if self.hdl() is None:
            return "The gateway is not connected."
        return None

    def colours(self) -> KeypadColours | None:
        hdl = self.hdl()
        if hdl is None:
            return None
        if self._colours is None or self._colours_hdl is not hdl:
            self._colours = KeypadColours(hdl)
            self._colours_hdl = hdl
        return self._colours


def _hub(hass: HomeAssistant, entry_id: str) -> HubManager:
    hubs = hass.data.setdefault(DATA_MANAGER, {})
    hub = hubs.get(entry_id)
    if hub is None:
        hub = hubs[entry_id] = HubManager(hass, entry_id)
    return hub


_BUTTONS_IN_NAME = re.compile(r"(\d{1,2})\s*-?\s*(?:buttons?|keys?|gang)\b", re.I)
DEFAULT_BUTTONS = 8


def button_hint(type_code, counted: int) -> int:
    """Best guess at a keypad's button count before it has been read.

    The catalogue's own count is often just 1 for multi-button panels, while
    its description says "4 button touch panel" - so the description wins,
    then a count above 1, then 8. The first colour read settles it: the
    manager keeps however many buttons the keypad actually answered for.
    """
    entry = catalog_entry(type_code)
    if entry:
        found = _BUTTONS_IN_NAME.search(f"{entry[1]} {entry[0]}")
        if found and 1 <= int(found.group(1)) <= MAX_BUTTONS:
            return int(found.group(1))
        if counted > 1:
            return counted
    return DEFAULT_BUTTONS


def keypad_record(disc, buttons: int) -> dict[str, Any]:
    """What the manager keeps about a keypad found by its scan."""
    scheme = led_scheme_for(disc.type_code)
    if scheme != SCHEME_DLP:
        buttons = button_hint(disc.type_code, buttons)
    if scheme == SCHEME_DLP:
        pages, last = dlp_led_pages(disc.type_code)
        pages = min(pages, max(1, (buttons + 7) // 8))
        count = pages * 8
        per_page = min(8, last)
    else:
        count = led_button_count(disc.type_code, buttons)
        per_page = 0
    model = (
        HDL_TYPE_NAMES.get(disc.type_code)
        or catalog_name(disc.type_code)
        or "Keypad"
    )
    return {
        "key": disc.key,
        "subnet": disc.subnet_id,
        "device": disc.device_id,
        "address": disc.address,
        "type_code": disc.type_code,
        "name": (getattr(disc, "remark", None) or "").strip() or model,
        "model": model,
        "buttons": max(1, min(int(count), MAX_BUTTONS)),
        "scheme": scheme,
        "per_page": per_page,
    }


# ---------------------------------------------------------------------------
# Websocket API (admin only)
# ---------------------------------------------------------------------------
def _entries(hass: HomeAssistant):
    return hass.config_entries.async_entries(DOMAIN)


async def _resolve(hass, connection, msg) -> HubManager | None:
    entry_id = msg["entry_id"]
    if not any(e.entry_id == entry_id for e in _entries(hass)):
        connection.send_error(msg["id"], "not_found", "That hub no longer exists.")
        return None
    hub = _hub(hass, entry_id)
    await hub.async_load()
    return hub


def _keypad(hub: HubManager, connection, msg) -> dict[str, Any] | None:
    keypad = hub.keypads.get(msg["key"])
    if keypad is None:
        connection.send_error(
            msg["id"], "not_found", "That keypad is not in the manager."
        )
    return keypad


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/manager/hubs"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_hubs(hass, connection, msg) -> None:
    hubs = []
    for entry in _entries(hass):
        hub = _hub(hass, entry.entry_id)
        await hub.async_load()
        hubs.append({
            "entry_id": entry.entry_id,
            "title": entry.title or "AR HDL BUSPRO",
            "online": hub.hdl() is not None,
            "keypads": len(hub.keypads),
        })
    connection.send_result(msg["id"], {"hubs": hubs})


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/manager/keypads",
    vol.Required("entry_id"): str,
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_keypads(hass, connection, msg) -> None:
    hub = await _resolve(hass, connection, msg)
    if hub is None:
        return
    connection.send_result(
        msg["id"], {"keypads": hub.sorted_keypads(), "online": hub.hdl() is not None}
    )


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/manager/scan",
    vol.Required("entry_id"): str,
    vol.Optional("duration", default=SCAN_SECONDS_DEFAULT): vol.All(
        vol.Coerce(int), vol.Range(min=3, max=SCAN_SECONDS_MAX)
    ),
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_scan(hass, connection, msg) -> None:
    """Scan the bus and return its keypads; nothing is saved or imported."""
    hub = await _resolve(hass, connection, msg)
    if hub is None:
        return
    reason = hub.blocked()
    if reason:
        connection.send_error(msg["id"], "offline", reason)
        return
    hdl = hub.hdl()
    if hub.scan_lock.locked():
        connection.send_error(msg["id"], "busy", "A scan is already running.")
        return
    from .discovery import SCAN_TIMEOUT_MARGIN, BusScanner

    async with hub.scan_lock:
        scanner = BusScanner(hdl)
        try:
            found = await asyncio.wait_for(
                scanner.scan(msg["duration"]),
                timeout=msg["duration"] + SCAN_TIMEOUT_MARGIN,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("AR HDL Manager keypad scan failed: %s", err)
            connection.send_error(msg["id"], "scan_failed", "The scan did not finish. Try again.")
            return
    results: list[dict[str, Any]] = []
    hub.last_scan = {}
    for disc in found:
        scheme = led_scheme_for(disc.type_code)
        is_keypad = scanner._is_keypad_candidate(disc)  # noqa: SLF001
        if scheme is None and not is_keypad:
            continue
        if disc.type_code in HDL_NO_ENTITY_ROLES:
            continue
        record = keypad_record(disc, scanner._button_count(disc))  # noqa: SLF001
        hub.last_scan[record["key"]] = record
        results.append({**record, "added": record["key"] in hub.keypads})
    _LOGGER.info(
        "AR HDL Manager keypad scan: %d device(s) on the bus, %d keypad(s), "
        "%d with a colour setting",
        len(found), len(results), sum(1 for r in results if r["scheme"]),
    )
    connection.send_result(msg["id"], {"found": results, "devices": len(found)})


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/manager/add",
    vol.Required("entry_id"): str,
    vol.Required("keys"): [str],
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_add(hass, connection, msg) -> None:
    hub = await _resolve(hass, connection, msg)
    if hub is None:
        return
    added = 0
    for key in msg["keys"]:
        record = hub.last_scan.get(key)
        if record is None or record.get("scheme") is None:
            continue
        previous = hub.keypads.get(key)
        if previous and previous.get("custom_name"):
            record = {**record, "name": previous["name"], "custom_name": True}
        if previous and previous.get("button_names"):
            record = {**record, "button_names": previous["button_names"]}
        hub.keypads[key] = dict(record)
        added += 1
    if added:
        await hub.async_save()
    connection.send_result(msg["id"], {"added": added, "keypads": hub.sorted_keypads()})


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/manager/remove",
    vol.Required("entry_id"): str,
    vol.Required("key"): str,
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_remove(hass, connection, msg) -> None:
    hub = await _resolve(hass, connection, msg)
    if hub is None:
        return
    if hub.keypads.pop(msg["key"], None) is not None:
        await hub.async_save()
    connection.send_result(msg["id"], {"keypads": hub.sorted_keypads()})


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/manager/rename",
    vol.Required("entry_id"): str,
    vol.Required("key"): str,
    vol.Optional("name"): str,
    vol.Optional("button_names"): {str: str},
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_rename(hass, connection, msg) -> None:
    """Names kept in Home Assistant only; nothing is written to the keypad."""
    hub = await _resolve(hass, connection, msg)
    if hub is None:
        return
    keypad = _keypad(hub, connection, msg)
    if keypad is None:
        return
    name = (msg.get("name") or "").strip()
    if name:
        keypad["name"] = name[:60]
        keypad["custom_name"] = True
    if "button_names" in msg:
        names = dict(keypad.get("button_names") or {})
        for button, label in msg["button_names"].items():
            label = label.strip()[:24]
            if label:
                names[str(button)] = label
            else:
                names.pop(str(button), None)
        keypad["button_names"] = names
    await hub.async_save()
    connection.send_result(msg["id"], {"keypad": keypad})


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/manager/read",
    vol.Required("entry_id"): str,
    vol.Required("key"): str,
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_read(hass, connection, msg) -> None:
    hub = await _resolve(hass, connection, msg)
    if hub is None:
        return
    keypad = _keypad(hub, connection, msg)
    if keypad is None:
        return
    reason = hub.blocked()
    if reason:
        connection.send_error(msg["id"], "offline", reason)
        return
    colours = hub.colours()
    try:
        result = await colours.read(
            (keypad["subnet"], keypad["device"]), keypad["type_code"], keypad["buttons"]
        )
    except ColourError as err:
        connection.send_error(msg["id"], "no_answer", str(err))
        return
    answered = len(result["colors"])
    if result["scheme"] != SCHEME_DLP and answered and answered != keypad["buttons"]:
        # The keypad has the last word on how many buttons it has.
        keypad["buttons"] = answered
        await hub.async_save()
    connection.send_result(msg["id"], {**result, "keypad": keypad})


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/manager/save",
    vol.Required("entry_id"): str,
    vol.Required("key"): str,
    vol.Required("colors"): [
        vol.Schema(
            {
                vol.Required("button"): vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_BUTTONS)),
                vol.Required("on"): str,
                vol.Required("off"): str,
                vol.Optional("rgbmode"): vol.Coerce(int),
                vol.Optional("colortype"): vol.Coerce(int),
            }
        )
    ],
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_save(hass, connection, msg) -> None:
    hub = await _resolve(hass, connection, msg)
    if hub is None:
        return
    keypad = _keypad(hub, connection, msg)
    if keypad is None:
        return
    reason = hub.blocked()
    if reason:
        connection.send_error(msg["id"], "offline", reason)
        return
    colours = hub.colours()
    try:
        written = await colours.save(
            (keypad["subnet"], keypad["device"]), keypad["type_code"], msg["colors"]
        )
    except ColourError as err:
        connection.send_error(msg["id"], "no_answer", str(err))
        return
    _LOGGER.info(
        "AR HDL Manager: saved %d button colour(s) to keypad %s",
        written, keypad["address"],
    )
    connection.send_result(msg["id"], {"written": written})


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/manager/led",
    vol.Required("entry_id"): str,
    vol.Required("key"): str,
    vol.Required("button"): vol.All(vol.Coerce(int), vol.Range(min=1, max=255)),
    vol.Required("on"): bool,
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_led(hass, connection, msg) -> None:
    """Show a button's on / off state on the real keypad; switches nothing."""
    hub = await _resolve(hass, connection, msg)
    if hub is None:
        return
    keypad = _keypad(hub, connection, msg)
    if keypad is None:
        return
    reason = hub.blocked()
    ni = None if reason else getattr(hub.hdl(), "network_interface", None)
    if ni is None:
        connection.send_error(msg["id"], "offline", reason or "The gateway is not connected.")
        return
    telegram = Telegram()
    telegram.target_address = (int(keypad["subnet"]), int(keypad["device"]))
    telegram.operate_code = OP_PANEL_CONTROL
    telegram.payload = [PANEL_CONTROL_BUTTON_STATUS, msg["button"], 1 if msg["on"] else 0]
    await ni.send_telegram(telegram)
    connection.send_result(msg["id"], {})


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
async def async_setup_manager(hass: HomeAssistant, version: str = "") -> None:
    """Register the websocket commands and the sidebar page (once)."""
    if hass.data.get(DATA_MANAGER_SETUP):
        return
    hass.data[DATA_MANAGER_SETUP] = True
    for command in (
        ws_hubs, ws_keypads, ws_scan, ws_add, ws_remove, ws_rename,
        ws_read, ws_save, ws_led,
    ):
        websocket_api.async_register_command(hass, command)
    try:
        await _async_register_panel(hass, version)
    except Exception as err:  # noqa: BLE001 - the page must never block the bus
        _LOGGER.warning("AR HDL Manager sidebar page could not be added: %s", err)


async def _async_register_panel(hass: HomeAssistant, version: str) -> None:
    http = getattr(hass, "http", None)
    if http is None or "frontend" not in hass.config.components:
        return  # no web UI on this install: the API is still there
    try:
        from homeassistant.components.http import StaticPathConfig

        await http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL, str(FRONTEND_DIR), False)]
        )
    except ImportError:  # Home Assistant before 2024.7
        http.register_static_path(STATIC_URL, str(FRONTEND_DIR), False)
    from homeassistant.components import panel_custom
    from homeassistant.setup import async_setup_component

    if not await async_setup_component(hass, "panel_custom", {}):
        return

    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_ELEMENT,
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        module_url=f"{STATIC_URL}/{FRONTEND_FILE}?v={version or '0'}",
        embed_iframe=False,
        require_admin=True,
    )


@callback
def async_forget_hub(hass: HomeAssistant, entry_id: str) -> HubManager | None:
    """Drop a hub's manager state (the hub was removed)."""
    return hass.data.get(DATA_MANAGER, {}).pop(entry_id, None)
