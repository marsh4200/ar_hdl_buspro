"""Keypad LED sync under an "all off" from Home Assistant (no HA needed).

Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).

    python3 tests/test_keypad_led_burst.py

A gateway that drops any frame arriving within MIN_GAP of the one before -
what a busy HDL IP gateway feeding a 9600-baud bus does - and 20 lights on
three keypads switched off together. Before the shared LED writer every
entity fired its LED frames in the same instant and some were lost, leaving
LEDs lit over dark lights. Now every LED must end on its load's state, with
no more than one repeat per button, on keypads that confirm and keypads
that don't. Also: a button shared by two loads stays lit while either is on.
"""
import asyncio
import importlib
import os
import sys
import types

PKG = os.environ.get("AR_HDL_PKG", os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "custom_components", "ar_hdl_buspro")))
root = types.ModuleType("arhdl_burst")
root.__path__ = [PKG]
sys.modules["arhdl_burst"] = root
enums = importlib.import_module("arhdl_burst.pybuspro.helpers.enums")
keypad_led = importlib.import_module("arhdl_burst.keypad_led")
keypad_led.LED_DEBOUNCE_SECONDS = 0.05
keypad_led.LED_ACK_TIMEOUT = 0.15
keypad_led.LED_REPEAT_SECONDS = 0.2
keypad_led.LED_FOLLOW_DELAYS = (0.1, 0.3)
OC = enums.OperateCode
MIN_GAP = 0.03
FAILS = []


class Tg:
    def __init__(self, op, payload, src):
        self.operate_code, self.payload, self.source_address = op, payload, src


class BusyGateway:
    """Drops frames closer than MIN_GAP; keypads in `confirming` answer."""

    def __init__(self, loop, confirming=()):
        self.loop, self.confirming = loop, set(confirming)
        self.virtual_handlers = []
        self.network_interface = self
        self.sent, self.dropped, self.led = [], 0, {}
        self._last = None

    async def send_telegram(self, telegram):
        now = self.loop.time()
        addr = tuple(telegram.target_address)
        _, button, status = telegram.payload
        self.sent.append((addr, button, status))
        if self._last is not None and now - self._last < MIN_GAP:
            self._last = now
            self.dropped += 1
            return
        self._last = now
        self.led[(addr, button)] = status
        if addr in self.confirming:
            self.loop.call_later(0.01, self._confirm, addr, button, status)

    def _confirm(self, addr, button, status):
        for h in list(self.virtual_handlers):
            h(Tg(OC.ControlPanelACResponse, [17, button, status], addr))


def check(label, cond, detail=""):
    print(("ok  " if cond else "FAIL") + f" {label}" + (f" -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(label)


class Load:
    """A light entity: optimistic state + debounced LED push, like light.py."""

    def __init__(self, bus, links):
        self.on = True
        self.sync = keypad_led.KeypadLedSync(bus, links)

    def turn(self, on):
        self.on = on
        self.sync.push_soon(lambda: self.on)


def build(bus):
    loads = []
    for k, kp in enumerate(((2, 1), (2, 5), (1, 50))):
        for b in range(1, 8 if k < 2 else 7):
            loads.append(Load(bus, f"{kp[0]}.{kp[1]}:{b}"))
    return loads


async def run(confirming, label):
    loop = asyncio.get_running_loop()
    bus = BusyGateway(loop, confirming)
    loads = build(bus)
    for c in confirming:  # keypads seen confirming before
        keypad_led._router(bus).confirmers.add(c)
    check(f"{label}: 20 loads", len(loads) == 20)
    for load in loads:          # all on, settle
        load.turn(True)
    await asyncio.sleep(2.0)
    lit = [k for k, v in bus.led.items() if v == 1]
    check(f"{label}: all 20 LEDs lit after all on", len(lit) == 20, f"{len(lit)} lit")
    for load in loads:          # then all off in one go
        load.turn(False)
    await asyncio.sleep(3.0)
    wrong = sorted(k for k, v in bus.led.items() if v != 0)
    check(f"{label}: every LED ends off", len(bus.led) == 20 and not wrong,
          f"{len(wrong)} still lit, {20 - len(bus.led)} never reached")
    check(f"{label}: no frame dropped by the gateway", bus.dropped == 0, f"dropped {bus.dropped}")
    offs = [s for s in bus.sent if s[2] == 0]
    check(f"{label}: at most one repeat per button", len(offs) <= 40, f"{len(offs)} off frames")


async def test_burst_unconfirmed():
    await run((), "keypads that never confirm")


async def test_burst_confirmed():
    await run(((2, 1), (2, 5), (1, 50)), "keypads that confirm")


async def test_shared_button():
    loop = asyncio.get_running_loop()
    bus = BusyGateway(loop)
    a, b = Load(bus, "2.1:3"), Load(bus, "2.1:3")
    a.turn(True); b.turn(True)
    await asyncio.sleep(0.6)
    a.turn(False)
    await asyncio.sleep(0.6)
    check("shared button stays lit while the other load is on", bus.led[((2, 1), 3)] == 1, str(bus.led))
    b.turn(False)
    await asyncio.sleep(0.6)
    check("shared button goes off once both are off", bus.led[((2, 1), 3)] == 0, str(bus.led))


async def main():
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            await fn()
    print("ALL PASSED" if not FAILS else f"{len(FAILS)} FAILED")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
