"""Regression tests for GitHub issues #25 and #17 (no Home Assistant needed).

Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).

    python3 tests/test_issue_17_25.py

#25 - HDL-MPTL4C Granite Display: the temperature entity must become
      available from the panel's 0xE3E8 reply, the humidity entity must poll
      0xE440 sub-type 0x45 and decode the 0xE441 reply, and the two entities
      on one address must not steal each other's readings. Scan import of a
      Granite Display (0x0890) creates both a temperature and a humidity
      sensor with hw kind "panel".
#17 - Universal switches imported from an IR module (HDL-MIRC04.40) are
      named by switch number, not "chN"; other device types keep "chN".
"""
import asyncio
import importlib
import os
import re
import struct
import sys
import types

PKG = os.environ.get("AR_HDL_PKG", os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "custom_components", "ar_hdl_buspro")))

pkg = types.ModuleType("pb")
pkg.__path__ = [os.path.join(PKG, "pybuspro")]
sys.modules["pb"] = pkg
enums = importlib.import_module("pb.helpers.enums")
sensor_mod = importlib.import_module("pb.devices.sensor")
OC = enums.OperateCode

ADDR = (1, 77)
GRANITE = 0x0890
FAILED = []


def _raw(op):
    return op.value if hasattr(op, "value") else bytes(op)


class Tg:
    def __init__(self, op, payload, src, code=GRANITE):
        self.operate_code, self.payload, self.source_address = op, payload, src
        self.source_device_type_code = code


class FakePanel:
    """A Granite Display: answers 0xE3E7 on 0xE3E8 and 0xE440/0x45 on 0xE441."""

    def __init__(self, loop, temp=26.99, rh=58):
        self.loop, self.temp, self.rh = loop, temp, rh
        self.cbs, self.sent = [], []
        self.network_interface = self
        self.logger = __import__("logging").getLogger("t")

    def register_telegram_received_device_cb(self, cb, addr, postfix=None):
        self.cbs.append((cb, tuple(addr)))

    def unregister_telegram_received_device_cb(self, cb, addr, postfix=None):
        pass

    async def send_telegram(self, telegram):
        op, payload = telegram.operate_code, list(telegram.payload or [])
        self.sent.append((op, payload))
        if op == OC.ReadTemperature:
            ch = payload[0] if payload else 1
            body = [ch, int(self.temp)] + list(struct.pack("<f", self.temp))
            self.loop.call_later(0.01, self.reply, OC.ReadTemperatureResponse, body)
        elif _raw(op) == b"\xe4\x40" and payload[:1] == [0x45]:
            self.loop.call_later(0.01, self.reply, OC.BroadcastLuminanceResponse,
                                 [0x45, 0, self.rh])

    def reply(self, op, payload):
        for cb, a in list(self.cbs):
            if a == ADDR:
                cb(Tg(op, payload, ADDR))


def check(name, cond, detail=""):
    print(("ok  " if cond else "FAIL") + f" {name}" + (f" -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


async def _panel_entities():
    loop = asyncio.get_running_loop()
    bus = FakePanel(loop)
    temp = sensor_mod.Sensor(bus, ADDR, device="panel", temperature_channel=1)
    hum = sensor_mod.Sensor(bus, ADDR, device="panel", temperature_channel=1,
                            read_humidity=True)
    await temp.read_sensor_status()
    await hum.read_sensor_status()
    await asyncio.sleep(0.1)
    return bus, temp, hum


def test_issue_25_panel_temperature_and_humidity():
    bus, temp, hum = asyncio.run(_panel_entities())
    check("#25 temperature reading arrives (entity available)",
          temp._current_temperature is not None)
    check("#25 temperature uses the precise float", temp.temperature == 26.99,
          str(temp.temperature))
    check("#25 humidity entity polls 0xE440 sub-type 0x45",
          any(_raw(op) == b"\xe4\x40" and p[:1] == [0x45] for op, p in bus.sent))
    check("#25 humidity decoded from 0xE441", hum._current_humidity == 58,
          str(hum._current_humidity))
    check("#25 humidity reply does not clobber illuminance",
          temp._brightness is None and hum._brightness is None)
    check("#25 both entities on one address keep their own reading",
          temp.temperature == 26.99 and hum.humidity == 58)


def test_issue_25_scan_import_creates_temp_and_humidity():
    src = open(os.path.join(PKG, "config_flow.py")).read()
    cat = open(os.path.join(PKG, "hdl_catalog.py")).read()
    codes = re.search(r"HUMIDITY_PANEL_CODES: Final = frozenset\(\{([^}]*)\}", cat).group(1)
    check("#25 0x0890 Granite Display listed as a humidity panel", "0x0890" in codes)
    check("#25 0x0890 imports with the panel hw kind",
          re.search(r'"0x0890": DEVICE_HW_PANEL', src) is not None)
    check("#25 panel import appends a humidity sensor",
          "hw_kind == DEVICE_HW_PANEL and panel_has_humidity(disc.type_code)" in src)


def test_issue_17_universal_switch_named_by_switch_number():
    # Lift _build_imported_device's naming rule out of config_flow without
    # importing Home Assistant: the branch must name universal switches by
    # switch number and keep "chN" for everything else.
    src = open(os.path.join(PKG, "config_flow.py")).read()
    body = src[src.index("def _build_imported_device("):]
    body = body[: body.index("base: dict[str, Any] = {")]
    check("#17 universal switch name uses 'switch N'",
          'name = f"{name} switch {channel}"' in body
          and "dtype == DEVICE_TYPE_UNIVERSAL_SWITCH" in body)
    check("#17 other device types keep 'chN'", 'name = f"{name} ch{channel}"' in body)
    check("#17 Add-climate form offers HVAC No.",
          "CONF_HVAC_NUMBER, default=defaults.get(CONF_HVAC_NUMBER, 1)" in src)


if __name__ == "__main__":
    test_issue_25_panel_temperature_and_humidity()
    test_issue_25_scan_import_creates_temp_and_humidity()
    test_issue_17_universal_switch_named_by_switch_number()
    print("ALL PASSED" if not FAILED else f"{len(FAILED)} FAILED")
    sys.exit(1 if FAILED else 0)
