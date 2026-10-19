"""Gateway liveness watchdog (no Home Assistant needed).

Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).

    python3 tests/test_gateway_liveness.py

UDP has no connection, so an open socket says nothing about the gateway. This
covers: a gateway that is already dead when Home Assistant starts goes
unavailable; one unplugged while running goes unavailable; a busy bus is never
probed; one lost probe reply does not grey out the site; the first frame from
the gateway brings it straight back; an unlicensed install is left alone; and
only frames from the gateway's own IP count as proof of life.
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
tg_mod = importlib.import_module("pb.core.telegram")
th_mod = importlib.import_module("pb.helpers.telegram_helper")
ni_mod = importlib.import_module("pb.transport.network_interface")
live_mod = importlib.import_module("pb.transport.liveness")
OC = enums.OperateCode
GW, OTHER = "192.168.88.10", "192.168.88.161"

L = live_mod.GatewayLiveness
L.TICK_SECONDS = 0.02
L.SILENCE_SECONDS = 0.4
L.REPLY_WINDOW_SECONDS = 0.1
L.STARTUP_GRACE_SECONDS = 0.1
L.OFFLINE_RETRY_SECONDS = 0.2


class FakeNI:
    def __init__(self, bus):
        self.bus = bus

    def _send_permitted(self):
        return self.bus.licensed

    async def send_telegram(self, telegram):
        self.bus.sent.append(telegram)
        if self.bus.gateway_up and self.bus.answer_probes:
            if self.bus.drop_next_replies:
                self.bus.drop_next_replies -= 1
                return
            reply = tg_mod.Telegram()
            reply.source_address = telegram.target_address
            self.bus.loop.call_later(0.01, self.bus.live.frame_received, reply)


class FakeBus:
    def __init__(self, loop):
        self.loop = loop
        self.logger = logging.getLogger("t")
        self.gateway_address_send_receive = ((GW, 6000), ("", 6000))
        self.licensed = True
        self.gateway_up = True
        self.answer_probes = True
        self.drop_next_replies = 0
        self.sent = []
        self._telegram_received_cbs = [
            {"device_address": (1, 20)}, {"device_address": (1, 21)},
        ]
        self.network_interface = FakeNI(self)
        self.changes = []
        self.live = L(self, self.changes.append)


def probes(bus):
    return [t for t in bus.sent if t.operate_code == b"\x00\x0e"]


async def test_dead_at_startup():
    bus = FakeBus(asyncio.get_running_loop())
    bus.gateway_up = False
    bus.live.start()
    await asyncio.sleep(0.6)
    assert bus.changes == [False], bus.changes
    assert not bus.live.reachable
    p = probes(bus)
    assert p and {tuple(t.target_address) for t in p} <= {(1, 20), (1, 21)}
    print("ok  gateway already dead at startup -> unavailable")

    bus.gateway_up = True
    await asyncio.sleep(0.4)  # next offline retry probe gets answered
    assert bus.changes == [False, True], bus.changes
    assert bus.live.reachable
    print("ok  comes back on its own once the gateway answers again")
    bus.live.stop()


async def test_unplugged_while_running():
    bus = FakeBus(asyncio.get_running_loop())
    bus.live.start()
    # Chatty bus: frames every 50 ms -> no probes at all.
    for _ in range(12):
        bus.live.frame_received(tg_mod.Telegram())
        await asyncio.sleep(0.05)
    assert probes(bus) == [] and bus.changes == []
    print("ok  busy bus is never probed")

    bus.gateway_up = False  # unplugged: silence from here on
    await asyncio.sleep(0.9)
    assert bus.changes == [False], bus.changes
    print("ok  unplugged while running -> unavailable")

    bus.live.frame_received(tg_mod.Telegram())  # any frame (keypad press)
    assert bus.changes == [False, True] and bus.live.reachable
    print("ok  first frame from the gateway restores it immediately")
    bus.live.stop()


async def test_one_lost_reply_is_tolerated():
    bus = FakeBus(asyncio.get_running_loop())
    bus.drop_next_replies = 2  # both targets of the first probe lose the reply
    bus.live.start()
    await asyncio.sleep(0.5)
    assert bus.changes == [], bus.changes
    assert bus.live.reachable
    assert len(probes(bus)) >= 4, "a miss is re-checked straight away"
    print("ok  one unanswered probe does not grey out the site")
    bus.live.stop()


async def test_unlicensed_left_alone():
    bus = FakeBus(asyncio.get_running_loop())
    bus.licensed = False
    bus.gateway_up = False
    bus.live.start()
    await asyncio.sleep(0.6)
    assert bus.changes == [] and probes(bus) == []
    print("ok  unlicensed install: no probes, state untouched")
    bus.live.stop()


def frame(src, target, op, payload):
    t = tg_mod.Telegram()
    t.source_address = src
    t.source_device_type = enums.DeviceType.PyBusPro
    t.target_address = target
    t.operate_code = op
    t.payload = payload
    return bytes(th_mod.TelegramHelper(None).build_send_buffer(t))


class NIBus:
    def __init__(self, loop, ip, devices):
        self.loop = loop
        self.logger = logging.getLogger("t")
        self.allowed_source_ips = {ip}
        self.dropped_source_ips = set()
        self.advertised_ip = "192.168.88.5"
        self._telegram_received_cbs = [{"device_address": d, "callback": None} for d in devices]
        self.network_interface = None
        self.liveness = None


async def test_only_gateway_ip_counts():
    """Two entries; frames are credited to the gateway that SENT them."""
    loop = asyncio.get_running_loop()
    bus_a = NIBus(loop, GW, [(1, 20)])
    bus_b = NIBus(loop, OTHER, [(2, 30)])
    seen_a, seen_b = [], []
    bus_a.liveness = live_a = L(bus_a, lambda r: None)
    bus_b.liveness = live_b = L(bus_b, lambda r: None)
    live_a.frame_received = lambda t: seen_a.append(t)
    live_b.frame_received = lambda t: seen_b.append(t)
    live_a.start(); live_b.start()
    ni_a = ni_mod.NetworkInterface(bus_a, ((GW, 6000), ("", 6000)))
    ni_b = ni_mod.NetworkInterface(bus_b, ((OTHER, 6000), ("", 6000)))
    ni_a.register_callback(lambda t: None)
    ni_b.register_callback(lambda t: None)

    f1 = frame((1, 20), (250, 250), OC.SingleChannelControlResponse, [1, 0xF8, 100])
    ni_a._udp_request_received(f1, (GW, 6000))
    assert len(seen_a) == 1 and not seen_b
    print("ok  frame from the gateway's own IP counts")

    # Linux SO_REUSEPORT delivered gateway A's unicast reply to entry B's socket.
    f2 = frame((1, 20), (250, 250), OC.SingleChannelControlResponse, [1, 0xF8, 0])
    ni_b._udp_request_received(f2, (GW, 6000))
    assert len(seen_a) == 2 and not seen_b, "credited to A, not B"
    print("ok  reply landing on the other entry's socket still credits its own gateway")

    ni_a._udp_request_received(b"garbage", (GW, 6000))
    assert len(seen_a) == 2, "invalid frames don't count"
    print("ok  invalid frames don't count")
    live_a.stop(); live_b.stop()


async def test_setup_check():
    loop = asyncio.get_running_loop()
    bus = FakeBus(loop)
    bus.gateway_up = False
    assert await bus.live.async_wait_for_answer([(1, 20)], 0.3) is False
    assert len(probes(bus)) == 2, "asked, then asked once more"
    print("ok  setup check fails when the gateway is down (entry -> retrying)")

    bus.gateway_up = True
    assert await bus.live.async_wait_for_answer([(1, 20)], 0.3) is True
    print("ok  setup check passes when the gateway answers")

    bus.gateway_up = True
    bus.drop_next_replies = 1
    assert await bus.live.async_wait_for_answer([(1, 20)], 0.4) is True
    print("ok  one lost reply at setup is retried, not a failure")

    bus.licensed = False
    bus.gateway_up = False
    assert await bus.live.async_wait_for_answer([(1, 20)], 0.3) is True
    print("ok  unlicensed install still loads (nothing is sent)")


async def main():
    await test_dead_at_startup()
    await test_unplugged_while_running()
    await test_one_lost_reply_is_tolerated()
    await test_unlicensed_left_alone()
    await test_only_gateway_ip_counts()
    await test_setup_check()
    print("\nALL PASSED")


asyncio.run(main())
