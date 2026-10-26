"""RGB channel groups, DALI-2 colour channels, the DALI-2 scan read and the
DLP AC scan import - plus checks that plain dimmers are untouched.

Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).

Runs without Home Assistant (tests/stub_ha.py stands in for it):

    python3 tests/test_rgb_dali.py
"""
import asyncio
import importlib
import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import stub_ha  # noqa: E402

PKG_DIR = os.environ.get("AR_HDL_PKG", os.path.abspath(os.path.join(
    HERE, "..", "custom_components", "ar_hdl_buspro")))
PKG = stub_ha.install(PKG_DIR)

const = importlib.import_module(f"{PKG}.const")
colour = importlib.import_module(f"{PKG}.colour")
light_mod = importlib.import_module(f"{PKG}.light")
cf = importlib.import_module(f"{PKG}.config_flow")
discovery = importlib.import_module(f"{PKG}.discovery")
OC = importlib.import_module(f"{PKG}.pybuspro.helpers.enums").OperateCode


class Tel:
    def __init__(self, op, payload, src):
        self.operate_code, self.payload, self.source_address = op, payload, src


class FakeBus:
    """Records sends; a channel command is answered with its level."""

    def __init__(self, loop):
        self.loop = loop
        self.cbs = []
        self.sent = []
        self.network_interface = self
        self.logger = logging.getLogger("t")

    def register_telegram_received_device_cb(self, cb, addr, postfix=None):
        self.cbs.append((cb, tuple(addr)))

    def unregister_telegram_received_device_cb(self, cb, addr, postfix=None):
        self.cbs = [(c, a) for c, a in self.cbs if c is not cb]

    async def send_telegram(self, telegram):
        self.sent.append(
            (telegram.operate_code, list(telegram.payload or []), telegram.target_address)
        )
        if telegram.operate_code == OC.SingleChannelControl:
            ch, lvl = telegram.payload[0], telegram.payload[1]
            self.loop.call_later(
                0.01, self.feed, telegram.target_address,
                OC.SingleChannelControlResponse, [ch, 0xF8, lvl],
            )

    def feed(self, addr, op, payload):
        for cb, a in list(self.cbs):
            if a == tuple(addr):
                cb(Tel(op, payload, addr))


class FakeGateway:
    def __init__(self, bus):
        self.hdl = bus
        self.device_id = "gw"
        self.available = True


class FakeEntry:
    entry_id = "e1"
    options = {}


class FakeHass:
    data = {}


def prep(entity):
    entity.hass = FakeHass()
    return entity


def sent_ops(bus, op):
    return [(p, a) for o, p, a in bus.sent if o == op]


async def main():
    loop = asyncio.get_running_loop()
    fails = 0

    def check(name, cond):
        nonlocal fails
        print(("PASS " if cond else "FAIL ") + name)
        fails += 0 if cond else 1

    # --- colour maths -------------------------------------------------------
    check("full red at full brightness", colour.group_levels((255, 0, 0), 255) == [100, 0, 0])
    check("half brightness white", colour.group_levels((255, 255, 255), 128) == [50, 50, 50])
    check("dim colour keeps its weak primary", colour.group_levels((255, 10, 0), 30)[1] == 1)
    on, bri, col = colour.group_state([50, 25, 0])
    check("state from levels", on and bri == 128 and col == (255, 128, 0))
    check("all zero = off", colour.group_state([0, 0, 0])[0] is False)
    check("kelvin bytes", colour.kelvin_bytes(4000) == [15, 160])
    check("rgbwaf pads amber/free", colour.dali_colour_values("rgbwaf", (1, 2, 3, 4)) == [1, 2, 3, 4, 0, 0])
    check("rgbcw 5 values", colour.dali_colour_values("rgbcw", (1, 2, 3, 4, 5)) == [1, 2, 3, 4, 5])

    # --- RGB group entity ---------------------------------------------------
    bus = FakeBus(loop)
    gw = FakeGateway(bus)
    cfg = {
        "id": "g1", const.CONF_DEVICE_TYPE: const.DEVICE_TYPE_RGB_LIGHT,
        const.CONF_NAME: "Strip", const.CONF_SUBNET_ID: 1, const.CONF_DEVICE_ID: 23,
        const.CONF_RGB_MODE: "rgb", const.CONF_RED_CHANNEL: 4,
        const.CONF_GREEN_CHANNEL: 5, const.CONF_BLUE_CHANNEL: 6,
    }
    ent = prep(light_mod.ARHDLRgbGroupLight(FakeEntry(), gw, cfg))
    ent._license_active = True
    await ent.async_added_to_hass()
    check("group is RGB", ent._attr_supported_color_modes == {light_mod.ColorMode.RGB})
    check("group starts off", not ent.is_on)

    bus.sent.clear()
    await ent.async_turn_on(rgb_color=(255, 0, 128), brightness=255)
    cmds = [(p[0], p[1]) for p, a in sent_ops(bus, OC.SingleChannelControl)]
    check("three channel commands", cmds == [(4, 100), (5, 0), (6, 50)])
    await asyncio.sleep(0.05)
    check("on after replies", ent.is_on)
    check("colour from the bus", ent.rgb_color == (255, 0, 128))
    check("brightness from the bus", ent.brightness == 255)

    # A keypad dims the module: the wheel follows.
    bus.feed((1, 23), OC.ReadStatusOfChannelsResponse, [6, 0, 0, 0, 40, 0, 20])
    await asyncio.sleep(0.01)
    check("external change -> brightness", ent.brightness == 102)
    check("external change -> colour", ent.rgb_color == (255, 0, 128))

    bus.sent.clear()
    await ent.async_turn_off()
    check("off zeroes all three",
          [(p[0], p[1]) for p, a in sent_ops(bus, OC.SingleChannelControl)] == [(4, 0), (5, 0), (6, 0)])
    await asyncio.sleep(0.05)
    bus.sent.clear()
    await ent.async_turn_on()
    check("plain on restores last colour",
          [(p[0], p[1]) for p, a in sent_ops(bus, OC.SingleChannelControl)] == [(4, 40), (5, 0), (6, 20)])
    await asyncio.sleep(0.05)
    check("other channels of the module untouched",
          all(p[0] in (4, 5, 6) for p, a in sent_ops(bus, OC.SingleChannelControl)))

    # RGBW group
    cfgw = dict(cfg, id="g2", **{const.CONF_RGB_MODE: "rgbw", const.CONF_RED_CHANNEL: 1,
                                 const.CONF_GREEN_CHANNEL: 2, const.CONF_BLUE_CHANNEL: 3,
                                 const.CONF_WHITE_CHANNEL: 7})
    entw = prep(light_mod.ARHDLRgbGroupLight(FakeEntry(), gw, cfgw))
    await entw.async_added_to_hass()
    bus.sent.clear()
    await entw.async_turn_on(rgbw_color=(0, 0, 0, 255), brightness=128)
    check("RGBW white only",
          [(p[0], p[1]) for p, a in sent_ops(bus, OC.SingleChannelControl)] == [(1, 0), (2, 0), (3, 0), (7, 50)])

    # --- DALI-2 colour channel ---------------------------------------------
    bus2 = FakeBus(loop)
    gw2 = FakeGateway(bus2)
    dcfg = {
        "id": "d1", const.CONF_DEVICE_TYPE: const.DEVICE_TYPE_LIGHT, const.CONF_NAME: "Dali",
        const.CONF_SUBNET_ID: 1, const.CONF_DEVICE_ID: 40, const.CONF_CHANNEL: 9,
        const.CONF_DIMMABLE: True, const.CONF_LIGHT_COLOR: "rgbw",
    }
    dl = prep(light_mod.ARHDLDaliColourLight(FakeEntry(), gw2, dcfg))
    await dl.async_added_to_hass()
    bus2.sent.clear()
    await dl.async_turn_on(rgbw_color=(10, 20, 30, 40), brightness=255)
    e45c = sent_ops(bus2, OC.ControlDALIColour)
    check("DALI colour -> 0xE45C",
          e45c and e45c[0][0] == [9, 100, 0xFE, 0, 0, 4, 10, 20, 30, 40, 0, 0])
    check("DALI colour remembered", dl.rgbw_color == (10, 20, 30, 40))
    bus2.sent.clear()
    await dl.async_turn_on(brightness=128)
    check("DALI brightness-only uses the dimmer command",
          sent_ops(bus2, OC.SingleChannelControl) and not sent_ops(bus2, OC.ControlDALIColour))

    cct = light_mod.ARHDLDaliColourLight(
        FakeEntry(), gw2, dict(dcfg, id="d2", **{const.CONF_CHANNEL: 10, const.CONF_LIGHT_COLOR: "cct"}))
    bus2.sent.clear()
    await cct.async_turn_on(color_temp_kelvin=3000)
    check("CCT sends Kelvin bytes",
          sent_ops(bus2, OC.ControlDALIColour)[0][0][5:8] == [2, 11, 184])

    # --- platform setup picks classes; plain dimmers stay plain -------------
    added = []

    class Data:
        gateway = gw

    class Hass:
        data = {const.DOMAIN: {"e1": Data()}}

    plain = {"id": "p1", const.CONF_DEVICE_TYPE: const.DEVICE_TYPE_LIGHT, const.CONF_NAME: "Plain",
             const.CONF_SUBNET_ID: 1, const.CONF_DEVICE_ID: 23, const.CONF_CHANNEL: 1,
             const.CONF_DIMMABLE: True}
    old_style = dict(plain, id="p2", **{const.CONF_CHANNEL: 2})  # no light_color key at all
    entry = FakeEntry()
    entry.options = {const.CONF_DEVICES: [plain, old_style, cfg, dcfg]}
    await light_mod.async_setup_entry(Hass(), entry, lambda ents: added.extend(ents))
    kinds = [type(e).__name__ for e in added]
    check("plain dimmers use the original class",
          kinds[:2] == ["ARHDLLight", "ARHDLLight"])
    check("group + DALI classes", kinds[2:] == ["ARHDLRgbGroupLight", "ARHDLDaliColourLight"])
    check("plain dimmer still brightness-only",
          added[0]._attr_supported_color_modes == {light_mod.ColorMode.BRIGHTNESS})

    # --- options-flow helpers -------------------------------------------------
    n = cf._normalize_device_input(const.DEVICE_TYPE_LIGHT, {
        const.CONF_NAME: "x", const.CONF_SUBNET_ID: 1.0, const.CONF_DEVICE_ID: 2.0,
        const.CONF_CHANNEL: 3.0, const.CONF_DIMMABLE: True, const.CONF_LIGHT_COLOR: "none"})
    check("plain light stores no colour key", const.CONF_LIGHT_COLOR not in n)
    g = cf._normalize_device_input(const.DEVICE_TYPE_RGB_LIGHT, {
        const.CONF_NAME: "g", const.CONF_SUBNET_ID: 1.0, const.CONF_DEVICE_ID: 23.0,
        const.CONF_RGB_MODE: "rgb", const.CONF_RED_CHANNEL: 1.0, const.CONF_GREEN_CHANNEL: 2.0,
        const.CONF_BLUE_CHANNEL: 3.0, const.CONF_WHITE_CHANNEL: 0.0,
        const.CONF_REPLACE_CHANNELS: True})
    check("group ints + no replace flag stored",
          g[const.CONF_RED_CHANNEL] == 1 and const.CONF_REPLACE_CHANNELS not in g
          and const.CONF_WHITE_CHANNEL not in g)
    existing = [
        {"id": "a", const.CONF_DEVICE_TYPE: "light", const.CONF_SUBNET_ID: 1, const.CONF_DEVICE_ID: 23, const.CONF_CHANNEL: 2},
        {"id": "b", const.CONF_DEVICE_TYPE: "light", const.CONF_SUBNET_ID: 1, const.CONF_DEVICE_ID: 23, const.CONF_CHANNEL: 5},
        {"id": "c", const.CONF_DEVICE_TYPE: "light", const.CONF_SUBNET_ID: 1, const.CONF_DEVICE_ID: 24, const.CONF_CHANNEL: 1},
    ]
    plain_c, groups_c = cf._channel_conflicts(dict(g, id="new"), existing)
    check("conflict only on same module + channel", [d["id"] for d in plain_c] == ["a"])
    err = cf.ARHDLOptionsFlow._channel_error(dict(g, id="new"), existing, replace=False)
    check("conflict without replace -> error", err == "rgb_channel_in_use")
    check("conflict with replace -> ok",
          cf.ARHDLOptionsFlow._channel_error(dict(g, id="new"), existing, replace=True) is None)
    with_group = existing + [dict(g, id="new")]
    check("plain light onto a grouped channel -> error",
          cf.ARHDLOptionsFlow._channel_error(
              {"id": "z", const.CONF_DEVICE_TYPE: "light", const.CONF_SUBNET_ID: 1,
               const.CONF_DEVICE_ID: 23, const.CONF_CHANNEL: 3}, with_group) == "rgb_channel_in_group")
    check("plain light elsewhere -> ok",
          cf.ARHDLOptionsFlow._channel_error(
              {"id": "z", const.CONF_DEVICE_TYPE: "light", const.CONF_SUBNET_ID: 1,
               const.CONF_DEVICE_ID: 23, const.CONF_CHANNEL: 6}, with_group) is None)
    check("duplicate channel rejected",
          cf._validate_rgb_group(dict(g, **{const.CONF_GREEN_CHANNEL: 1})) == "rgb_channels_duplicate")
    check("rgbw needs white",
          cf._validate_rgb_group(dict(g, **{const.CONF_RGB_MODE: "rgbw"})) == "rgb_channels_missing")
    check("summary shows group channels", "1.23.1/2/3" in cf._device_summary(g))

    # --- scan: DALI-2 channel types -> colour lights -----------------------
    scanner = discovery.BusScanner.__new__(discovery.BusScanner)
    scanner._found = {}
    scanner._buspro = None

    class ScanTel:
        def __init__(self, op, payload):
            self.operate_code = op
            self.payload = payload
            self.source_address = (1, 40)
            self.udp_data = bytes(19) + bytes([0x10, 0xD4]) + bytes(10)
            self.source_device_type = None

    scanner._on_telegram(ScanTel(OC.ReadDALIChannelInfoResponse, [0x0B, 1, 3, 2, 0, 4]))
    disc = scanner._found[(1, 40)]
    check("scan stores DALI types", disc.dali_channel_types == [1, 3, 2, 0, 4])
    scanner._on_telegram(ScanTel(OC.ReadDALIChannelInfoResponse, [0x0A, 100, 100]))
    check("other sub-command ignored", disc.dali_channel_types == [1, 3, 2, 0, 4])
    build = cf.ARHDLOptionsFlow._build_imported_device
    check("ch1 dimming = plain", const.CONF_LIGHT_COLOR not in build(disc, "light", 1))
    check("ch2 RGB", build(disc, "light", 2).get(const.CONF_LIGHT_COLOR) == "rgb")
    check("ch3 CCT", build(disc, "light", 3).get(const.CONF_LIGHT_COLOR) == "cct")
    check("ch4 on/off", build(disc, "light", 4)[const.CONF_DIMMABLE] is False)
    check("ch9 (not reported) = plain", const.CONF_LIGHT_COLOR not in build(disc, "light", 9))
    check("DALI-2 type code is a dimmer", "0x10D4" in const.HDL_DIMMER_TYPE_CODES)

    # --- scan: DLP AC gets its own protocol, Enviro unchanged ---------------
    flow = cf.ARHDLOptionsFlow.__new__(cf.ARHDLOptionsFlow)

    class D:
        def __init__(self, code):
            self.type_code = code
            self.subnet_id, self.device_id = 1, 70
            self.remark = None
            self.address = "1.70"
            self.op_codes = set()

    for code, kind, proto in (
        ("0x0095", "dlp_ac", "dlp_classic"),   # classic DLP
        ("0x08C1", "dlp_ac", "dlp_precise"),   # OLED TILE
        ("0x08DF", "dlp_ac", "hvac"),
        ("0x0890", "ac_panel", None),          # Granite (Enviro family)
    ):
        devs = []
        flow._import_panel_feature(D(code), cf.PANEL_FEATURE_AC, devs)
        got = devs[0] if devs else {}
        check(f"AC import {code} -> {kind}/{proto}",
              got.get(const.CONF_CLIMATE_KIND) == kind
              and got.get(const.CONF_AC_PROTOCOL) == proto)

    # --- climate entity: DLP AC through the real climate.py ----------------
    climate_mod = importlib.import_module(f"{PKG}.climate")
    dlp_mod = importlib.import_module(f"{PKG}.pybuspro.devices.dlp_ac")
    pac = importlib.import_module(f"{PKG}.pybuspro.devices.panel_ac")
    pcl = importlib.import_module(f"{PKG}.pybuspro.devices.climate")
    dlp_mod.AutoPanelAc._SETTLE_SECONDS = 0.05
    dlp_mod.AutoPanelAc._ENVIRO_GRACE_SECONDS = 0.0
    dlp_mod.DlpAirConditioner._READBACK_DELAY = 0
    pac.PanelAirConditioner._STARTUP_RETRY_SECONDS = 3600
    pcl.AirConditioner._READ_RETRY_SECONDS = 3600
    classic = [1, 24, 0x01, 0, 27, 22, 1, 25, 23, 0]

    bus3 = FakeBus(loop)
    legacy = {"id": "ac1", const.CONF_DEVICE_TYPE: const.DEVICE_TYPE_CLIMATE,
              const.CONF_NAME: "Lounge AC", const.CONF_SUBNET_ID: 1, const.CONF_DEVICE_ID: 70,
              const.CONF_CLIMATE_KIND: "ac_panel", const.CONF_HVAC_NUMBER: 1,
              const.CONF_TEMP_CHANNEL: 1}
    c1 = prep(climate_mod.ARHDLPanelAcClimate(FakeEntry(), FakeGateway(bus3), legacy))
    await c1.async_added_to_hass()
    bus3.feed((1, 70), OC.ReadDLPACResponse, classic)
    await asyncio.sleep(0.15)
    check("legacy ac_panel entry on a DLP detects dlp_classic",
          c1.extra_state_attributes.get("ac_protocol") == "dlp_classic")
    check("legacy entry now offers all DLP modes", len(c1.hvac_modes) == 6)
    check("room temperature shown", c1.current_temperature == 27.0)
    bus3.sent.clear()
    await c1.async_set_hvac_mode(climate_mod.HVACMode.DRY)
    check("legacy entry now controls the DLP",
          sent_ops(bus3, OC.ControlDLPAC) and sent_ops(bus3, OC.ControlDLPAC)[0][0][2] >> 4 == 4)

    bus4 = FakeBus(loop)
    scanned = dict(legacy, id="ac2", **{const.CONF_CLIMATE_KIND: "dlp_ac",
                                        const.CONF_AC_PROTOCOL: "dlp_classic",
                                        const.CONF_AC_HVAC_MODES: ["cool", "heat", "fan_only", "auto", "dry"]})
    c2 = prep(climate_mod.ARHDLPanelAcClimate(FakeEntry(), FakeGateway(bus4), scanned))
    bus4.feed((1, 70), OC.ReadDLPACResponse, classic)
    await asyncio.sleep(0.01)
    check("scanned dlp_ac entry available", c2._ac.available)
    bus4.sent.clear()
    await c2.async_set_temperature(temperature=21)
    check("dlp_ac setpoint write", sent_ops(bus4, OC.ControlDLPAC)[0][0][1] == 21)
    restricted = dict(scanned, id="ac3", **{const.CONF_AC_HVAC_MODES: ["cool"]})
    c3 = prep(climate_mod.ARHDLPanelAcClimate(FakeEntry(), FakeGateway(FakeBus(loop)), restricted))
    check("user-narrowed modes respected", [m.value for m in c3.hvac_modes] == ["off", "cool"])
    for c in (c1, c2, c3):
        await c.async_will_remove_from_hass()

    await asyncio.sleep(0.05)
    print(f"\n{'ALL PASSED' if not fails else f'{fails} FAILED'}")
    return fails


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main()) else 0)
