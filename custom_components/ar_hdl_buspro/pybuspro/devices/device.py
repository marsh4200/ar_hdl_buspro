"""Base device class for the pybuspro library."""
from __future__ import annotations

import asyncio
import random

from .control import _ReadStatusOfChannels

# How often to retry the startup channel-status read while nothing real has
# been observed yet (see _call_read_current_status_of_channels below). A
# single one-shot attempt is fragile: on a full HA restart, every Switch and
# Light on the integration schedules its read for the same ~3s mark, so a
# handful of channels can turn into a burst of simultaneous UDP requests
# right as the gateway/network stack is still warming up -- lose one packet
# (request or response, no retry) and that channel is stuck showing its
# default "off" forever, until someone operates it manually. Mirrors the
# retry-until-first-status pattern already proven safe for AC/IR channels
# in climate.py's AirConditioner (see its docstring for why *continuous*
# polling was tried and reverted instead -- this stops the moment a real
# reading arrives, same as that one).
_CHANNEL_STATUS_RETRY_SECONDS = 20
# After this many unanswered startup reads, stop hammering the bus every 20s
# and fall back to a slow retry. (Buspro wireless relay panels were thought
# never to answer ReadStatusOfChannels; they do - the replies were addressed
# to the old 200.200 sender and kept on the bus by the gateway, see
# HA_SOURCE_ADDRESS in telegram_helper.py.) A device that answers: the loop still
# stops the moment a real status arrives, and a device that was merely
# offline at boot is still picked up by the slow retry.
_STARTUP_FAST_RETRIES = 6
_STARTUP_SLOW_RETRY_SECONDS = 600
# Identical on-demand channel reads to one module within this window are
# collapsed into one (see _call_read_current_status_of_channels).
_READ_DEDUPE_SECONDS = 0.5
# ----- live status of relay/dimmer modules --------------------------------
# One ReadStatusOfChannels answer carries EVERY channel of a module, so the
# status read is done per module, not per channel: a 12-channel module used
# to receive 12 identical reads at startup, and a site with many modules put
# hundreds of frames on the bus within two seconds - replies were lost and
# lights showed the wrong state after a restart. Modules are now read one at
# a time, paced, and re-asked until each one answers.
_SYNC_START_DELAY = 1.0      # let every channel of the platform register first
_SYNC_PACING = 0.15          # gap between reads to different modules
_SYNC_FAST_RETRY = 3         # seconds between rounds while a module is new
_SYNC_FAST_ROUNDS = 10       # ~30 s of fast retries per module
_SYNC_SLOW_RETRY = 60        # then keep asking once a minute until it answers


def _module_state(buspro, address) -> dict:
    """Shared state for every channel of one module (subnet, device).

    `last_heard`: loop time of the last telegram FROM this module.
    `answers_reads`: has it ever answered ReadStatusOfChannels (wireless relay
    panels never do, so they are never marked unresponsive).
    `offline`: it stopped answering a command and a status read.
    `needs_read` / `attempts`: live-status read still owed (see _module_sync).
    """
    modules = buspro.__dict__.setdefault("_module_states", {})
    return modules.setdefault(
        tuple(address),
        {
            "last_heard": None,
            "answers_reads": False,
            "offline": False,
            "devices": [],
            "needs_read": False,
            "attempts": 0,
        },
    )


def _request_module_read(buspro, address, slow: bool = False) -> None:
    """Queue a live status read of one module and make sure the loop runs."""
    module = _module_state(buspro, address)
    module["needs_read"] = True
    module["attempts"] = _SYNC_FAST_ROUNDS if slow else 0
    task = buspro.__dict__.get("_module_sync_task")
    if task is None or task.done():
        buspro._module_sync_task = asyncio.ensure_future(
            _module_sync(buspro), loop=buspro.loop
        )


async def _module_sync(buspro) -> None:
    """Read each module that still owes its live status, one at a time."""
    await asyncio.sleep(_SYNC_START_DELAY)
    modules = buspro.__dict__.setdefault("_module_states", {})
    while True:
        pending = [a for a, m in modules.items() if m["needs_read"]]
        if not pending or buspro.network_interface is None:
            return  # all answered, or the bus was stopped (unload/reconnect)
        for address in pending:
            module = modules[address]
            if not module["needs_read"]:
                continue  # answered while this round was going
            module["attempts"] += 1
            reader = _ReadStatusOfChannels(buspro)
            reader.subnet_id, reader.device_id = address
            try:
                await reader.send()
            except Exception:  # noqa: BLE001
                buspro.logger.debug("Status read failed for %s.%s", *address)
            await asyncio.sleep(_SYNC_PACING)
        still = [modules[a] for a in pending if modules[a]["needs_read"]]
        if not still:
            continue  # re-check: something may have been queued meanwhile
        fast = any(m["attempts"] < _SYNC_FAST_ROUNDS for m in still)
        await asyncio.sleep(_SYNC_FAST_RETRY if fast else _SYNC_SLOW_RETRY)



def _stats(buspro) -> dict:
    """Per-bus command timing, shown in the integration's diagnostics."""
    stats = getattr(buspro, "bus_stats", None)
    if stats is None:
        stats = {
            "commands_confirmed": 0,
            "resends": 0,
            "unanswered_after_resend": 0,
            "reverted_unanswered": 0,
            "stale_replies_ignored": 0,
            "reply_ms_max": 0,
            "slow_replies": [],
        }
        buspro.bus_stats = stats
    return stats


def _bump(buspro, key: str) -> None:
    stats = _stats(buspro)
    stats[key] = stats.get(key, 0) + 1


def _record_latency(buspro, address, seconds: float) -> None:
    """Note how long a channel took to confirm the latest command."""
    stats = _stats(buspro)
    ms = int(seconds * 1000)
    stats["commands_confirmed"] += 1
    stats["reply_ms_max"] = max(stats["reply_ms_max"], ms)
    if ms >= _SLOW_REPLY_MS:
        slow = stats["slow_replies"]
        slow.append({"device": f"{address[0]}.{address[1]}", "ms": ms})
        del slow[:-20]
        buspro.logger.debug("Slow reply from %s.%s: %s ms", address[0], address[1], ms)


# Replies slower than this are listed individually in diagnostics.
_SLOW_REPLY_MS = 400


def startup_retry_delay(attempt: int) -> int:
    """Seconds to wait after startup read number `attempt` (1-based)."""
    if attempt < _STARTUP_FAST_RETRIES:
        return _CHANNEL_STATUS_RETRY_SECONDS
    return _STARTUP_SLOW_RETRY_SECONDS


class Device:
    """Base class for HDL Buspro devices."""

    def __init__(self, buspro, device_address, name: str = "") -> None:
        """Initialize a device wrapper.

        device_address is a (subnet_id, device_id) tuple.
        """
        self._device_address = device_address
        self._buspro = buspro
        self._name = name
        self.device_updated_cbs: list = []
        # Set by Switch/Light once a real ReadStatusOfChannelsResponse or
        # SingleChannelControlResponse for this channel has been seen --
        # tells _call_read_current_status_of_channels' startup retry loop
        # when to stop. Deliberately separate from "brightness == 0" since
        # that's a legitimate real reading, not just "never confirmed".
        self._got_initial_status = False
        # Last level the bus actually confirmed, to fall back to when a
        # command from here goes unanswered.
        self._confirmed_level = None
        self._module = None  # set by _join_module (switch/light channels)

    # ----- module live status / responsiveness ---------------------------

    def _join_module(self) -> None:
        """Register with this module and queue its live status read."""
        self._module = _module_state(self._buspro, self._device_address)
        self._module["devices"].append(self)
        if not self._module["answers_reads"]:
            _request_module_read(self._buspro, self._device_address)

    def _request_live_status(self) -> None:
        """Re-read this module's channels (reconnect, module back online)."""
        _request_module_read(self._buspro, self._device_address)

    @property
    def responsive(self) -> bool:
        """False once this module has stopped answering (see _module_went_quiet)."""
        return self._module is None or not self._module["offline"]

    def _heard_from_module(self, is_status_read: bool = False) -> None:
        """Note a telegram from this module; brings it back if it was offline."""
        module = self._module
        if module is None:
            return
        module["last_heard"] = self._buspro.loop.time()
        if is_status_read:
            module["answers_reads"] = True
            module["needs_read"] = False
        if not module["offline"]:
            return
        module["offline"] = False
        self._buspro.logger.info(
            "HDL module %s.%s is answering again",
            self._device_address[0], self._device_address[1],
        )
        for dev in list(module["devices"]):
            dev._call_device_updated()
        if not is_status_read:
            # Channels may have changed while it was away: re-read them all.
            self._request_live_status()

    def _module_went_quiet(self) -> None:
        """Mark this module unresponsive and keep asking until it answers."""
        module = self._module
        if module is None or module["offline"] or not module["answers_reads"]:
            return
        module["offline"] = True
        self._buspro.logger.warning(
            "HDL module %s.%s is not answering; marking its channels unavailable",
            self._device_address[0], self._device_address[1],
        )
        for dev in list(module["devices"]):
            dev._call_device_updated()
        _request_module_read(self._buspro, self._device_address, slow=True)

    # ----- confirmed level -------------------------------------------------

    def _mark_confirmed(self) -> None:
        """The current level came from the bus."""
        self._confirmed_level = self._brightness
        module = self._module
        if module is not None and module["needs_read"] and all(
            d._got_initial_status for d in module["devices"]
        ):
            module["needs_read"] = False  # every channel has a real level

    def _revert_to_confirmed(self) -> None:
        """Undo an optimistic level that the module never confirmed."""
        self._brightness = self._confirmed_level or 0


    @property
    def name(self) -> str:
        """Return the device name."""
        return self._name

    def register_telegram_received_cb(self, telegram_received_cb, postfix=None) -> None:
        """Register a per-device telegram callback."""
        self._buspro.register_telegram_received_device_cb(
            telegram_received_cb, self._device_address, postfix
        )

    def unregister_telegram_received_cb(self, telegram_received_cb, postfix=None) -> None:
        """Unregister a per-device telegram callback."""
        self._buspro.unregister_telegram_received_device_cb(
            telegram_received_cb, self._device_address, postfix
        )

    def register_device_updated_cb(self, device_updated_cb) -> None:
        """Register a callback fired when the device state changes."""
        self.device_updated_cbs.append(device_updated_cb)

    def unregister_device_updated_cb(self, device_updated_cb) -> None:
        """Unregister a device-updated callback."""
        if device_updated_cb in self.device_updated_cbs:
            self.device_updated_cbs.remove(device_updated_cb)

    async def _device_updated(self) -> None:
        for cb in list(self.device_updated_cbs):
            try:
                await cb(self)
            except Exception:  # noqa: BLE001
                self._buspro.logger.exception("Device-updated callback failed")

    async def _send_telegram(self, telegram) -> None:
        if self._buspro.network_interface is None:
            return
        await self._buspro.network_interface.send_telegram(telegram)

    # Seconds to wait for a SingleChannelControlResponse before resending a
    # channel command once. 0.8 s (the reference integration's value) is
    # shorter than the reply latency of a busy RS485 bus, so under rapid
    # clicking every command was being sent twice -- which made the bus
    # busier, which made more replies late, which caused more resends. 1.5 s
    # still recovers a genuinely lost frame, without feeding that loop.
    _ACK_TIMEOUT = 1.5
    # How long a command counts as "in flight" for stale-reply filtering.
    _INFLIGHT_WINDOW = 3.0

    # ----- in-flight command tracking ------------------------------------
    # Every click sends a command and the module answers each one in turn.
    # Clicking ON-OFF-ON quickly means the ON reply for the FIRST click can
    # arrive after the user already asked for OFF; applying it flipped the
    # entity back to ON, then OFF, then ON again as the replies trickled in,
    # so Home Assistant always looked a step or two behind the bus. Replies
    # are now matched to the commands they answer, and only the reply to
    # the LATEST click (or a change nobody here asked for) is applied.

    @staticmethod
    def _same_level(a, b) -> bool:
        """True if two channel levels mean the same thing."""
        if a == b:
            return True
        # Some relay firmware reports 255 for "on" whatever was sent.
        return bool(a) and bool(b) and (a == 255 or b == 255)

    def _note_command(self, level) -> int:
        """Record a command sent from here; returns its sequence number."""
        now = self._buspro.loop.time()
        inflight = self.__dict__.setdefault("_inflight", [])
        inflight[:] = [c for c in inflight if now - c[0] < self._INFLIGHT_WINDOW]
        self._cmd_seq = getattr(self, "_cmd_seq", 0) + 1
        inflight.append((now, level, self._cmd_seq))
        return self._cmd_seq

    def _accept_reply(self, level) -> bool:
        """Decide whether a reported channel level should be applied."""
        inflight = getattr(self, "_inflight", None)
        if not inflight:
            return not self._send_deferred(level)
        now = self._buspro.loop.time()
        inflight[:] = [c for c in inflight if now - c[0] < self._INFLIGHT_WINDOW]
        if not inflight:
            return not self._send_deferred(level)
        # Modules answer commands in the order they received them, so match
        # the reply to the OLDEST in-flight command with that level. Commands
        # before it were answered already (or their reply was lost).
        for i, (sent_at, sent, _) in enumerate(inflight):
            if self._same_level(level, sent):
                del inflight[: i + 1]
                if inflight:
                    # Newer clicks are still on their way: this is a late
                    # reply to an earlier one, so keep showing the latest.
                    _bump(self._buspro, "stale_replies_ignored")
                    return False
                self._awaiting_ack = False
                _record_latency(self._buspro, self._device_address, now - sent_at)
                # The bus is now really at this level (what a revert goes to).
                self._confirmed_level = level
                # Clicked again while this was on its way: send that now and
                # keep showing it, rather than flashing back to this level.
                return not self._send_deferred(level)
        # Not something we asked for (keypad, logic, a dimmer still ramping):
        # it is real bus state, so take it.
        inflight.clear()
        self._awaiting_ack = False
        return not self._send_deferred(level)

    # ----- one command per channel on the bus ------------------------------
    # Every click used to go straight onto the bus. Each one costs several
    # frames (command, the module's reply, keypad LED write and its reply),
    # so clicking on/off quickly on a slow bus - above all the wireless mesh -
    # built a queue; replies then came back seconds late, were taken as new
    # changes, and the switch flipped about until the backlog drained. Now a
    # channel has at most one command waiting for its reply: a click made
    # meanwhile is shown at once but held, and only the LATEST one is sent
    # when the reply arrives (or the wait times out).

    def _defer_if_busy(self, level, running_time) -> bool:
        """Hold this command if one is still waiting for its reply."""
        if not getattr(self, "_awaiting_ack", False):
            return False
        self._deferred = (level, running_time)
        _bump(self._buspro, "clicks_held")
        return True

    def _send_deferred(self, confirmed_level=None) -> bool:
        """Send the held command, if any. True if one was sent."""
        deferred = getattr(self, "_deferred", None)
        self._deferred = None
        if deferred is None:
            return False
        if confirmed_level is not None and self._same_level(
            confirmed_level, deferred[0]
        ):
            return False  # the channel is already where the last click wants
        self._awaiting_ack = False
        asyncio.ensure_future(self._set(*deferred), loop=self._buspro.loop)
        return True

    def _start_ack_watch(self, control) -> None:
        """Resend `control` once if the channel doesn't confirm in time.

        Each watch is tied to the command it was started for. Previously
        they shared one flag, so the watch of an earlier click could clear
        the flag of a later one (losing its retry) or resend an old level.
        """
        self._awaiting_ack = True
        seq = getattr(self, "_cmd_seq", 0)
        sent_at = self._buspro.loop.time()

        def _still_pending() -> bool:
            return (
                getattr(self, "_awaiting_ack", False)
                and getattr(self, "_cmd_seq", 0) == seq
            )

        async def _watch():
            await asyncio.sleep(self._ACK_TIMEOUT)
            if not _still_pending():
                return
            if self._send_deferred():
                return  # a newer click replaces the resend
            _bump(self._buspro, "resends")
            self._buspro.logger.debug(
                "No reply from %s within %ss; resending", self._device_address,
                self._ACK_TIMEOUT,
            )
            try:
                await control.send()
            except Exception:  # noqa: BLE001
                self._buspro.logger.debug(
                    "Channel command resend failed for %s", self._device_address
                )
                # Never leave the channel marked busy: every later click
                # would be held behind a reply that can no longer come.
                self._awaiting_ack = False
                self._send_deferred()
                return
            # Still nothing after the resend: ask the module what it is
            # actually doing, so Home Assistant doesn't keep showing a state
            # that never happened.
            await asyncio.sleep(self._ACK_TIMEOUT)
            if not _still_pending():
                return
            if self._send_deferred():
                return
            self._awaiting_ack = False
            getattr(self, "_inflight", []).clear()
            _bump(self._buspro, "unanswered_after_resend")
            self._call_read_current_status_of_channels()

            # If the module stays silent through that read too, the command
            # never happened: put the entity back to what the bus last
            # confirmed instead of leaving the optimistic state up forever,
            # and mark the module unresponsive until it answers again.
            await asyncio.sleep(self._ACK_TIMEOUT)
            if getattr(self, "_cmd_seq", 0) != seq:
                return  # clicked again meanwhile; that click has its own watch
            module = self._module
            if module is None:
                return
            last = module["last_heard"]
            if last is not None and last >= sent_at:
                return  # it answered something; its real state was applied
            _bump(self._buspro, "reverted_unanswered")
            self._buspro.logger.debug(
                "No answer from %s; reverting channel state", self._device_address
            )
            self._revert_to_confirmed()
            self._module_went_quiet()
            self._call_device_updated()

        asyncio.ensure_future(_watch(), loop=self._buspro.loop)

    def _call_device_updated(self) -> None:
        """Schedule device_updated callbacks on the running loop."""
        asyncio.ensure_future(self._device_updated(), loop=self._buspro.loop)

    def _call_read_current_status_of_channels(self, run_from_init: bool = False) -> None:
        """Schedule a read of the device's channel status.

        At startup (run_from_init=True) this retries every
        _CHANNEL_STATUS_RETRY_SECONDS until a real status telegram is seen
        for this channel, then stops for good -- see the module-level
        docstring above for why a single attempt isn't reliable enough.
        A scene-triggered re-read (run_from_init=False, called after this
        device's initial status is already known) stays a single attempt,
        since it's just picking up a likely change, not establishing the
        only source of truth for the entity's starting state.
        """

        async def _read():
            if run_from_init:
                # Stagger the very first attempt so a restart with many
                # channels doesn't fire them all in the same UDP burst.
                await asyncio.sleep(3 + random.uniform(0, 2))
                attempt = 0
                while not self._got_initial_status:
                    attempt += 1
                    reader = _ReadStatusOfChannels(self._buspro)
                    reader.subnet_id, reader.device_id = self._device_address
                    try:
                        await reader.send()
                    except Exception:  # noqa: BLE001
                        self._buspro.logger.debug(
                            "Startup status read failed for %s",
                            self._device_address,
                        )
                    await asyncio.sleep(startup_retry_delay(attempt))
            else:
                # One read answers every channel on the module, so when a
                # scene reply makes all N channels of a module ask at once,
                # only the first actually goes on the bus.
                recent = self._buspro.__dict__.setdefault("_recent_channel_reads", {})
                now = self._buspro.loop.time()
                last = recent.get(tuple(self._device_address))
                if last is not None and now - last < _READ_DEDUPE_SECONDS:
                    return
                recent[tuple(self._device_address)] = now
                reader = _ReadStatusOfChannels(self._buspro)
                reader.subnet_id, reader.device_id = self._device_address
                try:
                    await reader.send()
                except Exception:  # noqa: BLE001
                    self._buspro.logger.debug(
                        "Status read failed for %s", self._device_address
                    )

        asyncio.ensure_future(_read(), loop=self._buspro.loop)
