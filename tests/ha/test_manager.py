"""Real Home Assistant: the AR HDL Manager (keypad colours) and the
categorized scan-results screen."""
import asyncio
import socket
import types

import voluptuous_serialize
from homeassistant.helpers import config_validation as cv
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ar_hdl_buspro import keypad_colours, manager
from custom_components.ar_hdl_buspro.pybuspro.core.telegram import Telegram
from custom_components.ar_hdl_buspro.pybuspro.helpers.telegram_helper import TelegramHelper

PORT = 16127
HOST = "127.0.0.5"
RELAY = ((1, 20), b"\x01\xc8")     # 4ch relay
KEYPAD = ((1, 90), b"\x08\x4d")    # wall keypad with a colour setting
GRANITE = ((1, 91), b"\x08\xca")   # keypad without one


class FakeBus(asyncio.DatagramProtocol):
    def connection_made(self, transport):
        self.transport = transport
        self.leds = {b: ([255, 255, 255], [0, 0, 255]) for b in range(1, 5)}
        self.writes = []

    def send(self, addr, dev, op, payload):
        t = Telegram()
        t.source_address = dev[0]
        t.source_device_type = types.SimpleNamespace(value=dev[1])
        t.target_address = (250, 250)
        t.operate_code = bytes([op >> 8, op & 0xFF])
        t.payload = list(payload)
        self.transport.sendto(bytes(TelegramHelper(None).build_send_buffer(t)), addr)

    def datagram_received(self, data, addr):
        op = (data[21] << 8) | data[22]
        target = (data[23], data[24])
        p = list(data[25:-2])
        for dev in (RELAY, KEYPAD, GRANITE):
            if target not in (dev[0], (255, 255)):
                continue
            if op == 0x000E:
                self.send(addr, dev, 0x000F, [0] * 20)
            elif op == 0x0033 and dev is RELAY:
                self.send(addr, dev, 0x0034, [4, 0, 0, 0, 0])
            elif dev is KEYPAD and op == 0xE14C and p and p[0] in self.leds:
                on, off = self.leds[p[0]]
                self.send(addr, dev, 0xE14D, [p[0]] + on + off + [0])
            elif dev is KEYPAD and op == 0xE14E:
                self.leds[p[0]] = (p[1:4], p[4:7])
                self.writes.append(p)
                self.send(addr, dev, 0xE14F, [0xF8, p[0]])


class Token:
    def matches(self, *_):
        return True


async def setup(hass):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    s.bind((HOST, PORT))
    transport, bus = await hass.loop.create_datagram_endpoint(FakeBus, sock=s)
    assert await async_setup_component(hass, "frontend", {})
    entry = MockConfigEntry(
        domain="ar_hdl_buspro", version=2,
        data={"gateway_host": HOST, "gateway_port": PORT},
        options={"devices": [{"name": "Kitchen", "device_type": "switch",
                              "subnet_id": 1, "device_id": 20, "channel": 1}]},
        title="Test hub",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, bus, transport


async def test_manager_end_to_end(hass, hass_ws_client, monkeypatch):
    monkeypatch.setattr(keypad_colours, "TIMING_WIRED", (0.3, 2))
    entry, bus, transport = await setup(hass)
    try:
        # sidebar page registered, admin only
        panel = hass.data["frontend_panels"][manager.PANEL_URL_PATH]
        assert panel.require_admin and panel.sidebar_title == "AR HDL Manager"
        assert (manager.FRONTEND_DIR / manager.FRONTEND_FILE).is_file()

        ws = await hass_ws_client(hass)
        n = 0

        async def call(cmd, **data):
            nonlocal n
            n += 1
            await ws.send_json({"id": n, "type": f"ar_hdl_buspro/manager/{cmd}",
                                **data})
            return await ws.receive_json()

        eid = entry.entry_id
        r = await call("hubs")
        assert r["success"] and r["result"]["hubs"][0]["title"] == "Test hub"

        # unlicensed install: nothing goes to the bus, and it says why
        monkeypatch.setattr(manager, "get_unlock", lambda hass: None)
        r = await call("scan", entry_id=eid, duration=3)
        assert not r["success"] and "licence" in r["error"]["message"].lower()

        gateway = hass.data["ar_hdl_buspro"][eid].gateway
        gateway.hdl.unlock_provider = lambda: Token()
        monkeypatch.setattr(manager, "get_unlock", lambda hass: Token())

        r = await call("scan", entry_id=eid, duration=3)
        assert r["success"], r
        found = {k["address"]: k for k in r["result"]["found"]}
        print("scan found:", {a: (k["scheme"], k["buttons"]) for a, k in found.items()})
        assert found["1.90"]["scheme"] == "button" and not found["1.90"]["added"]
        assert "1.20" not in found                      # relays are left out
        assert found["1.91"]["scheme"] is None          # listed, not addable

        r = await call("add", entry_id=eid, keys=["1-90", "1-91", "9-9"])
        assert r["result"]["added"] == 1
        assert [k["key"] for k in r["result"]["keypads"]] == ["1-90"]
        # nothing was added to the integration's own devices
        assert len(entry.options["devices"]) == 1

        r = await call("read", entry_id=eid, key="1-90")
        assert r["success"], r
        colors = r["result"]["colors"]
        assert len(colors) == 4 and colors[0]["on"] == "#ffffff" and colors[0]["off"] == "#0000ff"

        r = await call("save", entry_id=eid, key="1-90",
                       colors=[dict(colors[2], on="#ff8000", off="#000000")])
        assert r["success"] and r["result"]["written"] == 1
        assert bus.writes == [[3, 255, 128, 0, 0, 0, 0]]
        r = await call("read", entry_id=eid, key="1-90")
        assert r["result"]["colors"][2]["on"] == "#ff8000"

        r = await call("rename", entry_id=eid, key="1-90", name="Lounge",
                       button_names={"1": "Main"})
        assert r["result"]["keypad"]["name"] == "Lounge"
        r = await call("save", entry_id=eid, key="1-91", colors=[])
        assert not r["success"]                         # not in the manager

        # saved list survives a reload of the hub
        await hass.config_entries.async_reload(eid)
        await hass.async_block_till_done()
        hass.data[manager.DATA_MANAGER].clear()         # as after a restart
        r = await call("keypads", entry_id=eid)
        kp = r["result"]["keypads"]
        assert [k["key"] for k in kp] == ["1-90"] and kp[0]["name"] == "Lounge"
        assert kp[0]["button_names"] == {"1": "Main"}

        r = await call("remove", entry_id=eid, key="1-90")
        assert r["result"]["keypads"] == []
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        transport.close()


async def test_scan_results_are_grouped(hass):
    entry, bus, transport = await setup(hass)
    try:
        from custom_components.ar_hdl_buspro import config_flow as cf
        from custom_components.ar_hdl_buspro.discovery import DiscoveredDevice

        init = await hass.config_entries.options.async_init(entry.entry_id)
        flow = hass.config_entries.options._progress[init["flow_id"]]
        flow._scan_results = [
            DiscoveredDevice(1, 12, "0x0260", "Unknown", 6, {"ReadStatusOfChannelsResponse"}),
            DiscoveredDevice(1, 30, "0x01C8", "Unknown", 4, {"ReadStatusOfChannelsResponse"}),
            DiscoveredDevice(1, 31, "0x01C8", "Unknown", 4, {"ReadStatusOfChannelsResponse"}),
            DiscoveredDevice(1, 90, "0x084D", "Unknown"),
            DiscoveredDevice(1, 254, "0x02F5", "Unknown"),
        ]
        form = await flow.async_step_scan_results()
        assert form["type"] == "form"
        # exactly what the frontend is sent
        shown = voluptuous_serialize.convert(
            form["data_schema"], custom_serializer=cv.custom_serializer)
        sections = {f["name"]: f for f in shown if f.get("type") == "expandable"}
        print("sections:", {k: (v["expanded"], len(v["schema"][1]["selector"]["select"]["options"]))
                            for k, v in sections.items()})
        # 0x084D is a keypad with a temperature sensor: its own line sits
        # under Keypads, its temperature line under Sensors.
        assert list(sections) == [cf.SCAN_CAT_DIMMERS, cf.SCAN_CAT_RELAYS,
                                  cf.SCAN_CAT_SENSORS, cf.SCAN_CAT_KEYPADS,
                                  cf.SCAN_CAT_GATEWAYS]
        assert sections[cf.SCAN_CAT_RELAYS]["expanded"]
        assert not sections[cf.SCAN_CAT_KEYPADS]["expanded"]
        relays = sections[cf.SCAN_CAT_RELAYS]["schema"][1]["selector"]["select"]["options"]
        assert [o["value"] for o in relays] == ["1-30", "1-31"]

        # what the frontend sends back, through the real schema
        data = form["data_schema"]({
            cf.SCAN_CAT_DIMMERS: {"discovered": ["1-12"]},
            cf.SCAN_CAT_RELAYS: {"select_all": True},
            cf.SCAN_CAT_SENSORS: {},
            cf.SCAN_CAT_KEYPADS: {},
            cf.SCAN_CAT_GATEWAYS: {},
        })
        done = await flow.async_step_scan_results(data)
        assert done["type"] == "create_entry"
        new = [d for d in done["data"]["devices"] if d["device_id"] != 20]
        kinds = sorted((d["device_id"], d["device_type"]) for d in new)
        assert len(new) == 14 and {k for k in kinds} == {(12, "light"), (30, "switch"), (31, "switch")}

        # every section has its translations
        import json, pathlib
        base = pathlib.Path(cf.__file__).parent
        for name in ("strings.json", "translations/en.json", "translations/no.json"):
            tr = json.loads((base / name).read_text())["options"]["step"]["scan_results"]["sections"]
            assert set(tr) == set(cf.SCAN_CATEGORIES), name
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        transport.close()
