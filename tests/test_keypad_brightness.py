"""Keypad backlight / status-LED brightness (no Home Assistant needed).

Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).

    python3 tests/test_keypad_brightness.py

Layouts from HDL Studio V1.9.65 (PanelBasic.Read/SavePanelBrightness):
read 0xE010 -> 0xE011, write 0xE012. Only the two levels may change; the
button lock and everything else go back exactly as the panel sent them.
"""
import asyncio
import importlib
import os
import sys
import types

PKG = os.environ.get("AR_HDL_PKG", os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "custom_components", "ar_hdl_buspro")))
root = types.ModuleType("arhdl_bright")
root.__path__ = [PKG]
sys.modules["arhdl_bright"] = root
importlib.import_module("arhdl_bright.pybuspro.devices.control")
kc = importlib.import_module("arhdl_bright.keypad_colours")
FAILS = []


def check(label, cond, detail=""):
    print(("ok  " if cond else "FAIL") + f" {label}" + (f" -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(label)


def test_payloads():
    # A plain touch panel (2061): [backlight, status, lock].
    check("plain panel: levels at 0/1, lock kept",
          kc.brightness_payload(2061, [80, 60, 1, 9, 9], 40, 20) == [40, 20, 1])
    # "No backlight" panel (2085): status LEDs lead.
    check("status-first panel: status at 0", kc.brightness_payload(2085, [60, 80, 0], None, 25) == [25, 80, 0])
    check("status-first panel has no backlight slider", not kc.brightness_has_backlight(2085))
    # Manual-lock panel (2351): 4 bytes, manual-lock key kept.
    check("manual-lock panel: 4 bytes, key kept", kc.brightness_payload(2351, [90, 90, 0, 7], 10, 10) == [10, 10, 0, 7])
    # FangYue 2.1 (2366): 6 bytes, jade + auto-lock kept.
    check("FangYue 2.1: 6 bytes kept", kc.brightness_payload(2366, [90, 90, 1, 3, 55, 30], 50, 70) == [50, 70, 1, 3, 55, 30])
    check("too-short reply is refused", kc.brightness_payload(2366, [90, 90, 1], 50, 70) is None)
    # DLP (165): Studio's fixed 11-byte block, lock capped at 1.
    check("DLP: Studio's 11-byte block", kc.brightness_payload(165, [70, 30, 2, 9, 9], 100, 0) == [100, 0, 1, 0, 1, 0, 0, 0, 0, 0, 0])
    check("levels clamped to 0-100", kc.brightness_payload(2061, [0, 0, 0], 300, -5) == [100, 0, 0])
    check("offset-shifted types left alone", not kc.brightness_supported(242))


class Tg:
    def __init__(self, op, payload, src):
        self.operate_code, self.payload, self.source_address = op, payload, src


class FakePanel:
    """A 2351 manual-lock panel at 1.30 holding [80, 60, 1, 4]."""

    def __init__(self):
        self.virtual_handlers, self.network_interface = [], self
        self.block, self.writes = [80, 60, 1, 4], []

    async def send_telegram(self, t):
        op = bytes(t.operate_code)
        loop = asyncio.get_running_loop()
        if op == b"\xe0\x10":
            loop.call_later(0.01, self._reply, b"\xe0\x11", list(self.block))
        elif op == b"\xe0\x12":
            self.writes.append(list(t.payload))
            self.block = list(t.payload)
            loop.call_later(0.01, self._reply, b"\xe0\x13", [0xF8])

    def _reply(self, op, payload):
        for h in list(self.virtual_handlers):
            h(Tg(op, payload, (1, 30)))


async def test_round_trip():
    bus = FakePanel()
    c = kc.KeypadColours(bus)
    try:
        await c.save_brightness((1, 30), 2351, 10, 10)
        check("save before read is refused", False)
    except kc.ColourError:
        check("save before read is refused", True)
    got = await c.read_brightness((1, 30), 2351)
    check("read gives levels", got == {"backlight": 80, "status": 60, "has_backlight": True}, str(got))
    await c.save_brightness((1, 30), 2351, 35, None)
    check("write changes only the backlight", bus.writes == [[35, 60, 1, 4]], str(bus.writes))
    await c.save_brightness((1, 30), 2351, None, 5)
    check("second write starts from the saved state", bus.writes[-1] == [35, 5, 1, 4], str(bus.writes))
    check("unsupported type is never asked", await c.read_brightness((1, 30), 243) is None)


async def main():
    test_payloads()
    await test_round_trip()
    print("ALL PASSED" if not FAILS else f"{len(FAILS)} FAILED")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
