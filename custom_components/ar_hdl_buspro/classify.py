"""Device classification helpers built on the HDL catalogue.

Pure functions only (no Home Assistant imports), so the bus scan and the
config flow share one implementation and it can be tested standalone.

Precedence used by the config flow, highest first:

1. Field-confirmed pins in const.py (HDL_TYPE_TO_DEVICE_TYPE, keypad /
   dimmer / no-entity sets, HDL_TYPE_CHANNEL_COUNT).
2. The device's own self-description: its 0xE549 reply to the 0xE548
   "read device module" request, which lists its functions as
   (big, small, count) triples - the same request the HDL Setup Tool uses.
3. The HDL catalogue (hdl_catalog.py): model, family and function list for
   ~1,800 type codes, taken from the HDL Setup Tool.
4. What the device answered during the scan (the original heuristics).
"""

# Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).
# All rights reserved. Proprietary and confidential.
# Licensed software - see LICENSE in the repository root.
from __future__ import annotations

from .hdl_catalog import HDL_CATALOG, HUMIDITY_PANEL_CODES, RCU_22CH_CODES

# HDL "big type" values used in function triples.
BIG_LIGHT = 1
BIG_CURTAIN = 2
BIG_PANEL = 4
BIG_SENSOR = 5
BIG_AC = 7
BIG_FLOOR_HEAT = 8

# Small types under BIG_LIGHT.
SMALL_DIMMER = 0
SMALL_RELAY = 1

# Catalogue families -> role in this integration. None = no opinion, let the
# scan replies decide (protocols this integration doesn't drive, or families
# too varied to call from the code alone).
FAMILY_ROLE: dict[str, str | None] = {
    "dimmer": "light",
    "relay": "switch",
    "curtain": "cover",
    "ir": "universal_switch",
    "dry_contact": "binary_sensor",
    "sensor": "sensor",
    "sensor_7in1": "sensor",
    "sensor_8in1": "sensor",
    "sensor_12in1": "sensor",
    "sensor_temp": "sensor",
    "keypad": "keypad",
    "dlp_panel": "keypad",
    "touch_panel": "keypad",
    "gateway": "gateway",
    "logic": "logic",
    "rcu": "switch",
}

# Families whose sensor protocol this integration already decodes.
FAMILY_HW_KIND: dict[str, str] = {
    "sensor_7in1": "sensors_in_one",
    "sensor_8in1": "8in1",
    "sensor_12in1": "12in1",
}

# Panel families: a panel that also answers a floor-heating read is a
# climate device, one that answers the channel temperature read has an
# onboard temperature sensor worth importing; otherwise buttons only.
PANEL_FAMILIES = frozenset({"keypad", "dlp_panel", "touch_panel"})


def code_int(type_code: str | None) -> int | None:
    """'0x01BD' -> 0x01BD; None for missing / malformed codes."""
    if not type_code:
        return None
    try:
        value = int(str(type_code), 16)
    except ValueError:
        return None
    return value if 0 < value <= 0xFFFF else None


def catalog_entry(type_code: str | None):
    """Return (model, description, family, functions) or None."""
    value = code_int(type_code)
    if value is None:
        return None
    return HDL_CATALOG.get(value)


def catalog_family(type_code: str | None) -> str:
    entry = catalog_entry(type_code)
    return entry[2] if entry else ""


def catalog_name(type_code: str | None) -> str | None:
    """Friendly 'MODEL - description' for scan labels."""
    entry = catalog_entry(type_code)
    if not entry:
        return None
    model, desc = entry[0], entry[1]
    if model and desc:
        return f"{model} - {desc}"
    return model or desc or None


def triples(functions) -> list[tuple[int, int, int]]:
    """Flat function list -> [(big, small, count)], dropping empties."""
    out: list[tuple[int, int, int]] = []
    if not functions:
        return out
    seq = [int(x) & 0xFF for x in functions]
    for i in range(0, len(seq) - len(seq) % 3, 3):
        big, small, count = seq[i], seq[i + 1], seq[i + 2]
        # Big type 0 carries a base-panel address on relay/dimmer bases,
        # not a function.
        if big == 0 or count <= 0:
            continue
        out.append((big, small, count))
    return out


def parse_module_reply(payload) -> tuple[str | None, list[int] | None]:
    """Decode a 0xE549 'device module' reply payload.

    Layout (from the HDL Setup Tool): [0:2] echo of the request's two
    session bytes, [2:22] the device remark (null padded), [22:] function
    triples. Returns (remark_bytes_as_text_or_None, functions_or_None).
    """
    try:
        data = [int(b) & 0xFF for b in (payload or [])]
    except (TypeError, ValueError):
        return None, None
    if len(data) < 22:
        return None, None
    raw = bytes(data[2:22]).split(b"\x00", 1)[0].decode("latin-1", "ignore")
    remark = "".join(ch for ch in raw if 32 <= ord(ch) < 127).strip() or None
    rest = data[22:]
    if not rest or len(rest) % 3:
        return remark, None
    return remark, rest


def role_from_functions(funcs) -> str | None:
    """Role implied by a function list (self-reported or catalogue)."""
    tr = triples(funcs)
    if not tr:
        return None
    bigs = {b for b, _s, _c in tr}
    light = [(s, c) for b, s, c in tr if b == BIG_LIGHT]
    if light:
        if all(s == SMALL_DIMMER for s, _c in light):
            return "light"
        return "switch"
    if BIG_CURTAIN in bigs:
        return "cover"
    if BIG_SENSOR in bigs:
        # (5, 0, n) = dry-contact inputs; anything else is a real sensor.
        if all(s in (0, 25) for b, s, _c in tr if b == BIG_SENSOR):
            return "binary_sensor"
        return "sensor"
    if BIG_PANEL in bigs:
        if all(s == 0 for b, s, _c in tr if b == BIG_PANEL):
            return "binary_sensor"  # (4, 0, n) dry-contact zones
        return "keypad"
    return None


def dry_contact_zones(funcs) -> int | None:
    """Dry-contact zone count from a function list, if it states one."""
    total = 0
    for big, small, count in triples(funcs):
        if (big == BIG_PANEL and small == 0) or (big == BIG_SENSOR and small == 0):
            total += count
    return total or None


def channel_plan_from_functions(funcs, role: str) -> list[tuple[int, str]] | None:
    """Channel -> role list for light/switch/cover devices.

    Light channels are numbered on from 1 across every light triple in
    order, so a mixed module (e.g. 4 relays then 2 dimmers) maps to the bus
    channel numbers the module itself uses.
    """
    tr = triples(funcs)
    if role == "cover":
        n = sum(c for b, _s, c in tr if b == BIG_CURTAIN)
        return [(i, "cover") for i in range(1, n + 1)] if n else None
    if role not in ("light", "switch"):
        return None
    plan: list[tuple[int, str]] = []
    channel = 0
    for big, small, count in tr:
        if big != BIG_LIGHT:
            continue
        kind = "light" if small == SMALL_DIMMER else "switch"
        for _ in range(count):
            channel += 1
            plan.append((channel, kind))
    return plan or None


def rcu_plan(type_code: str | None) -> list[tuple[int, str]] | None:
    """HDL's fixed 22-channel RCU layout: 18-21 dimmers, the rest relays."""
    value = code_int(type_code)
    if value is None or value not in RCU_22CH_CODES:
        return None
    return [(ch, "light" if 18 <= ch <= 21 else "switch") for ch in range(1, 23)]


def panel_has_humidity(type_code: str | None) -> bool:
    value = code_int(type_code)
    return value is not None and value in HUMIDITY_PANEL_CODES


def function_summary(funcs) -> str:
    """Short human summary, e.g. '18 relay + 4 dimmer'."""
    parts: list[str] = []
    for big, small, count in triples(funcs):
        if big == BIG_LIGHT:
            parts.append(f"{count} {'dimmer' if small == SMALL_DIMMER else 'relay'}")
        elif big == BIG_CURTAIN:
            parts.append(f"{count} curtain")
        elif big == BIG_PANEL:
            parts.append(f"{count} {'zone' if small == 0 else 'button'}")
        elif big == BIG_SENSOR:
            parts.append(f"{count} sensor")
        elif big == BIG_AC:
            parts.append(f"{count} AC")
        elif big == BIG_FLOOR_HEAT:
            parts.append(f"{count} floor heat")
    return " + ".join(parts)
