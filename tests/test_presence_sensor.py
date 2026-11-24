"""HDL presence sensors (no Home Assistant needed).

Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).

    python3 tests/test_presence_sensor.py

HDL Studio V1.9.65 lists device types 0x0187 / 0x018F / 0x05E4 as
HDL7in1DeviceTypeHumanSensor (HDL-MSMW24BP family) and reads them like a
7-in-1: ReadSensorsInOneStatus (0x1604) -> 0x1605 with presence in
payload[7]. Covers: the raw sender type code survives frame decoding even for
codes the DeviceType enum doesn't know; an entity created as "generic" (polls
0x1645, never answered) probes 0x1604 a few times, learns sensors-in-one from
the reply and then polls only 0x1604; presence in payload[7] drives the
entity; a device that answers nothing is probed at most 5 times; the scan
import maps the presence codes to sensors-in-one.
"""
import asyncio
import importlib
import os
import sys
import types

PKG = os.environ.get("AR_HDL_PKG", os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "custom_components", "ar_hdl_buspro")))

pkg = types.ModuleType("pb")
pkg.__path__ = [os.path.join(PKG, "pybuspro")]
sys.modules["pb"] = pkg
enums = importlib.import_module("pb.helpers.enums")
sensor_mod = importlib.import_module("pb.devices.sensor")
helper_mod = importlib.import_module("pb.helpers.telegram_helper")
telegram_mod = importlib.import_module("pb.core.telegram")
OC = enums.OperateCode

ADDR = (1, 40)


class Tg:
    def __init__(self, op, payload, src, code=None):
        self.operate_code, self.payload, self.source_address = op, payload, src
        self.source_device_type_code = code


class FakeBus:
    """One sensor; answers 0x1604 like an HDL presence sensor (or not at all)."""

    def __init__(self, loop, type_code=0x05E4, answers=True):
        self.loop = loop
        self.type_code = type_code
        self.answers = answers
        self.present = 1
        self.cbs = []
        self.sent = []
        self.network_interface = self
        self.logger = __import__("logging").getLogger("t")

    def register_telegram_received_device_cb(self, cb, addr, postfix=None):
        self.cbs.append((cb, tuple(addr)))

    async def send_telegram(self, telegram):
        self.sent.append(telegram.operate_code)
        if self.answers and telegram.operate_code == OC.ReadSensorsInOneStatus:
            # 0x1605: [ok, temp+20, lux hi, lux lo, humidity(0xFF none), x, x,
            #          presence, dry1, dry2]
            payload = [0xF8, 45, 0x01, 0x2C, 0xFF, 0, 0, self.present, 0, 0]
            self.loop.call_later(0.01, self.reply, OC.ReadSensorsInOneStatusResponse, payload)

    def reply(self, op, payload):
        for cb, a in list(self.cbs):
            if a == ADDR:
                cb(Tg(op, payload, ADDR, self.type_code))

    def count(self, op):
        return sum(1 for o in self.sent if o == op)


def test_raw_type_code_survives_decode():
    """A type code DeviceType doesn't list must still reach the device."""
    helper = helper_mod.TelegramHelper()
    t = telegram_mod.Telegram()
    t.source_address = ADDR
    t.target_address = (1, 254)
    t.operate_code = OC.ReadSensorsInOneStatusResponse
    t.payload = [0xF8, 45, 1, 44, 0xFF, 0, 0, 1, 0, 0]
    t.source_device_type = None
    buf = bytearray(helper.build_send_buffer(t))
    # Patch the sender type field to 0x05E4 and fix the CRC, as the sensor would send.
    start = buf.index(b"\xaa\xaa") + 2
    buf[start + 3], buf[start + 4] = 0x05, 0xE4
    length = buf[start]
    crc = helper._crc16(bytes(buf[start:start + length - 2]))
    buf[start + length - 2], buf[start + length - 1] = crc >> 8, crc & 0xFF
    got = helper.build_telegram_from_udp_data(bytes(buf), ("192.168.1.10", 6000))
    assert got is not None, "frame failed to decode"
    assert got.source_device_type_code == 0x05E4, got.source_device_type_code


async def test_generic_entity_learns_presence_sensor():
    bus = FakeBus(asyncio.get_running_loop())
    s = sensor_mod.Sensor(bus, ADDR, device=None)  # created before model known
    await s.read_sensor_status()
    await asyncio.sleep(0.05)
    assert bus.count(OC.ReadSensorStatus) >= 1  # generic 0x1645 still sent
    assert bus.count(OC.ReadSensorsInOneStatus) >= 1, "must probe 0x1604"
    assert s._device == "sensors_in_one", s._device
    assert s.is_presence_sensor
    assert s.movement is True, "presence byte payload[7]=1 must read occupied"
    assert s.temperature == 25

    # From now on it polls 0x1604 only.
    generic_before = bus.count(OC.ReadSensorStatus)
    bus.present = 0
    await s.read_sensor_status()
    await asyncio.sleep(0.05)
    assert bus.count(OC.ReadSensorStatus) == generic_before
    assert s.movement is False, "presence byte 0 must read clear"


async def test_silent_device_is_probed_at_most_five_times():
    bus = FakeBus(asyncio.get_running_loop(), answers=False)
    s = sensor_mod.Sensor(bus, ADDR, device=None)
    for _ in range(12):
        await s.read_sensor_status()
    await asyncio.sleep(0.02)
    assert bus.count(OC.ReadSensorsInOneStatus) <= 5, bus.count(OC.ReadSensorsInOneStatus)


async def test_ordinary_7in1_is_not_flagged_presence():
    bus = FakeBus(asyncio.get_running_loop(), type_code=0x0148)  # MSP07M.4C
    s = sensor_mod.Sensor(bus, ADDR, device="sensors_in_one")
    await s.read_sensor_status()
    await asyncio.sleep(0.05)
    assert s.movement is True
    assert not s.is_presence_sensor


def test_const_and_sensor_lists_match():
    src = open(os.path.join(PKG, "const.py"), encoding="utf-8").read()
    for code in sensor_mod.PRESENCE_TYPE_CODES:
        assert f'"0x{code:04X}"' in src.split("HDL_PRESENCE_TYPE_CODES")[1].split("\n")[0], code


async def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        if asyncio.iscoroutinefunction(t):
            await t()
        else:
            t()
        print("ok", t.__name__)


if __name__ == "__main__":
    asyncio.run(main())
