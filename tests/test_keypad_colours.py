"""Keypad LED colours + the categorized scan list (no HA needed).

Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).

    python3 tests/test_keypad_colours.py
"""
import asyncio
import importlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import stub_ha  # noqa: E402

PKG_DIR = os.environ.get("AR_HDL_PKG", os.path.abspath(os.path.join(
    HERE, "..", "custom_components", "ar_hdl_buspro")))
PKG = stub_ha.install(PKG_DIR)
kc = importlib.import_module(f"{PKG}.keypad_colours")
cf = importlib.import_module(f"{PKG}.config_flow")
const = importlib.import_module(f"{PKG}.const")
manager = importlib.import_module(f"{PKG}.manager")
kc.TIMING_WIRED = (0.1, 3)
kc.TIMING_WIRELESS = (0.1, 3)
kc.WRITE_GAP = 0

FAILS = []


def check(name, ok):
    print(("ok  " if ok else "FAIL") + " " + name)
    if not ok:
        FAILS.append(name)


class Tg:
    def __init__(self, op, payload, src):
        # Colour replies are not in the OperateCode enum: the parser leaves
        # operate_code None and only the datagram carries the code.
        self.operate_code = None
        self.udp_data = bytes(21) + bytes([op >> 8, op & 0xFF]) + bytes(4)
        self.payload, self.source_address = payload, src


class FakePanel:
    """A bus with one panel at 1.90 (per button) and one DLP at 1.60."""

    def __init__(self, loop, drop=0):
        self.loop, self.drop = loop, drop
        self.virtual_handlers = []
        self.network_interface = self
        self.sent = []
        self.leds = {b: ([255, 255, 255], [0, 0, 255]) for b in range(1, 5)}
        self.modes = {b: 2 for b in range(1, 5)}
        self.pages = {p: list(range(54)) for p in (1, 2)}

    async def send_telegram(self, t):
        op = (t.operate_code[0] << 8) | t.operate_code[1]
        addr, p = tuple(t.target_address), list(t.payload)
        self.sent.append((addr, op, p))
        if self.drop:
            self.drop -= 1
            return
        reply = None
        if addr == (1, 90):
            if op == 0xE14C and p[0] in self.leds:
                on, off = self.leds[p[0]]
                reply = (0xE14D, [p[0]] + on + off + [0])
            elif op == 0xE14E:
                self.leds[p[0]] = (p[1:4], p[4:7])
                reply = (0xE14F, [0xF8, p[0]])
            elif op == 0x354B and p[0] in self.leds:
                on, off = self.leds[p[0]]
                reply = (0x354C, [p[0], self.modes[p[0]]] + on + off)
            elif op == 0x3549:
                self.modes[p[0]] = p[1]
                self.leds[p[0]] = (p[2:5], p[5:8])
                reply = (0x354A, [0xF8, p[0]])
        elif addr == (1, 60):
            if op == 0x198C and p[0] in self.pages:
                reply = (0x198D, [p[0]] + self.pages[p[0]])
            elif op == 0x198E:
                self.pages[p[0]] = p[1:]
                reply = (0x198F, [0xF8, p[0]])
        if reply:
            self.loop.call_later(0.01, self._emit, reply, addr)

    def _emit(self, reply, addr):
        for h in list(self.virtual_handlers):
            h(Tg(reply[0], reply[1], addr))


class Disc:
    def __init__(self, code, dev, channels=None, ops=(), zones=None):
        self.subnet_id, self.device_id, self.type_code = 1, dev, code
        self.type_name, self.channel_count = "Unknown", channels
        self.op_codes, self.payloads, self.remark = set(ops), {}, None
        self.dry_contact_zones, self.self_functions = zones, None
        self.button_targets, self.dali_channel_types = {}, None
        self.dimmer_evidence, self.looks_like_keypad = False, False

    address = property(lambda s: f"{s.subnet_id}.{s.device_id}")
    key = property(lambda s: f"{s.subnet_id}-{s.device_id}")


async def main():
    loop = asyncio.get_running_loop()

    # --- which panels have a colour setting ---------------------------------
    check("0x13C3 wireless touch panel: per-button colours", kc.led_scheme_for("0x13C3") == "button")
    check("0x084D wall keypad: per-button colours", kc.led_scheme_for("0x084D") == "button")
    check("0x238C DLP: page colours", kc.led_scheme_for("0x238C") == "dlp")
    check("wireless DLP (5007): no colour setting", kc.led_scheme_for(5007) is None)
    check("0x08CA Granite keypad: no colour setting", kc.led_scheme_for("0x08CA") is None)
    check("relay 0x01C8: no colour setting", kc.led_scheme_for("0x01C8") is None)
    check("rgb-mode panel (2141)", kc.led_scheme_for(2141) == "rgbmode")
    check("junk type code is safe", kc.led_scheme_for("zz") is None and kc.led_scheme_for(None) is None)
    check("hex helpers", kc.hex_to_rgb("#FF8000") == [255, 128, 0] and kc.rgb_to_hex([255, 128, 0]) == "#ff8000"
          and kc.hex_to_rgb("junk") == [0, 0, 0] and kc.hex_to_rgb("#f80") == [255, 136, 0])

    # --- per-button read / write --------------------------------------------
    bus = FakePanel(loop)
    client = kc.KeypadColours(bus)
    got = await client.read((1, 90), "0x084D", 4)
    check("reads 4 buttons", got["scheme"] == "button" and len(got["colors"]) == 4)
    check("on/off decoded", got["colors"][0]["on"] == "#ffffff" and got["colors"][0]["off"] == "#0000ff")
    check("no reply handler left behind", bus.virtual_handlers == [])
    n = await client.save((1, 90), "0x084D", [{"button": 2, "on": "#ff0000", "off": "#001000"}])
    check("writes one button", n == 1 and bus.leds[2] == ([255, 0, 0], [0, 16, 0]))
    check("write frame is [button, on RGB, off RGB]",
          bus.sent[-1] == ((1, 90), 0xE14E, [2, 255, 0, 0, 0, 16, 0]))
    check("other buttons untouched", bus.leds[1] == ([255, 255, 255], [0, 0, 255]))

    # more buttons asked for than the panel has: stops, no error
    got = await client.read((1, 90), "0x084D", 8)
    check("fewer colour slots than buttons is fine", len(got["colors"]) == 4)

    # dropped frames are retried
    bus2 = FakePanel(loop, drop=2)
    got = await kc.KeypadColours(bus2).read((1, 90), "0x084D", 1)
    check("retries a dropped read", len(got["colors"]) == 1 and len(bus2.sent) == 3)

    # silent keypad -> clear error, nothing half-done
    try:
        await client.read((1, 91), "0x084D", 4)
        check("silent keypad raises", False)
    except kc.ColourError:
        check("silent keypad raises", True)
    try:
        await client.save((1, 91), "0x084D", [{"button": 1, "on": "#fff", "off": "#000"}])
        check("unconfirmed write raises", False)
    except kc.ColourError:
        check("unconfirmed write raises", True)

    # no colour setting -> never sends anything
    before = len(bus.sent)
    got = await client.read((1, 90), "0x08CA", 4)
    check("no-colour panel: nothing sent on read", got["scheme"] is None and len(bus.sent) == before)
    try:
        await client.save((1, 90), "0x08CA", [{"button": 1, "on": "#fff", "off": "#000"}])
        check("no-colour panel: save refused", False)
    except kc.ColourError:
        check("no-colour panel: save refused", len(bus.sent) == before)

    # --- rgb-mode panels keep their mode byte -------------------------------
    got = await client.read((1, 90), 2141, 4)
    check("rgbmode read keeps mode", got["scheme"] == "rgbmode" and got["colors"][0]["rgbmode"] == 2)
    entry = dict(got["colors"][0], on="#00ff00")
    await client.save((1, 90), 2141, [entry])
    check("rgbmode write = [button, mode, on, off]",
          bus.sent[-1][1] == 0x3549 and bus.sent[-1][2][:5] == [1, 2, 0, 255, 0])

    # --- DLP pages: only the edited slot changes ----------------------------
    try:
        await client.save((1, 60), "0x238C", [{"button": 1, "on": "#fff", "off": "#000"}])
        check("DLP save needs a read first", False)
    except kc.ColourError:
        check("DLP save needs a read first", True)
    got = await client.read((1, 60), "0x238C", 16)
    check("DLP reads 2 pages x 8 buttons", got["scheme"] == "dlp" and len(got["colors"]) == 16)
    check("DLP button 1 = slot 1 (slot 0 is the shortcut key)",
          got["colors"][0]["on"] == kc.rgb_to_hex([6, 7, 8]))
    before_page = list(bus.pages[2])
    await client.save((1, 60), "0x238C", [{"button": 10, "on": "#010203", "off": "#040506"}])
    after = bus.pages[2]
    check("DLP button 10 = page 2 slot 2", after[12:18] == [1, 2, 3, 4, 5, 6])
    check("rest of the page written back unchanged",
          after[:12] == before_page[:12] and after[18:] == before_page[18:] and len(after) == 54)
    check("page 1 not rewritten", not any(s[1] == 0x198E and s[2][0] == 1 for s in bus.sent))

    # --- the manager's keypad record ----------------------------------------
    rec = manager.keypad_record(Disc("0x084D", 90), 4)
    check("keypad record: '4 button touch panel' beats the catalogue's count of 1",
          rec["key"] == "1-90" and rec["scheme"] == "button"
          and manager.keypad_record(Disc("0x084D", 90), 1)["buttons"] == 4)
    check("0x13C2 = 2 buttons, 0x13C3 = 4 buttons",
          manager.button_hint("0x13C2", 2) == 2 and manager.button_hint("0x13C3", 4) == 4)
    check("unknown keypad starts at 8", manager.button_hint("0xFFF0", 0) == 8)
    rec = manager.keypad_record(Disc("0x238C", 60), 16)
    check("DLP record counts whole pages", rec["scheme"] == "dlp" and rec["buttons"] == 16)
    rec = manager.keypad_record(Disc("0x08CA", 91), 4)
    check("no-colour keypad is marked", rec["scheme"] is None)

    # --- categorized scan results -------------------------------------------
    class Entry:
        entry_id = "e1"
        options = {const.CONF_DEVICES: []}

    def flow_with(results):
        flow = cf.ARHDLOptionsFlow.__new__(cf.ARHDLOptionsFlow)
        flow.hass, flow._entry, flow._scan_results = None, Entry(), results
        flow._pending_scan_options, flow._climate_limit_queue = None, []
        flow.async_create_entry = lambda title="", data=None: data
        flow.async_show_form = lambda **kw: kw
        return flow

    dimmer = Disc("0x0260", 12, 6, {"ReadStatusOfChannelsResponse"})
    relay = Disc("0x01C8", 30, 4, {"ReadStatusOfChannelsResponse"})
    relay2 = Disc("0x01C8", 31, 4, {"ReadStatusOfChannelsResponse"})
    keypad = Disc("0x084D", 90)
    gateway = Disc("0x02F5", 254)
    drycon = Disc("0x0073", 80, zones=4)
    flow = flow_with([dimmer, relay, relay2, drycon, keypad, gateway])
    cat = lambda d: flow._scan_category(d, flow._infer_device_type(d, set()))  # noqa: E731
    check("dimmer -> Dimmers", cat(dimmer) == cf.SCAN_CAT_DIMMERS)
    check("relay -> Relays", cat(relay) == cf.SCAN_CAT_RELAYS)
    check("keypad -> Keypads", cat(keypad) == cf.SCAN_CAT_KEYPADS)
    check("gateway -> Gateways", cat(gateway) == cf.SCAN_CAT_GATEWAYS)
    check("dry contact -> inputs", cat(drycon) == cf.SCAN_CAT_INPUTS)

    # ticking inside a section imports only those lines
    saved = await flow_with([dimmer, relay, relay2]).async_step_scan_results({
        cf.SCAN_CAT_RELAYS: {const.CONF_DISCOVERED: ["1-30"], cf.CONF_SELECT_ALL: False},
        cf.SCAN_CAT_DIMMERS: {const.CONF_DISCOVERED: [], cf.CONF_SELECT_ALL: False},
        const.CONF_SPLIT_CHANNELS: True, const.CONF_DIMMER_CODES: "",
    })
    devs = saved[const.CONF_DEVICES]
    check("one relay ticked -> its 4 channels only",
          len(devs) == 4 and {d[const.CONF_DEVICE_ID] for d in devs} == {30})
    # "select everything" on a group takes the whole group, and only it
    saved = await flow_with([dimmer, relay, relay2]).async_step_scan_results({
        cf.SCAN_CAT_RELAYS: {const.CONF_DISCOVERED: [], cf.CONF_SELECT_ALL: True},
        const.CONF_SPLIT_CHANNELS: True, const.CONF_DIMMER_CODES: "",
    })
    devs = saved[const.CONF_DEVICES]
    check("select all relays -> both relays, no dimmer",
          len(devs) == 8 and {d[const.CONF_DEVICE_ID] for d in devs} == {30, 31}
          and all(d[const.CONF_DEVICE_TYPE] == "switch" for d in devs))
    saved = await flow_with([dimmer, relay]).async_step_scan_results({
        const.CONF_SPLIT_CHANNELS: True, const.CONF_DIMMER_CODES: "",
    })
    check("nothing ticked imports nothing", saved[const.CONF_DEVICES] == [])

    print("ALL PASSED" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
