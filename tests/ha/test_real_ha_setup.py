"""Real Home Assistant: does a dead gateway fail to load (retrying)?"""
import asyncio
import socket

import pytest
from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ar_hdl_buspro.pybuspro.core.telegram import Telegram
from custom_components.ar_hdl_buspro.pybuspro.helpers.enums import DeviceType
from custom_components.ar_hdl_buspro.pybuspro.helpers.telegram_helper import TelegramHelper

PORT = 16123
DEVICES = [{
    "name": "Kitchen", "device_type": "switch", "subnet_id": 1, "device_id": 20,
    "channel": 1,
}]


def make_entry(host):
    return MockConfigEntry(
        domain="ar_hdl_buspro", version=2,
        data={"gateway_host": host, "gateway_port": PORT},
        options={"devices": DEVICES}, title=f"GW {host}",
    )


class FakeGateway(asyncio.DatagramProtocol):
    """An HDL IP gateway on 127.0.0.2 with module 1.20 behind it."""
    def connection_made(self, transport):
        self.transport = transport
        self.frames = 0

    def datagram_received(self, data, addr):
        self.frames += 1
        t = Telegram()
        t.source_address = (1, 20)
        t.source_device_type = DeviceType.PyBusPro
        t.target_address = (250, 250)
        t.operate_code = b"\x00\x0f"
        t.payload = []
        self.transport.sendto(bytes(TelegramHelper(None).build_send_buffer(t)), addr)


async def start_fake(loop):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    s.bind(("127.0.0.2", PORT))
    transport, proto = await loop.create_datagram_endpoint(FakeGateway, sock=s)
    return transport, proto


async def test_dead_gateway_fails_to_load(hass):
    entry = make_entry("192.0.2.77")  # nothing there
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    print("dead gateway ->", entry.state, "|", entry.reason)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_live_gateway_loads_then_restart_while_dead_fails(hass):
    transport, proto = await start_fake(hass.loop)
    entry = make_entry("127.0.0.2")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    print("live gateway ->", entry.state, "frames seen by gateway:", proto.frames)
    assert entry.state is ConfigEntryState.LOADED
    gw = hass.data["ar_hdl_buspro"][entry.entry_id].gateway
    print("setup check:", gw.liveness.setup_check)

    transport.close()  # unplug the gateway
    await hass.config_entries.async_reload(entry.entry_id)  # = restart
    await hass.async_block_till_done()
    print("after unplug + reload ->", entry.state, "|", entry.reason)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_restart_keeps_failing_then_loads_when_plugged_back(hass):
    entry = make_entry("127.0.0.2")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    print("restart, gateway unplugged ->", entry.state)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    print("restart again, still unplugged ->", entry.state)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    transport, _ = await start_fake(hass.loop)  # plug it back in
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    print("gateway plugged back ->", entry.state)
    assert entry.state is ConfigEntryState.LOADED
    await hass.config_entries.async_unload(entry.entry_id)
    transport.close()


async def test_own_frame_reflected_is_not_an_answer(hass):
    # Gateway "at" 127.0.0.1: HA's own socket receives its own probe back.
    entry = make_entry("127.0.0.1")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    print("reflection only ->", entry.state)
    assert entry.state is ConfigEntryState.SETUP_RETRY
