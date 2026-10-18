"""Is the HDL IP gateway actually there? (no Home Assistant imports)

UDP has no connection. Opening the socket succeeds whether the gateway is
plugged in, unplugged or on fire, so "socket open" says nothing about the
gateway. Up to this release that was the only availability check: a gateway
unplugged for days kept every entity operable in Home Assistant, a press
showed ON, and nothing on site changed.

The only real proof of life is a frame FROM the gateway's IP. A live site
produces them constantly (status replies, keypad presses, sensor broadcasts,
the 60 s module re-sync), so normally this watchdog never sends anything.
When the bus has been quiet for SILENCE_SECONDS it asks one or two known
devices "who are you" (0x000E - every HDL device answers it, and the gateway
relays the reply from its own IP). Only after FAILURES_TO_OFFLINE probes in a
row go unanswered is the gateway declared unreachable; the very next frame
from it brings it back at once.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable

from ..core.telegram import Telegram
from ..helpers.telegram_helper import HA_SOURCE_ADDRESS

# 0x000E "read device information": answered by every HDL Buspro device and
# by the IP gateway itself. Same poke gateway auto-detection uses.
_PROBE_OPERATE_CODE = b"\x00\x0e"


class GatewayLiveness:
    """Track frames from the gateway and probe it when the bus goes quiet."""

    TICK_SECONDS = 5.0
    # No frame for this long -> probe. Healthy sites hear traffic far more
    # often than this (the module re-sync alone is once a minute).
    SILENCE_SECONDS = 75.0
    # A probe unanswered after this long counts as one failure.
    REPLY_WINDOW_SECONDS = 10.0
    # Consecutive unanswered probes before the gateway is declared down, so
    # one lost UDP frame never greys out a whole site.
    FAILURES_TO_OFFLINE = 2
    # First check this soon after connecting, so a gateway that is already
    # dead when Home Assistant starts is reported within ~40 s, not ~2 min.
    STARTUP_GRACE_SECONDS = 20.0
    # While down, probe this often (any frame at all brings it back sooner).
    OFFLINE_RETRY_SECONDS = 30.0
    # Devices asked per probe (rotating through the known ones).
    PROBE_TARGETS = 2

    def __init__(self, buspro, on_change: Callable[[bool], None]) -> None:
        self._buspro = buspro
        self._on_change = on_change
        self.reachable = True
        self._last_rx: float | None = None  # real time of the last frame
        self._quiet_since: float = 0.0  # silence is measured from here
        self._probe_at: float | None = None
        self._last_probe: float | None = None
        self._failures = 0
        self._cursor = 0
        self._task: asyncio.Task | None = None
        # Devices heard replying through this gateway: best probe targets.
        self._heard: dict[tuple, float] = {}

    # ----- lifecycle --------------------------------------------------------

    def start(self) -> None:
        """Begin watching. Assumes reachable until the first check says not."""
        self.stop()
        now = self._buspro.loop.time()
        # Backdate so the first probe falls due after STARTUP_GRACE_SECONDS.
        self._quiet_since = now - self.SILENCE_SECONDS + self.STARTUP_GRACE_SECONDS
        self._probe_at = None
        self._failures = 0
        self._task = asyncio.ensure_future(self._run(), loop=self._buspro.loop)

    def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None

    # ----- input ------------------------------------------------------------

    def frame_received(self, telegram) -> None:
        """A valid frame arrived from the gateway's IP (event loop)."""
        now = self._buspro.loop.time()
        self._last_rx = now
        self._quiet_since = now
        self._probe_at = None
        self._failures = 0
        source = getattr(telegram, "source_address", None)
        if source is not None:
            source = tuple(source)
            if source != tuple(HA_SOURCE_ADDRESS):
                self._heard[source] = now
                if len(self._heard) > 64:
                    oldest = min(self._heard, key=self._heard.get)
                    del self._heard[oldest]
        if not self.reachable:
            self.reachable = True
            self._buspro.logger.info("HDL gateway is answering again")
            self._on_change(True)

    # ----- watchdog -----------------------------------------------------------

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.TICK_SECONDS)
            try:
                await self.check()
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - never kill the watchdog
                self._buspro.logger.debug("Gateway liveness check failed: %s", err)

    def _send_permitted(self) -> bool:
        ni = self._buspro.network_interface
        if ni is None:
            return False
        check = getattr(ni, "_send_permitted", None)
        return True if check is None else bool(check())

    async def check(self) -> None:
        """One watchdog step (public so tests can drive it)."""
        if not self._send_permitted():
            # Unlicensed: nothing goes out, so silence proves nothing. Entities
            # are already unavailable for the licence; leave this state alone.
            self._probe_at = None
            return
        now = self._buspro.loop.time()

        if self._probe_at is not None:
            if now - self._probe_at < self.REPLY_WINDOW_SECONDS:
                return  # still waiting for an answer
            self._probe_at = None
            self._failures += 1
            if self.reachable and self._failures >= self.FAILURES_TO_OFFLINE:
                self.reachable = False
                self._buspro.logger.warning(
                    "HDL gateway %s is not answering (%s); marking its "
                    "entities unavailable",
                    self._gateway_label(),
                    "nothing received since it was connected"
                    if self._last_rx is None
                    else f"nothing received for {now - self._last_rx:.0f} s",
                )
                self._on_change(False)

        if not self.reachable:
            due = (
                self._last_probe is None
                or now - self._last_probe >= self.OFFLINE_RETRY_SECONDS
            )
        elif self._failures:
            due = True  # confirm a miss straight away
        else:
            due = now - self._quiet_since >= self.SILENCE_SECONDS
        if due:
            await self._probe(now)

    def _gateway_label(self) -> str:
        addr = getattr(self._buspro, "gateway_address_send_receive", None)
        try:
            return f"{addr[0][0]}:{addr[0][1]}"
        except (TypeError, IndexError):
            return "?"

    def _targets(self) -> list[tuple]:
        """Known device addresses, ones heard through this gateway first."""
        ordered: list[tuple] = sorted(self._heard, key=self._heard.get, reverse=True)
        known: list[tuple] = []
        for addr in getattr(self._buspro, "__dict__", {}).get("_module_states", {}):
            known.append(tuple(addr))
        for cb in getattr(self._buspro, "_telegram_received_cbs", ()):
            addr = cb.get("device_address")
            if addr is not None:
                known.append(tuple(addr))
        for addr in known:
            if addr not in ordered:
                ordered.append(addr)
        if not ordered:
            return []
        start = self._cursor % len(ordered)
        self._cursor = start + 1
        rotated = ordered[start:] + ordered[:start]
        return rotated[: self.PROBE_TARGETS]

    async def _probe(self, now: float) -> None:
        self._probe_at = now
        self._last_probe = now
        ni = self._buspro.network_interface
        targets = self._targets() or [(255, 255)]  # nothing configured yet
        for target in targets:
            telegram = Telegram()
            telegram.target_address = target
            telegram.operate_code = _PROBE_OPERATE_CODE
            telegram.payload = []
            try:
                await ni.send_telegram(telegram)
            except Exception as err:  # noqa: BLE001
                self._buspro.logger.debug("Liveness probe to %s failed: %s", target, err)

    def diagnostics(self) -> dict:
        now = self._buspro.loop.time()
        return {
            "reachable": self.reachable,
            "seconds_since_last_frame": (
                round(now - self._last_rx, 1) if self._last_rx is not None else None
            ),
            "unanswered_probes": self._failures,
            "probe_pending": self._probe_at is not None,
        }
