"""Touch-panel AC (0xE3D8-0xE3DB) decode and command tests.

Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).

Loads only the pybuspro package (no Home Assistant needed):

    python3 tests/test_panel_ac.py
"""
import asyncio
import importlib
import os
import sys
import types

PKG = os.environ.get("AR_HDL_PKG", os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "custom_components", "ar_hdl_buspro")))

# Import pybuspro as a standalone package so HA isn't required.
pkg = types.ModuleType("pb")
pkg.__path__ = [os.path.join(PKG, "pybuspro")]
sys.modules["pb"] = pkg
enums = importlib.import_module("pb.helpers.enums")
panel_ac = importlib.import_module("pb.devices.panel_ac")
control = importlib.import_module("pb.devices.control")
OC = enums.OperateCode

SENT = []


async def _fake_send(self):
    SENT.append((self.operate_code, list(self.payload)))


control._GenericControl.send = _fake_send


class FakeBus:
    def __init__(self, loop):
        self.loop = loop
        self.cbs = []
        import logging
        self.logger = logging.getLogger("test")
        self.network_interface = None

    def register_telegram_received_device_cb(self, cb, addr, postfix=None):
        self.cbs.append(cb)

    def unregister_telegram_received_device_cb(self, cb, addr, postfix=None):
        if cb in self.cbs:
            self.cbs.remove(cb)


class T:
    def __init__(self, op, payload):
        self.operate_code = op
        self.payload = payload


def make(loop, ch=1, temp_ch=1):
    panel_ac.PanelAirConditioner._STARTUP_RETRY_SECONDS = 3600
    dev = panel_ac.PanelAirConditioner(FakeBus(loop), (1, 60), ch, temp_ch)
    dev._stopped = True  # keep background loops quiet in tests
    dev._call_device_updated = lambda: None
    return dev


async def main():
    loop = asyncio.get_running_loop()
    fails = 0

    def check(name, cond):
        nonlocal fails
        print(("PASS " if cond else "FAIL ") + name)
        fails += 0 if cond else 1

    # --- decode: AC-2 status captured on an Enviro panel ------------------
    d = make(loop, ch=2)
    check("unavailable before any status", not d.available)
    for field, value in ((3, 1), (4, 26), (5, 3), (6, 0), (7, 26)):
        d._telegram_received_cb(T(OC.ReadPanelACResponse, [field, value, 2]))
    check("available after status", d.available)
    check("power on", d.is_on)
    check("mode cool", d.hvac_mode == "cool")
    check("fan high", d.fan_speed == "high")
    check("target = cool setpoint", d.target_temperature == 26)

    # --- other channel ignored -------------------------------------------
    d._telegram_received_cb(T(OC.ControlPanelACResponse, [3, 0, 1]))
    check("other channel ignored", d.is_on)

    # --- panel-originated change (E3D9) ----------------------------------
    d._telegram_received_cb(T(OC.ControlPanelACResponse, [6, 1, 2]))
    d._telegram_received_cb(T(OC.ControlPanelACResponse, [7, 24, 2]))
    check("mode heat via E3D9", d.hvac_mode == "heat")
    check("target follows heat setpoint", d.target_temperature == 24)

    # --- junk ignored, state kept ----------------------------------------
    d._telegram_received_cb(T(OC.ControlPanelACResponse, [5, 9, 2]))
    d._telegram_received_cb(T(OC.ControlPanelACResponse, [4, 250, 2]))
    d._telegram_received_cb(T(OC.ControlPanelACResponse, [8, 1, 2]))
    d._telegram_received_cb(T(OC.ControlPanelACResponse, [3]))
    check("bad fan kept", d.fan_speed == "high")
    check("bad setpoint kept", d.cool_target_temperature == 26)

    # --- room temperature (Granite capture) ------------------------------
    t = make(loop, ch=1, temp_ch=1)
    t._telegram_received_cb(T(OC.ReadTemperatureResponse, [1, 27, 216, 163, 216, 65]))
    check("room temp float", t.current_temperature == 27.1)
    t._telegram_received_cb(T(OC.ReadTemperatureResponse, [2, 10, 0, 0, 32, 65]))
    check("other temp channel ignored", t.current_temperature == 27.1)
    t._telegram_received_cb(T(OC.ReadTemperatureResponse, [1, 26]))
    check("whole-degree fallback", t.current_temperature == 26)

    # --- commands ---------------------------------------------------------
    panel_ac.PanelAirConditioner._READBACK_DELAY = 0
    panel_ac.PanelAirConditioner._FIELD_READ_GAP = 0
    c = make(loop, ch=2)
    c._telegram_received_cb(T(OC.ReadPanelACResponse, [3, 0, 2]))
    c._telegram_received_cb(T(OC.ReadPanelACResponse, [6, 0, 2]))

    SENT.clear()
    await c.turn_on()
    await asyncio.sleep(0.05)
    check("turn_on writes [3,1,ch]", SENT[0] == (OC.ControlPanelAC, [3, 1, 2]))
    check("turn_on reads back power", (OC.ReadPanelAC, [3, 2, 2]) in SENT)
    check("state not optimistic", not c.is_on)

    SENT.clear()
    await c.set_hvac_mode("heat")
    await asyncio.sleep(0.05)
    check("mode from off powers on first", SENT[0] == (OC.ControlPanelAC, [3, 1, 2]))
    check("then writes mode heat", SENT[1] == (OC.ControlPanelAC, [6, 1, 2]))

    c._telegram_received_cb(T(OC.ControlPanelACResponse, [3, 1, 2]))
    c._telegram_received_cb(T(OC.ControlPanelACResponse, [6, 1, 2]))
    SENT.clear()
    await c.set_target_temperature(23)
    check("heat setpoint -> field 7", SENT[0] == (OC.ControlPanelAC, [7, 23, 2]))

    c._telegram_received_cb(T(OC.ControlPanelACResponse, [6, 0, 2]))
    SENT.clear()
    await c.set_target_temperature(25)
    check("cool setpoint -> field 4", SENT[0] == (OC.ControlPanelAC, [4, 25, 2]))

    SENT.clear()
    await c.set_fan_speed("low")
    check("fan low -> [5,1,ch]", SENT[0] == (OC.ControlPanelAC, [5, 1, 2]))

    await asyncio.sleep(0.05)  # let the fan read-back finish
    SENT.clear()
    await c.read_status()
    check("status read covers 5 fields + temp",
          [p for _, p in SENT] == [[3, 2, 2], [4, 2, 2], [5, 2, 2], [6, 2, 2], [7, 2, 2], [1]])

    # --- opcode bytes -----------------------------------------------------
    check("opcodes", (OC.ControlPanelAC.value, OC.ControlPanelACResponse.value,
                      OC.ReadPanelAC.value, OC.ReadPanelACResponse.value)
          == (b"\xE3\xD8", b"\xE3\xD9", b"\xE3\xDA", b"\xE3\xDB"))

    await asyncio.sleep(0.05)
    print(f"\n{'ALL PASSED' if not fails else f'{fails} FAILED'}")
    return fails


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main()) else 0)
