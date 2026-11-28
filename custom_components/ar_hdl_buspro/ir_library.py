"""HDL IR emitter modules (HDL-MIRC01T / MIRC04 family): scan-time decoding.

Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).

Everything here is taken from HDL Studio V1.9.65 (DeviceCommon.dll,
FourChannelIRSender and Buspro Studio's FourChannelIRUVDataGirdView), so the
scan can name the universal switches an IR module answers to the same way
HDL's own tool does ("Lounge TV Power") instead of "switch 1".

Two reads, both addressed to the module:

* 0x137C [device_no, 0, 0] -> 0x137D: one of the module's 24 stored IR
  devices. Studio reads payload[2] as the IR library device type, [3..4] as
  the code index, [5] as the code length and [7..26] as the 20-byte name the
  installer gave the device. (Slots 1-4 of Studio's list are the module's
  four channels and are read with a trailing 1 instead of 0; they are not
  needed here.)
* 0xDB96 [switch_no, status] -> 0xDB97: what the module does when universal
  switch `switch_no` is turned ON (status 1) or OFF (status 0). Studio reads
  BOTH for every switch 1-99 - the two are configured independently - and
  keeps payload[0..10]:
      [0] switch no (echo)   [1] status (echo)   [2] IR channel
      [3] action: 1 = AC state, 2 = IR device key
      [4] AC channel (action 1) / stored device no 1-24, 25 = learned code
      [5] key number (action 2) / AC power (action 1)

The reply operate codes are the request + 1, which is how every other HDL
read/reply pair used by this integration works (0x000E/0x000F,
0xF00E/0xF00F, 0xE548/0xE549); they have not been confirmed against a live
capture yet, so everything here is decoded defensively and a module that
answers nothing simply imports the way it always did.
"""
from __future__ import annotations

from typing import Final

# HDL-MIRC01T.40 and the HDL-MIRC04.40 variants (hdl_catalog.py). Studio
# treats all of them with the same 4-slot channel offset.
IR_MODULE_TYPE_CODES: Final = frozenset({0x0514, 0x0515, 0x0516, 0x0517, 0x0518})

READ_IR_DEVICE_OP: Final = b"\x13\x7c"
READ_IR_DEVICE_REPLY: Final = "0x137D"
READ_UV_ACTION_OP: Final = b"\xdb\x96"
READ_UV_ACTION_REPLY: Final = "0xDB97"

IR_STORED_DEVICES: Final = 24
IR_UV_MAX: Final = 99
# Device number Studio uses for "a code learned on the module itself".
IR_LEARNED_DEVICE: Final = IR_STORED_DEVICES + 1

ACTION_AC: Final = 1
ACTION_IR_KEY: Final = 2

# HDL's IR library device types (dbNewIRKeyDefine.DeviceID). A stored
# device's library type byte is this minus one.
IR_LIBRARY_DEVICE_NAMES: Final = {
    1: "Projector",
    2: "Fan",
    3: "Set top box",
    4: "DVD",
    5: "TV",
    6: "AC",
    7: "AC",
}

# Key names per library device type, from HDL Studio's dbNewIRKeyDefine
# table (English column): {DeviceID: {KeyID: name}}.
IR_LIBRARY_KEYS: Final = {
    1: {  # Projector
        1: 'ON',
        2: 'OFF',
        3: 'Computer',
        4: 'Video',
        5: 'Source',
        6: 'Zoom+',
        7: 'Zoom-',
        8: 'Screen+',
        9: 'Screen-',
        10: 'Menu',
        11: 'OK',
        12: 'UP',
        13: 'Left',
        14: 'Right',
        15: 'Down',
        16: 'Exit',
        17: 'Volume+',
        18: 'Volume-',
        19: 'Mute',
        20: 'Auto',
        21: 'Pause',
        22: 'Brightness',
    },
    2: {  # Fan
        1: 'Power',
        2: 'Turn ON',
        3: 'Pivoting',
        4: 'Mode',
        5: 'Timing',
        6: 'Lighting',
        7: 'Anion',
        8: '1',
        9: '2',
        10: '3',
        11: '4',
        12: '5',
        13: '6',
        14: '7',
        15: '8',
        16: '9',
        17: 'Sleep',
        18: 'Cooling',
        19: 'Air flow',
        20: 'Low',
        21: 'Medium',
        22: 'High',
    },
    3: {  # Set top box
        1: 'Standby',
        2: '1',
        3: '2',
        4: '3',
        5: '4',
        6: '5',
        7: '6',
        8: '7',
        9: '8',
        10: '9',
        11: 'Home Page',
        12: '0',
        13: 'Back',
        14: 'Up',
        15: 'Left',
        16: 'OK',
        17: 'Right',
        18: 'Down',
        19: 'Volume+',
        20: 'Volume-',
        21: 'Channel+',
        22: 'Channel-',
        23: 'Menu',
    },
    4: {  # DVD
        1: 'Left',
        2: 'Up',
        3: 'OK',
        4: 'Down',
        5: 'Right',
        6: 'Power',
        7: 'Mute',
        8: 'Backward',
        9: 'Play',
        10: 'Forward',
        11: 'Previous',
        12: 'Stop',
        13: 'Next',
        14: 'System',
        15: 'Pause',
        16: 'Title',
        17: 'Switch',
        18: 'Menu',
        19: 'Back',
    },
    5: {  # TV
        1: 'Volume-',
        2: 'Channel+',
        3: 'Menu',
        4: 'Channel-',
        5: 'Volume+',
        6: 'Power',
        7: 'Mute',
        8: '1',
        9: '2',
        10: '3',
        11: '4',
        12: '5',
        13: '6',
        14: '7',
        15: '8',
        16: '9',
        17: '-/--',
        18: '0',
        19: 'AV/TV',
        20: 'Back',
        21: 'OK',
        22: 'Up',
        23: 'Left',
        24: 'Right',
        25: 'Down',
    },
    6: {  # AC
        1: 'Power',
        2: 'Mode',
        3: 'Fan',
        4: 'Not swing',
        5: 'Swing',
    },
    7: {  # AC
        6: 'Temp+',
        7: 'Temp-',
    },
}


def decode_name(raw) -> str | None:
    """Decode a 20-byte HDL name field (Studio: CommonFunction.Byte2String).

    Cut at the first 0x00 or at an FF FF FF run (an unwritten field), then
    decoded. Studio uses the PC's ANSI code page; UTF-8 then GB18030 (the
    Chinese code page HDL ships with) then Latin-1 covers what installers
    actually type.
    """
    try:
        data = bytes(int(b) & 0xFF for b in raw)
    except (TypeError, ValueError):
        return None
    data = data.split(b"\x00", 1)[0]
    cut = data.find(b"\xff\xff\xff")
    if cut >= 0:
        data = data[:cut]
    if not data:
        return None
    for enc in ("utf-8", "gb18030"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = data.decode("latin-1")
    text = "".join(ch for ch in text if ch.isprintable()).strip()
    return text or None


def parse_device_reply(payload) -> tuple[int, int, str | None] | None:
    """0x137D -> (device_no, library_type, name), or None if unusable."""
    try:
        p = [int(b) & 0xFF for b in payload]
    except (TypeError, ValueError):
        return None
    if len(p) < 7 or not 1 <= p[0] <= IR_STORED_DEVICES:
        return None
    return p[0], p[2], decode_name(p[7:27])


def parse_uv_reply(payload) -> tuple[int, int, list[int]] | None:
    """0xDB97 -> (switch_no, status, config[0..10]), or None if unusable."""
    try:
        p = [int(b) & 0xFF for b in payload]
    except (TypeError, ValueError):
        return None
    if len(p) < 6 or not 1 <= p[0] <= IR_UV_MAX or p[1] not in (0, 1):
        return None
    return p[0], p[1], (p[:11] + [0] * 11)[:11]


def is_configured(cfg) -> bool:
    """True if this universal-switch action does something on the module."""
    if not cfg or len(cfg) < 6:
        return False
    if cfg[3] == ACTION_AC:
        return 1 <= cfg[4] <= 4
    if cfg[3] == ACTION_IR_KEY:
        return 1 <= cfg[4] <= IR_LEARNED_DEVICE
    return False


def describe_action(cfg, devices: dict[int, tuple[int, str | None]]) -> str | None:
    """A human name for one universal-switch action, or None if unconfigured.

    `devices` is {device_no: (library_type, name)} from the 0x137D replies.
    Mirrors how Studio labels the row (device name + key name).
    """
    if not is_configured(cfg):
        return None
    if cfg[3] == ACTION_AC:
        return f"AC {cfg[4]} {'on' if cfg[5] else 'off'}"
    device_no, key = cfg[4], cfg[5]
    if device_no == IR_LEARNED_DEVICE:
        return f"Learned code {key}"
    lib_type, name = devices.get(device_no, (None, None))
    library_id = lib_type + 1 if isinstance(lib_type, int) and lib_type != 0xFF else None
    key_name = IR_LIBRARY_KEYS.get(library_id, {}).get(key) or f"key {key}"
    device_name = name or IR_LIBRARY_DEVICE_NAMES.get(library_id) or f"IR device {device_no}"
    return f"{device_name} {key_name}"
