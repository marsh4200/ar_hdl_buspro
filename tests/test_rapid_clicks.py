"""Rapid-click behaviour of relay/dimmer channels (no Home Assistant needed).

Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).

    python3 tests/test_rapid_clicks.py

Simulates a module that answers every SingleChannelControl in order, but
with bus latency, while the user clicks faster than the replies come back.
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
control = importlib.import_module("pb.devices.control")
switch_mod = importlib.import_module("pb.devices.switch")
light_mod = importlib.import_module("pb.devices.light")
OC = enums.OperateCode


class Tg:
    def __init__(self, op, payload, src):
        self.operate_code, self.payload, self.source_address = op, payload, src


class FakeBus:
    """Records sends; replies to each channel command after `latency`."""

    def __init__(self, loop, latency, drop_first=False):
        self.loop = loop
        self.latency = latency
        self.drop_first = drop_first
        self.cbs = []
        self.sent = []
        self.network_interface = self
        self.logger = __import__("logging").getLogger("t")

    def register_telegram_received_device_cb(self, cb, addr, postfix=None):
        self.cbs.append((cb, addr))

    async def send_telegram(self, telegram):
        self.sent.append((telegram.operate_code, list(telegram.payload)))
        if telegram.operate_code == OC.SingleChannelControl:
            if self.drop_first:
                self.drop_first = False
                return
            ch, lvl = telegram.payload[0], telegram.payload[1]
            addr = telegram.target_address
            self.loop.call_later(self.latency, self._reply, addr,
                                 OC.SingleChannelControlResponse, [ch, 0xF8, lvl])

    def _reply(self, addr, op, payload):
        for cb, a in self.cbs:
            if tuple(a) == tuple(addr):
                cb(Tg(op, payload, addr))


async def _quiet_reads(*_a, **_k):
    return None


def _make(cls, bus, *args):
    # Don't run the startup read loop in tests.
    orig = cls._call_read_current_status_of_channels
    cls._call_read_current_status_of_channels = lambda self, run_from_init=False: None
    dev = cls(bus, (1, 10), *args)
    cls._call_read_current_status_of_channels = orig
    dev._got_initial_status = True
    return dev


async def test_stale_replies_do_not_flip_state():
    loop = asyncio.get_running_loop()
    bus = FakeBus(loop, latency=0.5)
    sw = _make(switch_mod.Switch, bus, 3)
    history = []

    async def _upd(d):
        history.append(d.is_on)
    sw.register_device_updated_cb(_upd)

    # ON, OFF, ON, OFF, ON - 100 ms apart, much faster than the 0.5 s reply.
    for i in range(5):
        await (sw.set_on() if i % 2 == 0 else sw.set_off())
        await asyncio.sleep(0.1)
    await asyncio.sleep(1.0)

    assert sw.is_on is True, "final state must be the last click"
    # Before the fix the stale replies replayed ON/OFF/ON/OFF/ON in HA.
    assert all(history), f"state flipped back to an old click: {history}"
    # No resends: every click got its reply within the ack timeout.
    n_cmds = sum(1 for op, _ in bus.sent if op == OC.SingleChannelControl)
    assert n_cmds == 5, f"expected 5 commands, sent {n_cmds}"


async def test_slow_bus_does_not_double_traffic():
    loop = asyncio.get_running_loop()
    bus = FakeBus(loop, latency=1.0)  # slower than the old 0.8 s timeout
    sw = _make(switch_mod.Switch, bus, 1)
    await sw.set_on()
    await asyncio.sleep(2.0)
    n_cmds = sum(1 for op, _ in bus.sent if op == OC.SingleChannelControl)
    assert n_cmds == 1, f"a 1 s reply must not trigger a resend (sent {n_cmds})"
    assert sw.is_on


async def test_lost_frame_is_still_resent():
    loop = asyncio.get_running_loop()
    bus = FakeBus(loop, latency=0.1, drop_first=True)
    sw = _make(switch_mod.Switch, bus, 2)
    await sw.set_on()
    await asyncio.sleep(2.0)
    n_cmds = sum(1 for op, _ in bus.sent if op == OC.SingleChannelControl)
    assert n_cmds == 2, f"lost command should be resent once (sent {n_cmds})"
    assert sw.is_on


async def test_relay_reporting_255_counts_as_on():
    loop = asyncio.get_running_loop()
    bus = FakeBus(loop, latency=0.1)
    lt = _make(light_mod.Light, bus, 4)
    await lt.set_on()
    bus._reply((1, 10), OC.SingleChannelControlResponse, [4, 0xF8, 255])
    await asyncio.sleep(0.3)
    assert lt.is_on


async def test_external_change_is_applied():
    loop = asyncio.get_running_loop()
    bus = FakeBus(loop, latency=0.1)
    lt = _make(light_mod.Light, bus, 5)
    await lt.set_brightness(60)
    await asyncio.sleep(0.3)
    # Someone dims it from a keypad: not a level we asked for -> apply.
    bus._reply((1, 10), OC.SingleChannelControlResponse, [5, 0xF8, 20])
    assert lt.current_brightness == 20


async def main():
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            await fn()
            print("ok ", name)


asyncio.run(main())
