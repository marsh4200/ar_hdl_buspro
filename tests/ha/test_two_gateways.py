"""Two gateway entries in one HA: one live (chatty bus), one unplugged."""
import asyncio
import socket

from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ar_hdl_buspro.pybuspro.core.telegram import Telegram
from custom_components.ar_hdl_buspro.pybuspro.helpers.enums import DeviceType, OperateCode
from custom_components.ar_hdl_buspro.pybuspro.helpers.telegram_helper import TelegramHelper

PORT = 16124


def entry(host, sub):
    return MockConfigEntry(
        domain="ar_hdl_buspro", version=2,
        data={"gateway_host": host, "gateway_port": PORT},
        options={"devices": [{"name": f"L{sub}", "device_type": "switch",
                              "subnet_id": sub, "device_id": 20, "channel": 1}]},
        title=f"GW {host}",
    )


def frame(src):
    t = Telegram()
    t.source_address = src
    t.source_device_type = DeviceType.PyBusPro
    t.target_address = (255, 255)
    t.operate_code = OperateCode.SingleChannelControlResponse
    t.payload = [1, 0xF8, 100]
    return bytes(TelegramHelper(None).build_send_buffer(t))


async def chatty_live_gateway(stop):
    """Live gateway on 127.0.0.2 broadcasting bus traffic every 200 ms."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    s.bind(("127.0.0.2", PORT))
    s.setblocking(False)
    while not stop.is_set():
        s.sendto(frame((1, 20)), ("127.0.0.1", PORT))
        try:
            data, addr = s.recvfrom(2048)
            s.sendto(frame((1, 20)), addr)  # answer anything asked
        except BlockingIOError:
            pass
        await asyncio.sleep(0.2)
    s.close()


async def check(hass, dead_host, dead_first=False):
    stop = asyncio.Event()
    task = hass.loop.create_task(chatty_live_gateway(stop))
    live, dead = entry("127.0.0.2", 1), entry(dead_host, 2)
    for e in ((dead, live) if dead_first else (live, dead)):
        e.add_to_hass(hass)
    # Like an HA restart: setting up the domain sets up every entry at once.
    await hass.config_entries.async_setup(live.entry_id)
    await hass.async_block_till_done()
    order = "dead added first" if dead_first else "live added first"
    print(f"\n  [{order}] live 127.0.0.2 -> {live.state.value}")
    print(f"  [{order}] dead {dead_host} -> {dead.state.value} | {dead.reason}")
    lgw = hass.data["ar_hdl_buspro"][live.entry_id].gateway
    print(f"  [{order}] live setup check: {lgw.liveness.setup_check['result']}")
    result = (live.state, dead.state)
    stop.set()
    await task
    for e in (live, dead):
        if e.state is ConfigEntryState.LOADED:
            await hass.config_entries.async_unload(e.entry_id)
    return result


async def test_restart_live_first(hass):
    live, dead = await check(hass, "192.0.2.77")
    assert live is ConfigEntryState.LOADED and dead is ConfigEntryState.SETUP_RETRY


async def test_restart_dead_first(hass):
    live, dead = await check(hass, "192.0.2.77", dead_first=True)
    assert live is ConfigEntryState.LOADED and dead is ConfigEntryState.SETUP_RETRY


async def test_restart_dead_by_hostname(hass):
    live, dead = await check(hass, "hdl-gateway-2.invalid")
    assert live is ConfigEntryState.LOADED and dead is ConfigEntryState.SETUP_RETRY
