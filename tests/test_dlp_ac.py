"""DLP panel AC (0xE0EC/0xE0EE classic, 0x19AE/0x19B0 precise) and the
ac_panel protocol auto-detection.

Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).

Loads only the pybuspro package (no Home Assistant needed):

    python3 tests/test_dlp_ac.py
"""
import asyncio
import importlib
import logging
import os
import sys
import types

PKG = os.environ.get("AR_HDL_PKG", os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "custom_components", "ar_hdl_buspro")))

pkg = types.ModuleType("pb")
pkg.__path__ = [os.path.join(PKG, "pybuspro")]
sys.modules["pb"] = pkg
enums = importlib.import_module("pb.helpers.enums")
dlp_ac = importlib.import_module("pb.devices.dlp_ac")
panel_ac = importlib.import_module("pb.devices.panel_ac")
climate = importlib.import_module("pb.devices.climate")
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
        self.logger = logging.getLogger("test")
        self.network_interface = None

    def register_telegram_received_device_cb(self, cb, addr, postfix=None):
        self.cbs.append(cb)

    def unregister_telegram_received_device_cb(self, cb, addr, postfix=None):
        if cb in self.cbs:
            self.cbs.remove(cb)

    def feed(self, op, payload):
        for cb in list(self.cbs):
            cb(T(op, payload))


class T:
    def __init__(self, op, payload):
        self.operate_code = op
        self.payload = payload


# A classic DLP AC block: on, cool 24, mode cool (0) fan high (1), unlocked,
# room 27, heat 22, running 1, auto 25, dry 23, swing 0.
CLASSIC = [1, 24, 0x01, 0, 27, 22, 1, 25, 23, 0]
# A precise block: ac 0, on, cool 23, heat 21, auto 24, dry 22, mode heat (1),
# fan medium (2), fan enable 1, -, working..., room 26.4, decimals.
PRECISE = [0, 1, 23, 21, 24, 22, 1, 2, 1, 0, 1, 2, 1, 26, 4, 5, 0, 0, 0]


async def main():
    loop = asyncio.get_running_loop()
    fails = 0

    def check(name, cond):
        nonlocal fails
        print(("PASS " if cond else "FAIL ") + name)
        fails += 0 if cond else 1

    dlp_ac.DlpAirConditioner._READBACK_DELAY = 0

    # --- classic decode / encode -------------------------------------------
    st = dlp_ac.decode_classic(CLASSIC)
    check("classic decode power", st["power"] == 1)
    check("classic decode mode/fan nibbles", (st["mode"], st["fan"]) == (0, 1))
    check("classic decode room", st["room"] == 27.0)
    check("classic encode drops room/running",
          dlp_ac.encode_classic(st) == [1, 24, 0x01, 0, 22, 25, 23, 0])
    check("classic all-0xFF = no AC page", dlp_ac.decode_classic([255] * 10) is None)
    check("classic bad mode rejected", dlp_ac.decode_classic([1, 24, 0x91, 0, 27, 22, 1, 25, 23, 0]) is None)
    check("classic short rejected", dlp_ac.decode_classic([1, 24, 1]) is None)

    # --- precise decode / encode -------------------------------------------
    sp = dlp_ac.decode_precise(PRECISE)
    check("precise decode mode heat", sp["mode"] == 1)
    check("precise room with decimal", abs(sp["room"] - 26.4) < 1e-9)
    check("precise encode 14 bytes",
          dlp_ac.encode_precise(sp) == [0, 1, 23, 21, 24, 22, 1, 2, 1, 0, 5, 0, 0, 0])

    # --- classic device: commands only after a real block -------------------
    bus = FakeBus(loop)
    d = dlp_ac.DlpAirConditioner(bus, (1, 50), "dlp_classic", start=False)
    d._call_device_updated = lambda: None
    SENT.clear()
    await d.turn_on()
    await asyncio.sleep(0.01)
    check("no write before status", not any(op == OC.ControlDLPAC for op, _ in SENT))
    check("classic read is E0EC with no payload", (OC.ReadDLPAC, []) in SENT)

    bus.feed(OC.ReadDLPACResponse, CLASSIC)
    check("available after E0ED", d.available)
    check("protocol classic", d.protocol == "dlp_classic")
    check("mode cool, fan high", (d.hvac_mode, d.fan_speed) == ("cool", "high"))
    check("target = cool setpoint", d.target_temperature == 24)

    SENT.clear()
    await d.set_hvac_mode("heat")
    check("mode heat writes full block",
          SENT[0] == (OC.ControlDLPAC, [1, 24, 0x11, 0, 22, 25, 23, 0]))
    check("target follows heat", d.target_temperature == 22)
    SENT.clear()
    await d.set_target_temperature(21)
    check("heat setpoint written in heat slot",
          SENT[0] == (OC.ControlDLPAC, [1, 24, 0x11, 0, 21, 25, 23, 0]))
    SENT.clear()
    await d.set_fan_speed("low")
    check("fan low keeps mode", SENT[0][1][2] == 0x13)
    SENT.clear()
    await d.turn_off()
    check("off keeps everything else", SENT[0] == (OC.ControlDLPAC, [0, 24, 0x13, 0, 21, 25, 23, 0]))
    await asyncio.sleep(0.05)
    check("write is read back", (OC.ReadDLPAC, []) in SENT)

    # --- auto: precise answer wins over classic -----------------------------
    bus2 = FakeBus(loop)
    a = dlp_ac.DlpAirConditioner(bus2, (1, 51), "auto", start=False)
    a._call_device_updated = lambda: None
    bus2.feed(OC.ReadDLPACResponse, CLASSIC)
    check("auto takes classic first", a.protocol == "dlp_classic")
    bus2.feed(OC.ReadDLPPreciseACResponse, PRECISE)
    check("auto switches to precise", a.protocol == "dlp_precise")
    bus2.feed(OC.ReadDLPACResponse, CLASSIC)
    check("classic ignored once precise", a.hvac_mode == "heat")
    SENT.clear()
    await a.set_target_temperature(20)
    check("precise write 0x19B0", SENT[0][0] == OC.ControlDLPPreciseAC and SENT[0][1][3] == 20)

    # --- AutoPanelAc: Enviro panel stays Enviro -----------------------------
    panel_ac.PanelAirConditioner._STARTUP_RETRY_SECONDS = 3600
    climate.AirConditioner._READ_RETRY_SECONDS = 3600
    dlp_ac.AutoPanelAc._SETTLE_SECONDS = 0.05
    dlp_ac.AutoPanelAc._ENVIRO_GRACE_SECONDS = 0.0

    def make_auto(addr, with_enviro=True):
        b = FakeBus(loop)
        env = panel_ac.PanelAirConditioner(b, addr, 1, 1) if with_enviro else None
        if env:
            env._stopped = True
        dl = dlp_ac.DlpAirConditioner(b, addr, "auto", start=False)
        hv = climate.AirConditioner(b, addr, 1)
        hv._stopped = True
        return b, dlp_ac.AutoPanelAc(b, env, dl, hv), env, dl, hv

    b3, auto, env, dl, hv = make_auto((1, 60))
    b3.feed(OC.ReadPanelACResponse, [3, 1, 1])
    await asyncio.sleep(0.02)
    check("Enviro answer -> enviro", auto.detected == "enviro")
    check("DLP reader stopped", dl._stopped)
    check("Enviro modes stay cool/heat", auto.supported_modes == ["cool", "heat"])

    # --- AutoPanelAc: a DLP saved as ac_panel now works ---------------------
    b4, auto2, env2, dl2, hv2 = make_auto((1, 61))
    b4.feed(OC.ReadDLPACResponse, CLASSIC)
    await asyncio.sleep(0.02)
    check("visible before settling", auto2.available and auto2.is_on)
    await asyncio.sleep(0.15)
    check("DLP answer -> dlp_classic", auto2.detected == "dlp_classic")
    check("Enviro reader stopped", env2._stopped)
    check("DLP offers all five modes", len(auto2.supported_modes) == 5)
    SENT.clear()
    await auto2.set_hvac_mode("dry")
    check("command goes to DLP protocol", SENT[0][0] == OC.ControlDLPAC)

    # --- AutoPanelAc: nothing answered -> nothing sent ----------------------
    b5, auto3, *_ = make_auto((1, 62))
    SENT.clear()
    await auto3.turn_on()
    check("no command when nothing answered",
          not any(op in (OC.ControlDLPAC, OC.ControlPanelAC, OC.ControlACStatus) for op, _ in SENT))

    # --- opcode bytes -------------------------------------------------------
    check("opcodes", (OC.ReadDLPAC.value, OC.ControlDLPAC.value,
                      OC.ReadDLPPreciseAC.value, OC.ControlDLPPreciseAC.value)
          == (b"\xE0\xEC", b"\xE0\xEE", b"\x19\xAE", b"\x19\xB0"))

    await asyncio.sleep(0.05)
    print(f"\n{'ALL PASSED' if not fails else f'{fails} FAILED'}")
    return fails


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main()) else 0)
