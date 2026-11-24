"""Live status of relay/dimmer modules (no Home Assistant needed).

Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).

    python3 tests/test_offline_module.py

Covers: after a restart every channel shows what its module reports, with
one paced status read per module (not per channel) retried until answered;
an unanswered command keeps the commanded state (a lost reply looks exactly
like a lost command, so it is never guessed back to the old level); a module
that answers reads goes unavailable until it answers again; every live module
is re-read periodically so a missed telegram can't leave the state wrong.
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
device_mod = importlib.import_module("pb.devices.device")
switch_mod = importlib.import_module("pb.devices.switch")
light_mod = importlib.import_module("pb.devices.light")
OC = enums.OperateCode

# Speed the timings up; the logic is the same.
device_mod.Device._ACK_TIMEOUT = 0.1
device_mod._SYNC_START_DELAY = 0.05
device_mod._SYNC_PACING = 0.01
device_mod._SYNC_FAST_RETRY = 0.2
device_mod._SYNC_SLOW_RETRY = 0.4


class Tg:
    def __init__(self, op, payload, src):
        self.operate_code, self.payload, self.source_address = op, payload, src


class FakeBus:
    """Several 12-channel modules behind one gateway."""

    def __init__(self, loop, n_modules=1, answers_reads=True, lose_reads=0,
                 panel_buttons=False):
        self.loop = loop
        self.modules = {(1, 10 + i): [0] * 12 for i in range(n_modules)}
        self.online = {a: True for a in self.modules}
        self.answers_reads = answers_reads
        self.lose_reads = lose_reads  # first N read replies are lost on the bus
        self.panel_buttons = panel_buttons  # answers 0xE3DA [17, button, button]
        self.cbs = []
        self.sent = []
        self.network_interface = self
        self.logger = __import__("logging").getLogger("t")

    def register_telegram_received_device_cb(self, cb, addr, postfix=None):
        self.cbs.append((cb, tuple(addr)))

    async def send_telegram(self, telegram):
        addr = tuple(telegram.target_address)
        self.sent.append((telegram.operate_code, addr))
        if not self.online.get(addr):
            return
        levels = self.modules[addr]
        if telegram.operate_code == OC.SingleChannelControl:
            ch, lvl = telegram.payload[0], telegram.payload[1]
            levels[ch - 1] = lvl
            self.loop.call_later(0.02, self.reply, addr,
                                 OC.SingleChannelControlResponse, [ch, 0xF8, lvl])
        elif telegram.operate_code == OC.ReadStatusOfChannels and self.answers_reads:
            if self.lose_reads:
                self.lose_reads -= 1
                return
            self.loop.call_later(0.02, self.reply, addr,
                                 OC.ReadStatusOfChannelsResponse, [12, *levels])

        elif telegram.operate_code == OC.ReadPanelAC and self.panel_buttons:
            field, button = telegram.payload[0], telegram.payload[1]
            if field == 17:
                self.loop.call_later(0.02, self.reply, addr, OC.ReadPanelACResponse,
                                     [17, button, 1 if levels[button - 1] else 0])

    def reply(self, addr, op, payload):
        for cb, a in list(self.cbs):
            if a == addr:
                cb(Tg(op, payload, addr))

    def reads(self, addr=None):
        return sum(1 for op, a in self.sent
                   if op == OC.ReadStatusOfChannels and (addr is None or a == addr))


async def test_restart_shows_live_state_one_read_per_module():
    bus = FakeBus(asyncio.get_running_loop(), n_modules=5)
    bus.modules[(1, 12)][4] = 100   # 1.12 ch5 is ON at the wall
    bus.modules[(1, 14)][0] = 60    # 1.14 ch1 dimmed to 60
    chans = {(a, ch): switch_mod.Switch(bus, a, ch)
             for a in bus.modules for ch in range(1, 13)}
    lt = light_mod.Light(bus, (1, 14), 1)
    await asyncio.sleep(0.4)
    assert chans[((1, 12), 5)].is_on is True, "ON light must show ON after restart"
    assert lt.current_brightness == 60
    assert sum(c.is_on for c in chans.values()) == 2  # 1.12 ch5 and 1.14 ch1
    # 61 channels on 5 modules: 5 reads, not 61.
    assert bus.reads() == 5, f"expected one read per module, sent {bus.reads()}"


async def test_lost_replies_are_retried_until_answered():
    bus = FakeBus(asyncio.get_running_loop(), n_modules=3, lose_reads=4)
    bus.modules[(1, 11)][2] = 100
    sw = switch_mod.Switch(bus, (1, 11), 3)
    others = [switch_mod.Switch(bus, a, 1) for a in ((1, 10), (1, 12))]
    await asyncio.sleep(1.2)
    assert sw.is_on is True, "lost replies must be re-asked, not given up on"
    assert all(o.is_on is False for o in others)
    reads_after = bus.reads()
    await asyncio.sleep(0.8)
    assert bus.reads() == reads_after, "no more reads once every module answered"


async def test_reconnect_rereads_once_per_module():
    bus = FakeBus(asyncio.get_running_loop())
    chans = [switch_mod.Switch(bus, (1, 10), ch) for ch in range(1, 13)]
    await asyncio.sleep(0.3)
    bus.modules[(1, 10)][6] = 100  # changed while the gateway link was down
    before = bus.reads()
    for c in chans:  # every entity resyncs on reconnect
        await c.read_status()
    await asyncio.sleep(0.3)
    assert bus.reads() - before == 1
    assert chans[6].is_on is True


async def test_dead_module_keeps_command_and_goes_unavailable():
    bus = FakeBus(asyncio.get_running_loop())
    sw = switch_mod.Switch(bus, (1, 10), 1)
    sibling = switch_mod.Switch(bus, (1, 10), 2)
    await asyncio.sleep(0.3)
    bus.online[(1, 10)] = False
    await sw.set_on()
    assert sw.is_on is True, "optimistic state shows straight away"
    await asyncio.sleep(0.5)  # ack + resend + read + wait
    assert sw.is_on is True, "unconfirmed command must not be guessed back to OFF"
    assert not sw.responsive and not sibling.responsive


async def test_module_comes_back_on_its_own():
    bus = FakeBus(asyncio.get_running_loop())
    sw = switch_mod.Switch(bus, (1, 10), 1)
    await asyncio.sleep(0.3)
    bus.online[(1, 10)] = False
    await sw.set_on()
    await asyncio.sleep(0.5)
    assert not sw.responsive
    bus.modules[(1, 10)][0] = 100  # switched on at the panel meanwhile
    bus.online[(1, 10)] = True
    await asyncio.sleep(1.0)
    assert sw.responsive and sw.is_on is True


async def test_live_module_unaffected():
    bus = FakeBus(asyncio.get_running_loop())
    sw = switch_mod.Switch(bus, (1, 10), 1)
    await asyncio.sleep(0.3)
    await sw.set_on()
    await asyncio.sleep(0.5)
    assert sw.is_on is True and sw.responsive


async def test_lost_reply_does_not_show_light_off():
    """The field bug: light really switched ON, its reply was lost on the
    wireless mesh, and Home Assistant reverted the entity to OFF."""
    bus = FakeBus(asyncio.get_running_loop(), answers_reads=False)
    sw = switch_mod.Switch(bus, (1, 10), 1)
    await sw.set_off()
    await asyncio.sleep(0.1)
    real_reply = bus.reply
    bus.reply = lambda *a: None          # module acts, but replies are lost
    await sw.set_on()
    await asyncio.sleep(0.5)
    bus.reply = real_reply
    assert bus.modules[(1, 10)][0] == 100, "light is really on"
    assert sw.is_on is True and sw.responsive, "HA must not show it OFF"


async def test_silent_panel_is_left_alone_and_harmless():
    bus = FakeBus(asyncio.get_running_loop(), answers_reads=False)
    sw = switch_mod.Switch(bus, (1, 10), 1)
    await asyncio.sleep(1.0)
    assert sw.is_on is False and sw.responsive
    # A button press on the panel still updates it live.
    bus.modules[(1, 10)][0] = 100
    bus.reply((1, 10), OC.SingleChannelControlResponse, [1, 0xF8, 100])
    await asyncio.sleep(0.05)
    assert sw.is_on is True


async def test_reads_are_sent_from_home_assistants_own_subnet():
    # Replies are addressed to the sender. From 200.200 they were kept on a
    # subnet-200 bus by the gateway and never reached Home Assistant.
    th_mod = importlib.import_module("pb.helpers.telegram_helper")
    control = importlib.import_module("pb.devices.control")
    bus = FakeBus(asyncio.get_running_loop())
    reader = control._ReadStatusOfChannels(bus)
    reader.subnet_id, reader.device_id = 200, 13
    buf = bytes(th_mod.TelegramHelper(bus).build_send_buffer(reader.telegram))
    sender = (buf[17], buf[18])  # after IP(4) + HDLMIRACLE(10) + AAAA + len
    assert sender == (250, 250), f"sender {sender}"


async def test_periodic_resync_heals_a_missed_change():
    old = device_mod._RESYNC_SECONDS
    device_mod._RESYNC_SECONDS = 0.3
    try:
        bus = FakeBus(asyncio.get_running_loop(), n_modules=2)
        a = switch_mod.Switch(bus, (1, 10), 4)
        b = switch_mod.Switch(bus, (1, 11), 2)
        await asyncio.sleep(0.2)
        assert not a.is_on and not b.is_on
        # Switched at the wall, but the broadcast never reached HA.
        bus.modules[(1, 10)][3] = 100
        bus.modules[(1, 11)][1] = 40
        await asyncio.sleep(0.6)
        assert a.is_on and b.is_on, "periodic read must pick up missed changes"
    finally:
        device_mod._RESYNC_SECONDS = old


async def test_resync_skips_a_module_mid_click():
    old = device_mod._RESYNC_SECONDS
    device_mod._RESYNC_SECONDS = 0.3
    try:
        bus = FakeBus(asyncio.get_running_loop())
        sw = switch_mod.Switch(bus, (1, 10), 1)
        await asyncio.sleep(0.2)
        sw._awaiting_ack = True          # a command is waiting for its reply
        sw._inflight = [(bus.loop.time() + 10, 100, 1)]
        before = bus.reads()
        await asyncio.sleep(0.5)
        assert bus.reads() == before, "must not read a module mid-click"
    finally:
        device_mod._RESYNC_SECONDS = old


async def test_module_dying_quietly_goes_unavailable_on_resync():
    """Nobody presses anything: missed periodic reads alone mark it offline."""
    old = device_mod._RESYNC_SECONDS
    device_mod._RESYNC_SECONDS = 0.3
    try:
        bus = FakeBus(asyncio.get_running_loop())
        sw = switch_mod.Switch(bus, (1, 10), 1)
        await asyncio.sleep(0.2)
        assert sw.responsive
        bus.online[(1, 10)] = False      # module loses power
        await asyncio.sleep(1.0)
        assert sw.responsive, "a couple of missed reads is not enough"
        await asyncio.sleep(1.6)
        assert not sw.responsive, "missed re-sync reads must mark it offline"
        bus.online[(1, 10)] = True
        await asyncio.sleep(0.6)
        assert sw.responsive, "comes back once it answers"
    finally:
        device_mod._RESYNC_SECONDS = old


async def test_known_module_dead_at_startup_goes_unavailable():
    bus = FakeBus(asyncio.get_running_loop())
    bus.known_modules = {(1, 10)}        # answered before the restart
    bus.online[(1, 10)] = False
    sw = switch_mod.Switch(bus, (1, 10), 1)
    await asyncio.sleep(1.5)
    assert sw.responsive, "still within the startup retries"
    await asyncio.sleep(1.3)
    assert not sw.responsive, "known module dead at startup must go offline"


async def test_never_answered_module_dead_at_startup_left_alone():
    # Could be hardware that never answers status reads: don't grey it out.
    bus = FakeBus(asyncio.get_running_loop())
    bus.online[(1, 10)] = False
    sw = switch_mod.Switch(bus, (1, 10), 1)
    await asyncio.sleep(2.8)
    assert sw.responsive


async def test_first_answer_is_remembered():
    bus = FakeBus(asyncio.get_running_loop(), n_modules=2)
    bus.known_modules = {(1, 11)}
    saved = []
    bus.on_module_answered = saved.append
    switch_mod.Switch(bus, (1, 10), 1)
    switch_mod.Switch(bus, (1, 11), 1)
    await asyncio.sleep(0.4)
    assert saved == [(1, 10)], f"only the new module is reported, got {saved}"
    assert bus.known_modules == {(1, 10), (1, 11)}


async def test_gateway_down_does_not_mark_modules_offline():
    # The gateway check greys everything out; modules must not ALSO be marked
    # offline (they'd stay grey for a minute after the gateway came back).
    bus = FakeBus(asyncio.get_running_loop())
    bus.known_modules = {(1, 10)}
    bus.liveness = types.SimpleNamespace(reachable=False)
    bus.online[(1, 10)] = False
    sw = switch_mod.Switch(bus, (1, 10), 1)
    await asyncio.sleep(2.8)
    assert sw.responsive


async def main():
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            await fn()
            print("ok ", name)
    print("\nALL PASSED")


asyncio.run(main())
