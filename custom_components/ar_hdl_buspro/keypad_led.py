"""Keep HDL keypad button LEDs in step with relays switched from Home Assistant.

An HDL keypad's button LED shows the BUTTON's own on/off state, not the
load's. When a button is pressed the keypad flips its state and switches the
relay, so they agree. When anything else switches that relay - Home
Assistant, another keypad, logic - the keypad is never told, and its LED
stays where it was.

HDL's fix is the panel-control command "Control button status": operate code
0xE3D8 with payload [17, button, 1 = on / 0 = off], sent to the keypad. It
sets the button's LED (and state) without running the button's targets.
Confirmed on a Buspro wireless panel (0x13C3 at 2.1).

Links are stored per relay/light entity as text, e.g. "2.1:2, 1.50:4"
(keypad subnet.device:button), filled in automatically by the bus scan from
the keypads' own button programming (see discovery.py) and editable by hand.
"""

# Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).
# All rights reserved. Proprietary and confidential.
# Licensed software - see LICENSE in the repository root.
from __future__ import annotations

import asyncio
import logging
import re

from .pybuspro.devices.control import _GenericControl

_LOGGER = logging.getLogger(__name__)

# Panel control (0xE3D8) and its "Control button status" sub-type.
OP_PANEL_CONTROL = b"\xe3\xd8"
PANEL_CONTROL_BUTTON_STATUS = 17

# A command from Home Assistant updates linked keypad LEDs this long after
# the LAST click, not after every click: clicking a light on/off quickly
# used to put 1 + (number of linked buttons) telegrams on the bus per click,
# all competing with the relay commands themselves.
LED_DEBOUNCE_SECONDS = 0.3

# Button-target type meaning "single channel control" in a keypad's
# programming (0xE001 reply byte 2), e.g. [2, 1, 89, 2, 1, 2, 100, 0, 0] =
# button 2, target 1: single channel control of 2.1 channel 2 at 100%.
TARGET_TYPE_SINGLE_CHANNEL = 89

_LINK_RE = re.compile(r"(?<!\d)(\d{1,3})\s*[.\-/]\s*(\d{1,3})\s*[:#]\s*(\d{1,3})(?!\d)")


def parse_keypad_leds(value) -> list[tuple[int, int, int]]:
    """'2.1:2, 1.50:4' -> [(2, 1, 2), (1, 50, 4)]; bad tokens are ignored."""
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        value = ", ".join(str(v) for v in value)
    links: list[tuple[int, int, int]] = []
    for match in _LINK_RE.finditer(str(value)):
        subnet, device, button = (int(g) for g in match.groups())
        if subnet > 255 or device > 255 or not 1 <= button <= 255:
            continue
        link = (subnet, device, button)
        if link not in links:
            links.append(link)
    return links


def format_keypad_leds(links) -> str:
    """[(2, 1, 2), (1, 50, 4)] -> '1.50:4, 2.1:2' (sorted, deduplicated)."""
    return ", ".join(f"{s}.{d}:{b}" for s, d, b in sorted(set(links)))


# Keypads confirm "Control button status" with 0xE3D9 [17, button, status].
OP_PANEL_CONTROL_RESPONSE = b"\xe3\xd9"
# Keypads that have been SEEN sending that confirmation get a confirmed
# write: wait this long for it, and send at most this many times. Keypads
# never seen confirming (not every model does) are never waited on -- a
# retry loop against a keypad that never answers just triples the LED
# traffic on every click, which is what made 5.0.14 feel slower. They get
# one write, then one repeat once things have settled.
LED_ACK_TIMEOUT = 0.6
LED_ATTEMPTS = 2
LED_REPEAT_SECONDS = 1.0

# Every LED write on one bus goes through one paced queue (_LedWriter), at
# most one frame per LED_FRAME_GAP. An "all off" from Home Assistant used to
# fire every entity's LED writes - and later all their repeats - in the same
# instant, on top of the relay commands themselves; the gateway feeds a
# 9600-baud bus and dropped some, which left LEDs lit over dark lights.
LED_FRAME_GAP = 0.05

# When a relay/light changes on the BUS (a keypad press, another keypad,
# logic, a timer), the linked LEDs are set this long after the LAST change,
# once per delay, each time with the load's live state. Writing at the same
# instant as the relay's reply loses the race: the pressed keypad is still
# applying its own LED state for the press and overwrites ours, so the LED
# could stay on with the light off. Same settle pattern as the HA keypad
# buttons (KEYPAD_PRESS_LED_DELAYS in switch.py).
LED_FOLLOW_DELAYS = (0.5, 1.5)


def _op_bytes(op) -> bytes | None:
    value = getattr(op, "value", op)
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, (list, tuple)):
        return bytes(value)
    return None


class _LedAckRouter:
    """One per bus client: routes 0xE3D9 confirmations to waiting writes."""

    def __init__(self) -> None:
        self.waiting: dict[tuple[int, int, int], tuple[bool, asyncio.Future]] = {}
        # Keypads (subnet, device) seen confirming a button-status write.
        self.confirmers: set[tuple[int, int]] = set()

    def handle(self, telegram) -> None:
        if _op_bytes(getattr(telegram, "operate_code", None)) != OP_PANEL_CONTROL_RESPONSE:
            return
        payload = list(getattr(telegram, "payload", None) or [])
        if len(payload) < 2 or payload[0] != PANEL_CONTROL_BUTTON_STATUS:
            return
        source = getattr(telegram, "source_address", None)
        if source is None:
            return
        self.confirmers.add((int(source[0]), int(source[1])))
        key = (int(source[0]), int(source[1]), int(payload[1]))
        entry = self.waiting.get(key)
        if entry is None:
            return
        expected, fut = entry
        if len(payload) >= 3 and bool(payload[2]) != expected:
            return  # confirms a different state; keep waiting
        if not fut.done():
            fut.set_result(True)


def _router(hdl) -> _LedAckRouter:
    router = getattr(hdl, "_led_ack_router", None)
    if router is None:
        router = _LedAckRouter()
        hdl._led_ack_router = router  # noqa: SLF001
        handlers = getattr(hdl, "virtual_handlers", None)
        if handlers is not None:
            handlers.append(router.handle)
    return router


class _LedWriter:
    """One per bus client: the only place keypad LED frames are sent from.

    * Paced: frames go out one at a time, LED_FRAME_GAP apart, so a burst of
      LED updates can't swamp the gateway.
    * Latest state wins: each button is queued once, and what is sent is its
      desired state at the moment it goes out - never a stale earlier one.
    * Shared buttons: when several loads are linked to one button, its LED is
      on while ANY of them is on.
    * Verified: a button on a keypad that never confirms is sent once more,
      LED_REPEAT_SECONDS after the queue last went quiet, with its state then.
      One repeat per burst, however many loads asked.
    """

    def __init__(self, hdl, router: _LedAckRouter) -> None:
        self._hdl = hdl
        self._router = router
        self.desired: dict[tuple[int, int, int], dict[int, bool]] = {}
        self._queue: dict[tuple[int, int, int], None] = {}
        self._waiters: dict[tuple[int, int, int], list[asyncio.Future]] = {}
        self._verify: dict[tuple[int, int, int], None] = {}
        self._verify_timer: asyncio.TimerHandle | None = None
        self._task: asyncio.Task | None = None

    def state(self, link) -> bool:
        return any(self.desired.get(link, {}).values())

    async def write(self, links, owner: int, on: bool, verify: bool) -> None:
        """Set `owner`'s view of these buttons and wait until they are sent."""
        loop = asyncio.get_running_loop()
        futs = []
        for link in links:
            self.desired.setdefault(link, {})[owner] = on
            self._queue[link] = None
            fut = loop.create_future()
            self._waiters.setdefault(link, []).append(fut)
            futs.append(fut)
            if verify and link[:2] not in self._router.confirmers:
                self._verify[link] = None
        self._kick()
        if futs:
            await asyncio.gather(*futs, return_exceptions=True)

    def _kick(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._drain())

    async def _drain(self) -> None:
        if self._verify_timer is not None:
            self._verify_timer.cancel()
            self._verify_timer = None
        first = True
        while self._queue:
            if not first:
                await asyncio.sleep(LED_FRAME_GAP)
            first = False
            link = next(iter(self._queue))
            del self._queue[link]
            try:
                await self._send(link, self.state(link))
            finally:
                for fut in self._waiters.pop(link, []):
                    if not fut.done():
                        fut.set_result(None)
        if self._verify:
            loop = asyncio.get_running_loop()
            self._verify_timer = loop.call_later(LED_REPEAT_SECONDS, self._repeat)

    def _repeat(self) -> None:
        self._verify_timer = None
        links, self._verify = list(self._verify), {}
        for link in links:
            self._queue[link] = None
        if links:
            self._kick()

    async def _send(self, link, on: bool) -> None:
        """One button's LED; wait for confirmation only if its keypad gives one."""
        subnet, device, button = link
        if (subnet, device) not in self._router.confirmers:
            await self._frame(link, on)
            return
        loop = asyncio.get_running_loop()
        for attempt in range(1, LED_ATTEMPTS + 1):
            fut = loop.create_future()
            self._router.waiting[link] = (on, fut)
            try:
                if not await self._frame(link, on):
                    return
                await asyncio.wait_for(fut, LED_ACK_TIMEOUT)
                return
            except asyncio.TimeoutError:
                _LOGGER.debug(
                    "Keypad %s.%s button %s did not confirm LED %s (attempt %s/%s)",
                    subnet, device, button, "on" if on else "off",
                    attempt, LED_ATTEMPTS,
                )
                if link in self._queue:
                    return  # a newer state is queued; send that instead
            finally:
                entry = self._router.waiting.get(link)
                if entry is not None and entry[1] is fut:
                    del self._router.waiting[link]

    async def _frame(self, link, on: bool) -> bool:
        subnet, device, button = link
        ctrl = _GenericControl(self._hdl)
        ctrl.subnet_id, ctrl.device_id = subnet, device
        ctrl.operate_code = OP_PANEL_CONTROL
        ctrl.payload = [PANEL_CONTROL_BUTTON_STATUS, button, 1 if on else 0]
        try:
            await ctrl.send()
            return True
        except Exception as err:  # noqa: BLE001 - LED sync must never break control
            _LOGGER.debug(
                "Keypad LED sync to %s.%s button %s failed: %s",
                subnet, device, button, err,
            )
            return False


def _writer(hdl) -> _LedWriter:
    writer = getattr(hdl, "_led_writer", None)
    if writer is None:
        writer = _LedWriter(hdl, _router(hdl))
        hdl._led_writer = writer  # noqa: SLF001
    return writer


class KeypadLedSync:
    """Send 'Control button status' to every keypad button linked to a load."""

    def __init__(self, hdl, value) -> None:
        self._hdl = hdl
        self._links = parse_keypad_leds(value)
        self._last: bool | None = None
        self._pending: asyncio.TimerHandle | None = None
        # Last load (relay/light) state seen, and the settle timers that set
        # the LEDs after a change on the bus (see follow()).
        self._load: bool | None = None
        self._follow: list[asyncio.TimerHandle] = []
        if self._links:
            _router(hdl)

    @property
    def active(self) -> bool:
        return bool(self._links)

    async def push(self, on: bool, force: bool = False, repeat: bool = True) -> None:
        """Set the linked buttons' LEDs to `on`.

        Without `force` nothing is sent if the LEDs were already set to this
        state, so a relay's own status replies don't repeat the message.
        Home Assistant commands force it, which also repairs a keypad that
        drifted out of step for any other reason.
        """
        if not self._links:
            return
        on = bool(on)
        if not force and self._pending is not None:
            return  # a Home Assistant command is about to set them anyway
        if not force and self._last is on:
            return
        self._last = on
        if getattr(self._hdl, "network_interface", None) is None:
            return
        # All LED frames on this bus share one paced queue; see _LedWriter.
        await _writer(self._hdl).write(
            self._links, id(self), on, verify=force and repeat
        )

    def push_soon(self, state) -> None:
        """Force the LEDs once clicking has settled (debounced).

        `state` is the on/off value, or a zero-argument callable returning
        it; a callable is read when the push actually fires, so the LEDs get
        the load's state at that moment rather than at the first click.
        """
        if not self._links:
            return
        loop = getattr(self._hdl, "loop", None)
        if loop is None:
            return
        if self._pending is not None:
            self._pending.cancel()

        def _fire() -> None:
            self._pending = None
            on = bool(state() if callable(state) else state)
            self._load = on
            loop.create_task(self.push(on, force=True))

        self._pending = loop.call_later(LED_DEBOUNCE_SECONDS, _fire)

    def follow(self, state) -> None:
        """The load changed on the bus: set the LEDs once it has settled.

        Called on every status update of the relay/light. Nothing is sent if
        the load is unchanged and the LEDs were already set to it (status
        reads repeat the same state). Otherwise the LEDs are forced to the
        load's LIVE state at each LED_FOLLOW_DELAYS mark after the last
        change, so the final word is always the relay's real state - never
        the keypad's own LED handling, and never a stale earlier click.
        """
        if not self._links:
            return
        on = bool(state() if callable(state) else state)
        if self._pending is not None:
            self._load = on
            return  # a Home Assistant command is about to set them anyway
        if on is self._load and on is self._last and not self._follow:
            return
        self._load = on
        loop = getattr(self._hdl, "loop", None)
        if loop is None:
            return
        for handle in self._follow:
            handle.cancel()
        self._follow = []
        last = len(LED_FOLLOW_DELAYS) - 1

        def _fire(index: int) -> None:
            if index == last:
                self._follow = []
            if self._pending is not None:
                return  # a Home Assistant command will set them
            now = bool(state() if callable(state) else state)
            self._load = now
            loop.create_task(self.push(now, force=True, repeat=False))

        for index, delay in enumerate(LED_FOLLOW_DELAYS):
            self._follow.append(loop.call_later(delay, _fire, index))
