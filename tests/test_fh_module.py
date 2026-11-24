"""HDL floor heating module (0x1C5E/0x1C5C), HDL-MFH06.432 family.

Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).

Loads only the pybuspro package (no Home Assistant needed):

    python3 tests/test_fh_module.py
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
fhm = importlib.import_module("pb.devices.floor_heating_module")
control = importlib.import_module("pb.devices.control")
OC = enums.OperateCode

SENT = []


async def _fake_send(self):
    SENT.append((self.operate_code, list(self.payload)))


control._GenericControl.send = _fake_send


class T:
    def __init__(self, op, payload):
        self.operate_code = op
        self.payload = payload


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


# Channel 2: on, heat, Celsius, mode day, setpoints 21/24/18/12,
# flag 1, current 23, flag 0, output 40 %.
REPLY_CH2 = [2, 0x01, 0, 2, 21, 24, 18, 12, 1, 23, 0, 40, 0, 0]


async def main():
    loop = asyncio.get_running_loop()
    fails = 0

    def check(name, cond):
        nonlocal fails
        print(("PASS " if cond else "FAIL ") + name)
        fails += 0 if cond else 1

    fhm.FloorHeatingModule._READBACK_DELAY = 0
    fhm.FloorHeatingModule._WAIT_FOR_STATUS = 0.2

    check("opcodes", (OC.ReadFloorHeatingModule.value, OC.ControlFloorHeatingModule.value)
          == (b"\x1c\x5e", b"\x1c\x5c"))
    check("decode wrong channel ignored", fhm.decode_status(REPLY_CH2, 3) is None)
    check("decode block", fhm.decode_status(REPLY_CH2, 2)[:7] == [1, 0, 2, 21, 24, 18, 12])
    check("all 0xFF = not fitted", fhm.decode_status([1] + [255] * 13, 1) is None)
    check("signed temp negative", fhm.signed_temp(0x85) == -5)
    check("signed temp 0x80 = 0", fhm.signed_temp(0x80) == 0)
    check("encode = Studio's 10 bytes",
          fhm.encode_control(2, [1, 0, 2, 21, 24, 18, 12, 9, 9])
          == [2, 1, 0, 2, 21, 24, 18, 12, 0, 0])

    bus = FakeBus(loop)
    d = fhm.FloorHeatingModule(bus, (1, 30), 2, start=False)
    d._call_device_updated = lambda: None

    # No answer: nothing is written.
    SENT.clear()
    ok = await d.control(on=False)
    check("no write without status", not ok and all(
        op == OC.ReadFloorHeatingModule for op, _ in SENT))
    check("read asks with the channel", (OC.ReadFloorHeatingModule, [2]) in SENT)

    bus.feed(OC.ReadFloorHeatingModuleResponse, [1, 0x00, 0, 1, 5, 5, 5, 5, 0, 9, 0, 0, 0, 0])
    check("other channel's reply ignored", not d.available)
    bus.feed(OC.ReadFloorHeatingModuleResponse, REPLY_CH2)
    check("state decoded", d.available and d.is_on and d.work_type == "heat")
    check("target follows mode (day)", d.target_temperature == 24)
    check("current temp", d.current_temperature == 23)
    check("output percent", d.output_percent == 40)

    SENT.clear()
    await d.control(setpoint=26)
    await asyncio.sleep(0.01)
    check("setpoint written to active mode slot",
          (OC.ControlFloorHeatingModule, [2, 1, 0, 2, 21, 26, 18, 12, 0, 0]) in SENT)
    check("read back after write", (OC.ReadFloorHeatingModule, [2]) in SENT)

    SENT.clear()
    await d.control(on=False)
    check("off keeps work type nibble",
          (OC.ControlFloorHeatingModule, [2, 0x00, 0, 2, 21, 24, 18, 12, 0, 0]) in SENT)
    SENT.clear()
    await d.control(on=True, work_type=fhm.WORK_COOL)
    check("cool on = 0x11",
          (OC.ControlFloorHeatingModule, [2, 0x11, 0, 2, 21, 24, 18, 12, 0, 0]) in SENT)
    SENT.clear()
    await d.control(mode=fhm.MODE_AWAY, setpoint=10)
    check("mode change then setpoint lands in away slot",
          (OC.ControlFloorHeatingModule, [2, 0x01, 0, 4, 21, 24, 18, 10, 0, 0]) in SENT)

    # The write's own reply updates the state too.
    bus.feed(OC.ControlFloorHeatingModuleResponse, [2, 0x00, 1, 1, 70, 72, 64, 50, 0, 0x81, 0, 0, 0, 0])
    check("control reply decoded, F unit, negative temp",
          (not d.is_on) and d.fahrenheit and d.current_temperature == -1 and d.target_temperature == 70)
    d.stop()

    print("\n%d failure(s)" % fails)
    return fails


def _run() -> int:
    return asyncio.run(main()) + scan_import_checks()


def scan_import_checks() -> int:
    """Scan side, on the Home Assistant stub: a module imports as 6 channels."""
    sys.path.insert(0, os.path.dirname(__file__))
    import stub_ha  # noqa: E402

    name = stub_ha.install(PKG)
    const = importlib.import_module(f"{name}.const")
    cf = importlib.import_module(f"{name}.config_flow")
    importlib.import_module(f"{name}.climate")  # entity module loads
    fails = 0

    def check(label, cond):
        nonlocal fails
        print(("PASS " if cond else "FAIL ") + label)
        fails += 0 if cond else 1

    class Disc:
        def __init__(self, code, dev_id):
            self.type_code = code
            self.type_name = "Unknown"
            self.subnet_id, self.device_id = 1, dev_id
            self.remark = "Floor"
            self.address = f"1.{dev_id}"
            self.key = f"1-{dev_id}"
            self.op_codes = set()
            self.channel_count = None
            self.self_functions = [8, 0, 6]
            self.dimmer_evidence = False
            self.looks_like_keypad = False
            self.dali_channel_types = None
            self.payloads = {}

    flow = cf.ARHDLOptionsFlow.__new__(cf.ARHDLOptionsFlow)
    flow.hass = None
    for code in ("0x00D3", "0x00D2", "0x27DF"):
        check(f"{code} scans as climate",
              flow._infer_device_type(Disc(code, 40), set()) == const.DEVICE_TYPE_CLIMATE)
    devs = []
    check("module imports 6 channels", flow._import_fh_module(Disc("0x00D3", 40), devs) == 6)
    check("kind + channel set",
          [(d[const.CONF_CLIMATE_KIND], d[const.CONF_HVAC_NUMBER]) for d in devs]
          == [(const.CLIMATE_KIND_FH_MODULE, ch) for ch in range(1, 7)])
    check("re-scan adds nothing", flow._import_fh_module(Disc("0x00D3", 40), devs) == 0)
    check("summary shows channel", cf._device_summary(devs[2]).endswith("1.40.3]"))
    return fails


if __name__ == "__main__":
    sys.exit(1 if _run() else 0)
