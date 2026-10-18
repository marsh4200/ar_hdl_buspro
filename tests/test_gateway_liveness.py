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
    def __init__(self, loop):
        self.loop = loop
        self.logger = logging.getLogger("t")
        self.allowed_source_ips = {GW}
        self.dropped_source_ips = set()
        self.advertised_ip = "192.168.88.5"
        self._telegram_received_cbs = [{"device_address": (1, 20), "callback": None}]
        self.heard = []
        self.on_gateway_frame = self.heard.append


async def test_only_gateway_ip_counts():
    bus = NIBus(asyncio.get_running_loop())
    ni = ni_mod.NetworkInterface(bus, ((GW, 6000), ("", 6000)))
    ni.register_callback(lambda t: None)
    f1 = frame((1, 20), (250, 250), OC.SingleChannelControlResponse, [1, 0xF8, 100])
    ni._udp_request_received(f1, (GW, 6000))
    assert len(bus.heard) == 1
    f2 = frame((1, 20), (255, 255), OC.SingleChannelControlResponse, [1, 0xF8, 0])
    ni._udp_request_received(f2, (OTHER, 6000))
    assert len(bus.heard) == 1, "relay via another HDL IP device is not proof of life"
    ni._udp_request_received(b"garbage", (GW, 6000))
    assert len(bus.heard) == 1, "invalid frames don't count"
    print("ok  only valid frames from the gateway's own IP count")


async def main():
    await test_dead_at_startup()
    await test_unplugged_while_running()
    await test_one_lost_reply_is_tolerated()
    await test_unlicensed_left_alone()
    await test_only_gateway_ip_counts()
    print("\nALL PASSED")


asyncio.run(main())
