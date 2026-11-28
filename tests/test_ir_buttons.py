"""IR-module universal switches as named buttons (no Home Assistant needed).

Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).

    python3 tests/test_ir_buttons.py

Covers ir_library.py (taken from HDL Studio V1.9.65's FourChannelIRSender):
the 0x137D stored-device and 0xDB97 universal-switch-action replies decode,
junk is rejected, and actions are named after the device and key the way
Studio labels them. Then the scan: those replies from an IR module are
stored on the discovered device and the phase sends Studio's reads; and the
import: every configured ON and OFF action becomes its own button, an
unresponsive module still gets one button, a re-scan duplicates nothing,
and only button-mode entries leave the switch platform.
"""
import asyncio
import importlib
import os
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
import stub_ha  # noqa: E402

PKG_DIR = os.path.abspath(os.path.join(HERE, "..", "custom_components", "ar_hdl_buspro"))
PKG = stub_ha.install(PKG_DIR)
const = importlib.import_module(f"{PKG}.const")
ir = importlib.import_module(f"{PKG}.ir_library")
discovery = importlib.import_module(f"{PKG}.discovery")
cf = importlib.import_module(f"{PKG}.config_flow")
importlib.import_module(f"{PKG}.button")  # entity module loads

FAILS = []


def check(label, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + label + (f" -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(label)


def name20(text):
    b = text.encode("utf-8")
    return list(b) + [0] * (20 - len(b))


def device_reply(no, lib_type, name):
    # [no, 0, lib type, code idx hi, code idx lo, length, x, name(20)]
    return [no, 0, lib_type, 0, 7, 40, 0] + name20(name)


def uv_reply(no, status, action, target, key, channel=1):
    return [no, status, channel, action, target, key, 0, 0, 0, 0, 0]


TV, PROJECTOR = 4, 0   # library type byte = dbNewIRKeyDefine.DeviceID - 1


def test_library():
    check("device reply decodes", ir.parse_device_reply(device_reply(3, TV, "Lounge TV")) == (3, TV, "Lounge TV"))
    check("unwritten name (FF FF FF) gives no name",
          ir.parse_device_reply([2, 0, TV, 0, 0, 0, 0] + [0xFF] * 20) == (2, TV, None))
    check("device number out of range rejected", ir.parse_device_reply(device_reply(40, TV, "x")) is None)
    check("uv reply decodes", ir.parse_uv_reply(uv_reply(7, 1, 2, 3, 6))[:2] == (7, 1))
    check("uv bad status rejected", ir.parse_uv_reply([7, 5, 1, 2, 3, 6]) is None)
    for junk in ([], "x", [None], [300, 300, 300, 300, 300, 300, 300]):
        try:
            ir.parse_device_reply(junk); ir.parse_uv_reply(junk)
        except Exception as e:  # noqa: BLE001
            check(f"never raises on {junk!r}", False, str(e))
    devs = {3: (TV, "Lounge TV"), 4: (PROJECTOR, None)}
    check("TV key 6 is Power", ir.describe_action(uv_reply(1, 1, 2, 3, 6), devs) == "Lounge TV Power")
    check("TV key 5 is Volume+", ir.describe_action(uv_reply(1, 1, 2, 3, 5), devs) == "Lounge TV Volume+")
    check("unnamed device uses its library type",
          ir.describe_action(uv_reply(1, 1, 2, 4, 1), devs) == "Projector ON")
    check("unknown device/key still named", ir.describe_action(uv_reply(1, 1, 2, 9, 3), devs) == "IR device 9 key 3")
    check("learned code", ir.describe_action(uv_reply(1, 1, 2, 25, 4), devs) == "Learned code 4")
    check("AC action", ir.describe_action(uv_reply(1, 1, 1, 2, 1), devs) == "AC 2 on")
    check("unconfigured -> None", ir.describe_action(uv_reply(1, 1, 0, 0, 0), devs) is None)


class Tg:
    def __init__(self, op_hex, payload, src=(1, 40), code=0x0517):
        self.operate_code = None
        self.payload = payload
        self.source_address = src
        udp = [0] * 30
        udp[19], udp[20] = code >> 8, code & 0xFF
        udp[21], udp[22] = int(op_hex[2:4], 16), int(op_hex[4:6], 16)
        self.udp_data = udp
        self.source_device_type = None


class FakeIRModule:
    """Answers 0x137C / 0xDB96 like an HDL-MIRC04.40 with two devices."""

    def __init__(self, scanner, answers=True):
        self.scanner, self.answers, self.sent = scanner, answers, []
        self.network_interface = self
        self.allowed_source_ips = None

    async def send_telegram(self, t):
        op = bytes(t.operate_code)
        self.sent.append((op, list(t.payload)))
        if not self.answers:
            return
        if op == ir.READ_IR_DEVICE_OP:
            no = t.payload[0]
            if no == 1:
                self.scanner._on_telegram(Tg("0x137D", device_reply(1, TV, "Lounge TV")))
            elif no == 2:
                self.scanner._on_telegram(Tg("0x137D", device_reply(2, PROJECTOR, "Cinema")))
            else:
                self.scanner._on_telegram(Tg("0x137D", [no, 0, 0xFF, 0, 0, 0, 0] + [0] * 20))
        elif op == ir.READ_UV_ACTION_OP:
            no, st = t.payload
            table = {(1, 1): (2, 1, 6), (1, 0): (2, 1, 6), (2, 1): (2, 1, 5),
                     (3, 1): (2, 2, 1), (3, 0): (2, 2, 2)}
            a, tgt, key = table.get((no, st), (0, 0, 0))
            self.scanner._on_telegram(Tg("0xDB97", uv_reply(no, st, a, tgt, key)))


def _scan(answers=True):
    scanner = discovery.BusScanner.__new__(discovery.BusScanner)
    scanner._found = {}
    bus = FakeIRModule(scanner, answers)
    scanner._buspro = bus
    dev = discovery.DiscoveredDevice(subnet_id=1, device_id=40, type_code="0x0517",
                                     type_name="Unknown", remark="Lounge IR")
    scanner._found[(1, 40)] = dev
    old_gap, old_listen = discovery._FRAME_GAP, discovery._IR_LISTEN
    discovery._FRAME_GAP, discovery._IR_LISTEN = 0, 0.01
    try:
        asyncio.run(scanner._read_ir_modules())
    finally:
        discovery._FRAME_GAP, discovery._IR_LISTEN = old_gap, old_listen
    return dev, bus


def test_scan():
    dev, bus = _scan()
    check("scan stored named devices", dev.ir_devices.get(1) == (TV, "Lounge TV")
          and dev.ir_devices.get(2) == (PROJECTOR, "Cinema"), str(dev.ir_devices))
    check("scan read ON and OFF of every switch 1-99", len(dev.ir_actions) == 198, str(len(dev.ir_actions)))
    check("device read payload is Studio's [n, 0, 0]",
          (ir.READ_IR_DEVICE_OP, [5, 0, 0]) in bus.sent)
    check("uv read payload is [switch, status]",
          (ir.READ_UV_ACTION_OP, [3, 0]) in bus.sent)
    silent, sbus = _scan(answers=False)
    check("silent module: only the 24 device reads are sent",
          len(sbus.sent) == 24 and not silent.ir_actions, str(len(sbus.sent)))
    return dev, silent


class Disc:
    def __init__(self, src):
        for k in ("subnet_id", "device_id", "type_code", "remark", "ir_devices", "ir_actions"):
            setattr(self, k, getattr(src, k))
        self.address = f"{self.subnet_id}.{self.device_id}"


def test_import(dev, silent):
    flow = cf.ARHDLOptionsFlow.__new__(cf.ARHDLOptionsFlow)
    flow.hass = None
    devs = []
    n = flow._import_ir_module(Disc(dev), devs)
    names = [d[const.CONF_NAME] for d in devs]
    check("one button per configured action", n == 5, str(names))
    check("all are buttons", all(d[const.CONF_UV_SHOW_AS] == const.UV_SHOW_AS_BUTTON for d in devs))
    check("named after device and key", "Lounge IR Lounge TV Power" in names
          and "Lounge IR Lounge TV Volume+" in names and "Lounge IR Cinema ON" in names, str(names))
    check("OFF doing the same as ON is marked (off)", "Lounge IR Lounge TV Power (off)" in names, str(names))
    check("OFF with its own action keeps its own name", "Lounge IR Cinema OFF" in names, str(names))
    off = [d for d in devs if d[const.CONF_UV_BUTTON_STATUS] == const.UV_BUTTON_OFF]
    check("OFF buttons send OFF on the right switch",
          sorted(d[const.CONF_SUB_NUMBER] for d in off) == [1, 3])
    check("ir_action stored for the attribute",
          devs[0][const.CONF_IR_ACTION].endswith("(universal switch 1 ON)"), devs[0].get(const.CONF_IR_ACTION))
    check("re-scan adds nothing", flow._import_ir_module(Disc(dev), devs) == 0)
    check("device list tells ON and OFF apart",
          cf._device_summary(off[0]).endswith("@ 1.40.1 OFF]"), cf._device_summary(off[0]))
    fallback = []
    flow._import_ir_module(Disc(silent), fallback)
    check("unresponsive module still gets switch 1 as a button",
          len(fallback) == 1 and fallback[0][const.CONF_SUB_NUMBER] == 1
          and fallback[0][const.CONF_NAME] == "Lounge IR switch 1"
          and fallback[0][const.CONF_UV_SHOW_AS] == const.UV_SHOW_AS_BUTTON, str(fallback))


def test_platform_split():
    src = open(os.path.join(PKG_DIR, "switch.py")).read()
    check("switch platform skips button-mode entries",
          "device_cfg.get(CONF_UV_SHOW_AS) == UV_SHOW_AS_BUTTON" in src)
    check("button platform registered", "button" in const.PLATFORMS)
    flow_src = open(os.path.join(PKG_DIR, "config_flow.py")).read()
    body = flow_src[flow_src.index("def _universal_switch_schema("):]
    body = body[: body.index("\ndef ")]
    check("form offers Show as and Button sends",
          "CONF_UV_SHOW_AS," in body and "CONF_UV_BUTTON_STATUS," in body
          and "default=defaults.get(CONF_UV_SHOW_AS, UV_SHOW_AS_SWITCH)" in body)


if __name__ == "__main__":
    test_library()
    d, s = test_scan()
    test_import(d, s)
    test_platform_split()
    print("ALL PASSED" if not FAILS else f"{len(FAILS)} FAILED")
    sys.exit(1 if FAILS else 0)
