"""Telegrams relayed by a second HDL IP device (no Home Assistant needed).

Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).

    python3 tests/test_second_gateway.py

A site's wireless devices can reach the network through their own HDL IP
gateway. Their replies and button presses must not be thrown away, while
traffic for devices that are not configured here still is, and a frame that
arrives through both gateways is handled once.
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
OC = enums.OperateCode
GW, OTHER = "192.168.88.10", "192.168.88.161"


class FakeBuspro:
    def __init__(self, loop):
        self.loop = loop
        self.logger = logging.getLogger("t")
        self.allowed_source_ips = {GW}
        self.dropped_source_ips = set()
        self.advertised_ip = "192.168.88.5"
        self._telegram_received_cbs = [{"device_address": (1, 20), "callback": None}]


def frame(src, target, op, payload):
    t = tg_mod.Telegram()
    t.source_address = src
    t.source_device_type = enums.DeviceType.PyBusPro
    t.target_address = target
    t.operate_code = op
    t.payload = payload
    return bytes(th_mod.TelegramHelper(None).build_send_buffer(t))


async def main():
    loop = asyncio.get_running_loop()
    bus = FakeBuspro(loop)
    ni = ni_mod.NetworkInterface(bus, ((GW, 6000), ("", 6000)))
    got = []
    ni.register_callback(got.append)

    reply_on = frame((1, 20), (250, 250), OC.SingleChannelControlResponse, [1, 0xF8, 100])
    ni._udp_request_received(reply_on, (GW, 6000))
    assert len(got) == 1, "gateway frame accepted"
    print("ok  frame from the configured gateway")

    ni._udp_request_received(reply_on, (OTHER, 6000))
    assert len(got) == 1, "same frame via second gateway must be dropped as a copy"
    print("ok  duplicate via second gateway handled once")

    reply_off = frame((1, 20), (255, 255), OC.SingleChannelControlResponse, [1, 0xF8, 0])
    ni._udp_request_received(reply_off, (OTHER, 6000))
    assert len(got) == 2 and got[-1].payload[2] == 0, "configured device via .161 accepted"
    print("ok  configured device relayed via second gateway accepted")

    to_ha = frame((3, 7), (250, 250), OC.UniversalSwitchControl, [5, 255])
    ni._udp_request_received(to_ha, (OTHER, 6000))
    assert len(got) == 3, "telegram addressed to Home Assistant accepted"
    print("ok  telegram addressed to Home Assistant accepted")

    phantom = frame((9, 99), (255, 255), OC.SingleChannelControlResponse, [1, 0xF8, 100])
    ni._udp_request_received(phantom, (OTHER, 6000))
    assert len(got) == 3, "unknown device from foreign IP must be dropped"
    assert OTHER in bus.dropped_source_ips
    print("ok  unknown device from another IP still dropped")

    ni._udp_request_received(reply_on, (GW, 6000))
    ni._udp_request_received(reply_on, (GW, 6000))
    assert len(got) == 5, "real repeats from the same IP are kept"
    print("ok  repeats from the same gateway kept")
    print("\nALL PASSED")


asyncio.run(main())
