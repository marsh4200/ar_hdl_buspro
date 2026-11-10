"""Curtain modules: real bus position on HDL motors, time-based elsewhere.

Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).

Runs without Home Assistant (tests/stub_ha.py stands in for it):

    python3 tests/test_curtain_position.py
"""
import asyncio
import enum
import importlib
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import stub_ha  # noqa: E402

PKG_DIR = os.environ.get("AR_HDL_PKG", os.path.abspath(os.path.join(
    HERE, "..", "custom_components", "ar_hdl_buspro")))
PKG = stub_ha.install(PKG_DIR)

# Cover bits the generic stub doesn't model.
cover_stub = types.ModuleType("homeassistant.components.cover")


class CoverEntityFeature(enum.IntFlag):
    OPEN = 1
    CLOSE = 2
    SET_POSITION = 4
    STOP = 8


class CoverDeviceClass(str, enum.Enum):
    CURTAIN = "curtain"


cover_stub.CoverEntityFeature = CoverEntityFeature
cover_stub.CoverDeviceClass = CoverDeviceClass
cover_stub.CoverEntity = stub_ha.Entity
sys.modules["homeassistant.components.cover"] = cover_stub

cv = importlib.import_module(f"{PKG}.cover")
OC = importlib.import_module(f"{PKG}.pybuspro.helpers.enums").OperateCode

cv._PCT_PROBE_WAIT = 0.2
cv._PCT_POLL_INTERVAL = 0.05


class Tg:
    def __init__(self, op, payload, type_code=0x02C4):
        self.operate_code, self.payload = op, payload
        self.udp_data = bytes(19) + bytes([type_code >> 8, type_code & 0xFF]) + bytes(10)


class Motor:
    """kind: 'pct2' / 'pct3' (0x25E8) report position; 'mw02' has none.

    `noisy` mimics a motor that also sends [1, 2] (close) status frames while
    it moves -- the frame that used to snap the old estimate to 0.
    """

    def __init__(self, kind, pos=30, speed=10, push=True, noisy=False):
        self.kind, self.pos, self.target = kind, pos, pos
        self.speed, self.push, self.noisy = speed, push, noisy
        self.type_code = 0x25E8 if kind == "pct3" else 0x02C4
        self.sent, self.cb, self.task = [], None, None

    async def run(self):
        while self.pos != self.target:
            await asyncio.sleep(0.02)
            step = min(self.speed, abs(self.target - self.pos))
            self.pos += step if self.target > self.pos else -step
            if self.noisy:
                self.reply(OC.CurtainSwitchControlResponse, [1, 2])
            if self.push:
                self.reply(OC.CurtainSwitchControlResponse, [17, self.pos])

    def reply(self, op, p):
        asyncio.get_running_loop().call_soon(self.cb, Tg(op, p, self.type_code))

    def go(self, t):
        self.target = t
        if not self.task or self.task.done():
            self.task = asyncio.get_running_loop().create_task(self.run())

    async def handle(self, op, p):
        self.sent.append((op, list(p)))
        pct = self.kind in ("pct2", "pct3")
        if op == OC.ReadStatusOfCurtainSwitch:
            if p[0] == 17 and pct:
                self.reply(OC.ReadStatusOfCurtainSwitchResponse, [17, self.pos])
            elif p[0] == 1:
                self.reply(OC.ReadStatusOfCurtainSwitchResponse, [1, 0])
        elif op == OC.CurtainSwitchControl:
            if p[0] == 17 and pct:
                self.go(p[1])
            elif p[0] == 1:
                self.reply(OC.CurtainSwitchControlResponse, [1, p[1]])
                if pct:
                    if p[1] == 1:
                        self.go(100)
                    elif p[1] == 2:
                        self.go(0)
                    else:
                        self.target = self.pos


def make(motor, travel=2, timer=False):
    e = cv.ARHDLCurtainModuleCover.__new__(cv.ARHDLCurtainModuleCover)

    def base_init(self, entry, gateway, device_cfg):
        self._subnet, self._device_id = 1, 50
        self._dev = types.SimpleNamespace(
            register_telegram_received_cb=lambda cb: setattr(motor, "cb", cb))

    orig = cv._ARHDLCoverBase.__init__
    cv._ARHDLCoverBase.__init__ = base_init
    try:
        cv.ARHDLCurtainModuleCover.__init__(
            e, types.SimpleNamespace(entry_id="x"), None,
            {"curtain_number": 1, "travel_time": travel, "subnet_id": 1, "device_id": 50,
             "use_travel_timer": timer})
    finally:
        cv._ARHDLCoverBase.__init__ = orig
    loop = asyncio.get_running_loop()
    e.hass = types.SimpleNamespace(async_create_task=loop.create_task)
    e.async_write_ha_state = lambda: None
    e.schedule_update_ha_state = lambda: None
    e._send = motor.handle
    return e


async def start(e, m):
    e._dev.register_telegram_received_cb(e._telegram_received)
    await e._send(OC.ReadStatusOfCurtainSwitch, [1])
    if not e._use_timer:
        e._probe_task = e.hass.async_create_task(e._probe_native())
        await e._probe_task


async def settle(e, t=3.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + t
    while loop.time() < end:
        await asyncio.sleep(0.05)
        if e._moving_dir == 0 and not (e._poll_task and not e._poll_task.done()):
            break


FAILED = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        FAILED.append(name)


async def main():
    # 1. 2-byte percentage motor that pushes its position
    m = Motor("pct2", pos=30)
    e = make(m)
    await start(e, m)
    check("pct2 detected native", e._native and e._pct_form == 2)
    check("pct2 real position at startup", e.current_cover_position == 30)
    check("pct2 not assumed state", e.assumed_state is False)
    await e.async_set_cover_position(position=70)
    check("set 70 sends [17,70]", (OC.CurtainSwitchControl, [17, 70]) in m.sent)
    await settle(e)
    check("reaches 70 from motor reports", e.current_cover_position == 70)
    check("no timed stop sent", (OC.CurtainSwitchControl, [1, 0]) not in m.sent)
    await e.async_close_cover()
    await settle(e)
    check("close -> 0 and closed", e.current_cover_position == 0 and e.is_closed)
    m.go(40)
    await asyncio.sleep(0.01)
    await settle(e)
    check("keypad move followed to 40", e.current_cover_position == 40)
    e._handle_pct_report(221)
    check("221 -> track not_measured", e.extra_state_attributes == {"track": "not_measured"})
    e._handle_pct_report(55)
    check("position clears track attr", e.extra_state_attributes is None)
    await e.async_will_remove_from_hass()

    # 2. The reported bug: motor sends [1, 2] frames while moving. Position
    #    must never jump back to 0 -- it only follows the [17, pct] frames.
    m = Motor("pct2", pos=0, noisy=True)
    e = make(m)
    await start(e, m)
    seen = []
    orig = e._handle_pct_report

    def spy(v):
        orig(v)
        seen.append(e.current_cover_position)

    e._handle_pct_report = spy
    await e.async_set_cover_position(position=60)
    await settle(e)
    check("no snap back to 0 mid-travel",
          seen and all(b >= a for a, b in zip(seen, seen[1:])) and 0 not in seen[1:])
    check("lands on 60", e.current_cover_position == 60)
    await e.async_will_remove_from_hass()

    # 3. 0x25E8 motor: 3-byte percentage form, picked from its type code
    m = Motor("pct3", pos=0, push=False)
    e = make(m)
    await start(e, m)
    check("0x25E8 uses 3-byte form", e._native and e._pct_form == 3)
    await e.async_set_cover_position(position=50)
    check("0x25E8 set sends [17,50,0]", (OC.CurtainSwitchControl, [17, 50, 0]) in m.sent)
    await settle(e, 5)
    check("0x25E8 reaches 50 by polling", e.current_cover_position == 50)
    await e.async_will_remove_from_hass()

    # 4. MW02 controller, timer OFF: HDL only -- no slider, no timer, no stop
    m = Motor("mw02")
    e = make(m)
    await start(e, m)
    SP = cv.CoverEntityFeature.SET_POSITION
    check("mw02 HDL mode: no position slider",
          not e._native and not (e.supported_features & SP)
          and e.current_cover_position is None)
    await e.async_open_cover()
    await asyncio.sleep(0.3)
    check("mw02 HDL mode: open sends [1,1], no timed stop",
          (OC.CurtainSwitchControl, [1, 1]) in m.sent
          and (OC.CurtainSwitchControl, [1, 0]) not in m.sent)
    check("mw02 HDL mode: open reported -> not closed", e.is_closed is False)
    await e.async_will_remove_from_hass()

    # 5. Motor that reports position, timer ON: timer wins, no probe
    m = Motor("pct2", pos=0, noisy=True, push=True)
    e = make(m, timer=True)
    await start(e, m)
    check("timer on: no position probe",
          not e._native and not any(p[0] == 17 for _o, p in m.sent))
    seen = []
    await e.async_set_cover_position(position=50)
    for _ in range(12):
        await asyncio.sleep(0.1)
        seen.append(e.current_cover_position)
    check("timer on: [1,2] frames no longer snap to 0 mid-travel",
          all(b >= a for a, b in zip(seen, seen[1:])))
    check("timer on: lands on 50 with timed stop",
          e.current_cover_position == 50 and (OC.CurtainSwitchControl, [1, 0]) in m.sent)
    await e.async_will_remove_from_hass()

    # 6. MW02 controller, timer ON: unchanged time-based behaviour
    m = Motor("mw02")
    e = make(m, timer=True)
    await start(e, m)
    check("mw02 timer: time-based", not e._native and e.assumed_state is True)
    await e.async_set_cover_position(position=50)
    check("mw02 drives with open action", (OC.CurtainSwitchControl, [1, 1]) in m.sent)
    await asyncio.sleep(1.3)
    check("mw02 timed stop sent",
          (OC.CurtainSwitchControl, [1, 0]) in m.sent and e.current_cover_position == 50)
    await e.async_will_remove_from_hass()


asyncio.run(main())
print(f"\n{'FAILED: ' + ', '.join(FAILED) if FAILED else 'all passed'}")
sys.exit(1 if FAILED else 0)
