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
# and fall back to a slow retry. Some hardware (the Buspro wireless relay
# panels) never answers ReadStatusOfChannels at all, and every channel of it
# used to re-ask every 20 seconds forever - constant traffic on the wireless
# mesh for nothing. A device that answers is unaffected: the loop still
# stops the moment a real status arrives, and a device that was merely
# offline at boot is still picked up by the slow retry.
_STARTUP_FAST_RETRIES = 6
_STARTUP_SLOW_RETRY_SECONDS = 600
# Identical on-demand channel reads to one module within this window are
# collapsed into one (see _call_read_current_status_of_channels).
_READ_DEDUPE_SECONDS = 0.5


def _stats(buspro) -> dict:
    """Per-bus command timing, shown in the integration's diagnostics."""
    stats = getattr(buspro, "bus_stats", None)
    if stats is None:
        stats = {
            "commands_confirmed": 0,
            "resends": 0,
            "unanswered_after_resend": 0,
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
            return True
        now = self._buspro.loop.time()
        inflight[:] = [c for c in inflight if now - c[0] < self._INFLIGHT_WINDOW]
        if not inflight:
            return True
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
                return True
        # Not something we asked for (keypad, logic, a dimmer still ramping):
        # it is real bus state, so take it.
        inflight.clear()
        self._awaiting_ack = False
        return True

    def _start_ack_watch(self, control) -> None:
        """Resend `control` once if the channel doesn't confirm in time.

        Each watch is tied to the command it was started for. Previously
        they shared one flag, so the watch of an earlier click could clear
        the flag of a later one (losing its retry) or resend an old level.
        """
        self._awaiting_ack = True
        seq = getattr(self, "_cmd_seq", 0)

        def _still_pending() -> bool:
            return (
                getattr(self, "_awaiting_ack", False)
                and getattr(self, "_cmd_seq", 0) == seq
            )

        async def _watch():
            await asyncio.sleep(self._ACK_TIMEOUT)
            if not _still_pending():
                return
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
                return
            # Still nothing after the resend: ask the module what it is
            # actually doing, so Home Assistant doesn't keep showing a state
            # that never happened.
            await asyncio.sleep(self._ACK_TIMEOUT)
            if not _still_pending():
                return
            self._awaiting_ack = False
            getattr(self, "_inflight", []).clear()
            _bump(self._buspro, "unanswered_after_resend")
            self._call_read_current_status_of_channels()

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
