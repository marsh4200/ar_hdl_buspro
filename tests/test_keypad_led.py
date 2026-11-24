"""Keypad LED sync: confirmed writes, retries and debouncing (no HA needed).

Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).

    python3 tests/test_keypad_led.py
"""
import asyncio
import importlib
import os
import sys
import types

PKG = os.environ.get("AR_HDL_PKG", os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "custom_components", "ar_hdl_buspro")))

# keypad_led imports `.pybuspro...`, so load it inside a stub package.
root = types.ModuleType("arhdl")
root.__path__ = [PKG]
sys.modules["arhdl"] = root
control = importlib.import_module("arhdl.pybuspro.devices.control")
enums = importlib.import_module("arhdl.pybuspro.helpers.enums")
keypad_led = importlib.import_module("arhdl.keypad_led")
keypad_led.LED_DEBOUNCE_SECONDS = 0.05
keypad_led.LED_ACK_TIMEOUT = 0.1
keypad_led.LED_REPEAT_SECONDS = 0.2
keypad_led.LED_FOLLOW_DELAYS = (0.1, 0.3)
OC = enums.OperateCode


class Tg:
    def __init__(self, op, payload, src):
        self.operate_code, self.payload, self.source_address = op, payload, src


class FakeKeypadBus:
    """Keypad that confirms LED writes; can drop the first N frames."""

    def __init__(self, loop, drop=0):
        self.loop = loop
        self.drop = drop
        self.virtual_handlers = []
        self.network_interface = self
        self.sent = []
        self.led = {}

    async def send_telegram(self, telegram):
        _, button, status = telegram.payload
        self.sent.append((tuple(telegram.target_address), button, status))
        if self.drop:
            self.drop -= 1
            return
        addr = tuple(telegram.target_address)
        self.led[(addr, button)] = status
        self.loop.call_later(0.02, self._confirm, addr, button, status)

    def _confirm(self, addr, button, status):
        for h in list(self.virtual_handlers):
            h(Tg(OC.ControlPanelACResponse, [17, button, status], addr))


def _learned(bus, *keypads):
    """Pretend these keypads have been seen confirming before."""
    keypad_led._router(bus).confirmers.update(keypads)


async def test_unknown_keypad_is_never_waited_on():
    bus = FakeKeypadBus(asyncio.get_running_loop(), drop=99)
    sync = keypad_led.KeypadLedSync(bus, "2.1:2")
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    await sync.push(True, force=True)
    assert loop.time() - t0 < 0.05, "must not wait for a confirmation"
    assert len(bus.sent) == 1
    await asyncio.sleep(0.3)
    assert len(bus.sent) == 2, "one repeat after settling, no more"


async def test_keypad_learned_from_its_confirmation():
    bus = FakeKeypadBus(asyncio.get_running_loop())
    sync = keypad_led.KeypadLedSync(bus, "2.1:2")
    await sync.push(True, force=True)
    await asyncio.sleep(0.05)
    assert (2, 1) in keypad_led._router(bus).confirmers


async def test_confirmed_write_sent_once():
    bus = FakeKeypadBus(asyncio.get_running_loop())
    _learned(bus, (2, 1), (2, 5))
    sync = keypad_led.KeypadLedSync(bus, "2.1:2, 2.5:4")
    await sync.push(True, force=True)
    await asyncio.sleep(0.3)
    assert len(bus.sent) == 2, bus.sent
    assert bus.led == {((2, 1), 2): 1, ((2, 5), 4): 1}


async def test_lost_frame_is_retried_for_confirming_keypad():
    bus = FakeKeypadBus(asyncio.get_running_loop(), drop=1)
    _learned(bus, (2, 1))
    sync = keypad_led.KeypadLedSync(bus, "2.1:2")
    await sync.push(True, force=True)
    assert len(bus.sent) == 2, bus.sent
    assert bus.led == {((2, 1), 2): 1}


async def test_rapid_clicks_end_on_final_state():
    bus = FakeKeypadBus(asyncio.get_running_loop())
    _learned(bus, (2, 1))
    sync = keypad_led.KeypadLedSync(bus, "2.1:2")
    state = {"on": False}
    for i in range(7):  # ON OFF ON OFF ON OFF ON
        state["on"] = i % 2 == 0
        sync.push_soon(lambda: state["on"])
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.4)
    assert bus.led == {((2, 1), 2): 1}
    assert len(bus.sent) == 1, f"debounce should send once, sent {bus.sent}"


async def test_newer_state_cancels_old_repeat():
    bus = FakeKeypadBus(asyncio.get_running_loop())
    sync = keypad_led.KeypadLedSync(bus, "9.9:1")  # never confirms
    bus.drop = 99
    await sync.push(True, force=True)
    await sync.push(False, force=True)
    await asyncio.sleep(0.3)
    assert [s for *_, s in bus.sent] == [1, 0, 0], bus.sent


async def test_relay_change_beats_keypad_overwrite():
    """Keypad sets its own (wrong) LED right after the relay reply - the
    follow-up write after settling must leave the LED matching the relay."""
    loop = asyncio.get_running_loop()
    bus = FakeKeypadBus(loop, drop=99)  # non-confirming keypad
    sync = keypad_led.KeypadLedSync(bus, "2.1:2")
    relay = {"on": True}
    sync.follow(lambda: relay["on"])          # startup: relay on
    await asyncio.sleep(0.4)
    bus.drop = 0
    relay["on"] = False                        # light goes off on the bus
    sync.follow(lambda: relay["on"])
    await asyncio.sleep(0.03)
    bus.led[((2, 1), 2)] = 1                   # keypad overwrites: LED stays on
    await asyncio.sleep(0.4)
    assert bus.led == {((2, 1), 2): 0}, bus.led


async def test_follow_recovers_lost_frame():
    loop = asyncio.get_running_loop()
    bus = FakeKeypadBus(loop, drop=1)
    sync = keypad_led.KeypadLedSync(bus, "2.1:2")
    sync.follow(lambda: True)
    await asyncio.sleep(0.4)
    assert bus.led == {((2, 1), 2): 1}, bus.led


async def test_follow_uses_final_state_after_flapping():
    loop = asyncio.get_running_loop()
    bus = FakeKeypadBus(loop)
    sync = keypad_led.KeypadLedSync(bus, "2.1:2")
    relay = {"on": False}
    for i in range(5):                         # ON OFF ON OFF ON
        relay["on"] = i % 2 == 0
        sync.follow(lambda: relay["on"])
        await asyncio.sleep(0.02)
    relay["on"] = False                        # ...and it ends OFF
    sync.follow(lambda: relay["on"])
    await asyncio.sleep(0.4)
    assert bus.led == {((2, 1), 2): 0}, bus.led
    assert all(s == 0 for *_, s in bus.sent), bus.sent


async def test_repeated_status_reads_do_not_spam():
    loop = asyncio.get_running_loop()
    bus = FakeKeypadBus(loop)
    sync = keypad_led.KeypadLedSync(bus, "2.1:2")
    sync.follow(lambda: True)
    await asyncio.sleep(0.4)
    sent = len(bus.sent)
    for _ in range(5):
        sync.follow(lambda: True)              # same state again
    await asyncio.sleep(0.4)
    assert len(bus.sent) == sent, bus.sent


async def test_ha_command_reply_does_not_double_send():
    loop = asyncio.get_running_loop()
    bus = FakeKeypadBus(loop)
    _learned(bus, (2, 1))
    sync = keypad_led.KeypadLedSync(bus, "2.1:2")
    sync.push_soon(lambda: True)
    sync.follow(lambda: True)                  # relay reply while pending
    await asyncio.sleep(0.15)
    sync.follow(lambda: True)                  # late duplicate reply
    await asyncio.sleep(0.4)
    assert len(bus.sent) == 1, bus.sent


async def main():
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            await fn()
            print("ok ", name)


asyncio.run(main())
