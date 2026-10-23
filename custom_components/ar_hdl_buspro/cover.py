"""Cover (curtain) platform for the AR HDL BUSPRO integration.

Two hardware styles are supported:

- curtain_module: a real HDL curtain module (MW02 / MWM70B family) driven via
  CurtainSwitchControl (0xE3E0) with actions stop=0 / open=1 / close=2, status
  read via ReadStatusOfCurtainSwitch (0xE3E2). Two sub-modes:

  * native percentage (HDL curtain / roller motors: MWM70B, MVSM45B,
    MWM65B.20, MVSM35B.20 ...): the motor takes a target percentage and
    reports its real position. Same opcodes, first byte 16 + curtain number
    (17 for curtain 1): 0xE3E0 [17, pct] moves to pct, 0xE3E2 [17] reads it,
    and the motor answers / broadcasts 0xE3E1 or 0xE3E3 [17, pct] (0-100;
    221 = track length unknown, 238 = measuring the track). Detected per
    entity at start-up by asking the motor, so existing installs need no
    re-import. 0 = closed, 100 = open, the same as Home Assistant.
  * time based (MW02 / MW04 relay-style controllers, which have no
    percentage): SET_POSITION is estimated from the configured travel time:
    a full open/close still just fires the command and lets the module's own
    limit switches decide when to stop, while a partial position drives in
    the right direction and sends an explicit stop after a
    proportionally-scaled delay. This is a client-side estimate -- it can
    drift if the curtain is moved by something this integration doesn't see.
- relay_pair: the very common install style where a curtain motor hangs off
  two interlocked relay channels (one drives open, one drives close). We pulse
  the direction channel for the configured travel time, then release it, and
  track position optimistically.
"""

# Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).
# All rights reserved. Proprietary and confidential.
# Licensed software - see LICENSE in the repository root. Unauthorised
# copying, redistribution, modification, or circumvention of the licence
# check in licensing.py is prohibited.
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ARHDLData
from .const import (
    CONF_CLOSE_CHANNEL,
    CONF_COVER_MODE,
    CONF_CURTAIN_NUMBER,
    CONF_DEVICE_ID,
    CONF_DEVICE_TYPE,
    CONF_DEVICES,
    CONF_NAME,
    CONF_OPEN_CHANNEL,
    CONF_RECALIBRATE_BEFORE_REPOSITION,
    CONF_SUBNET_ID,
    CONF_TRAVEL_TIME,
    COVER_MODE_CURTAIN_MODULE,
    COVER_MODE_RELAY_PAIR,
    DEFAULT_TRAVEL_TIME,
    DEVICE_TYPE_COVER,
    DOMAIN,
)
from .entity import ARHDLBaseEntity, build_device_info, build_unique_id
from .gateway import ARHDLGateway
from .pybuspro.devices.device import Device as PyBusproDevice
from .pybuspro.helpers.enums import OperateCode

_LOGGER = logging.getLogger(__name__)

# CurtainSwitchControl actions
_ACTION_STOP = 0
_ACTION_OPEN = 1
_ACTION_CLOSE = 2

# Native percentage motors: the curtain byte is 16 + curtain number.
_PCT_OFFSET = 16
# Motors that take a third byte on every curtain command, as the HDL Setup
# Tool sends it: [16 + n, pct, 0] for a position and [n, action, 1] for
# open / close / stop. Without it the motor reads whatever follows in the
# frame as that byte (seen on 0x25E8: some positions worked, 25% and 0%
# stopped at ~42%). Taken from the motor's own device type on the bus.
_LONG_FORM_TYPE_CODES = frozenset({0x25E8})  # HDL-MWM65B.20
# Offset of the source device type in a raw HDL frame (udp_data).
_UDP_DEVICE_TYPE_INDEX = 19
# Position replies above 100 that are states, not positions.
_PCT_TRACK_UNKNOWN = 221  # "fail to get the length of the track, reset the module"
_PCT_TRACK_MEASURING = 238  # "busy getting the length of the track"
# How long to wait for the motor to answer the start-up position read.
_PCT_PROBE_WAIT = 2.0
# While a native motor moves: read its position this often, call it stopped
# once the position has not changed for this many reads, and give up after
# _PCT_POLL_MAX seconds (a dead motor must not poll forever).
_PCT_POLL_INTERVAL = 1.0
_PCT_POLL_STABLE_READS = 3
_PCT_POLL_MAX = 180.0
# Some motors are slow to start, or only update their reported position at
# the end of travel. An unchanged position doesn't count as "stopped" until
# it has moved at least once, or this long has passed.
_PCT_POLL_GRACE = 20.0
# Read the position again this long after a move has settled (seconds after
# the poll ends), to catch a final position the motor didn't push.
_PCT_FOLLOWUP_READS = (2.0, 5.0, 10.0)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up AR HDL BUSPRO covers."""
    data: ARHDLData = hass.data[DOMAIN][entry.entry_id]
    devices = entry.options.get(CONF_DEVICES, [])

    entities: list[CoverEntity] = []
    for device_cfg in devices:
        if device_cfg.get(CONF_DEVICE_TYPE) != DEVICE_TYPE_COVER:
            continue
        mode = device_cfg.get(CONF_COVER_MODE, COVER_MODE_CURTAIN_MODULE)
        if mode == COVER_MODE_RELAY_PAIR:
            entities.append(ARHDLRelayPairCover(entry, data.gateway, device_cfg))
        else:
            entities.append(ARHDLCurtainModuleCover(entry, data.gateway, device_cfg))

    if entities:
        async_add_entities(entities)


class _ARHDLCoverBase(ARHDLBaseEntity, CoverEntity):
    """Shared base for both cover styles."""

    _attr_device_class = CoverDeviceClass.CURTAIN

    def __init__(
        self,
        entry: ConfigEntry,
        gateway: ARHDLGateway,
        device_cfg: dict[str, Any],
    ) -> None:
        """Initialize common cover state."""
        super().__init__(entry, gateway, device_cfg)
        self._subnet = int(device_cfg[CONF_SUBNET_ID])
        self._device_id = int(device_cfg[CONF_DEVICE_ID])
        self._attr_device_info = build_device_info(entry, device_cfg, gateway.device_id)
        # See light.py: a curtain module can drive several curtains that all
        # share one HA device, so each one needs its own visible name rather
        # than deferring to the (shared) device name.
        self._attr_has_entity_name = False
        self._attr_name = device_cfg.get(CONF_NAME) or f"HDL {self._subnet}.{self._device_id}"
        # A lightweight pybuspro Device just for telegram callbacks + sending.
        self._dev = PyBusproDevice(
            gateway.hdl, (self._subnet, self._device_id), device_cfg.get(CONF_NAME, "")
        )

    async def _send(self, operate_code, payload: list[int]) -> None:
        """Send a raw telegram to this device via the gateway."""
        from .pybuspro.core.telegram import Telegram

        telegram = Telegram()
        telegram.target_address = (self._subnet, self._device_id)
        telegram.operate_code = operate_code
        telegram.payload = payload
        await self._dev._send_telegram(telegram)  # noqa: SLF001 - library-internal sender


class ARHDLCurtainModuleCover(_ARHDLCoverBase):
    """Cover backed by an HDL curtain module (CurtainSwitchControl).

    Native percentage motors report their real position (see the module
    docstring) and are driven by percentage. Everything else keeps the
    travel-time estimate: `is_closed` still reflects a real bus report when
    we have one, while `current_cover_position`, `is_opening` and
    `is_closing` are local estimates, hence assumed state.
    """

    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )

    def __init__(self, entry, gateway, device_cfg) -> None:
        """Initialize the curtain-module cover."""
        super().__init__(entry, gateway, device_cfg)
        self._curtain_number = int(device_cfg.get(CONF_CURTAIN_NUMBER, 1))
        self._attr_unique_id = build_unique_id(
            entry.entry_id, device_cfg, suffix="cover"
        )
        self._travel_time = max(
            1, int(device_cfg.get(CONF_TRAVEL_TIME, DEFAULT_TRAVEL_TIME))
        )
        # See CONF_RECALIBRATE_BEFORE_REPOSITION in const.py -- off by default.
        self._recalibrate = bool(
            device_cfg.get(CONF_RECALIBRATE_BEFORE_REPOSITION, False)
        )
        # None -> unknown until the module tells us something.
        self._status: int | None = None
        # Optimistic position: 0 = closed, 100 = open. Assume closed at boot,
        # refined for real the first time we see an open/close status report.
        self._position = 0
        self._move_task: asyncio.Task | None = None
        self._moving_dir = 0  # +1 opening, -1 closing, 0 idle

        # Native percentage motor state (see module docstring). Off until the
        # motor answers a percentage read, so non-percentage modules behave
        # exactly as before.
        self._pct_byte = _PCT_OFFSET + self._curtain_number
        self._native = False
        # 2 = [17, pct]; 3 = [17, pct, 0] (the MWM65B.20 0x25E8 form, which
        # only answers the 3-byte read). Whichever read the motor answered.
        self._pct_form = 2
        self._probe_form = 2
        # Device type from the motor's own frames; decides the command form.
        self._type_code: int | None = None
        self._long_form = False
        self._native_position: int | None = None
        self._native_target: int | None = None
        self._track_state: str | None = None
        self._poll_task: asyncio.Task | None = None
        self._followup_task: asyncio.Task | None = None
        self._probe_task: asyncio.Task | None = None
        # Re-read the position after a gateway reconnect (entity.py).
        self._resync_device = self

    @property
    def device_identifier(self) -> str:
        """Identifier used in resync debug logs (entity.py)."""
        return f"{self._subnet}.{self._device_id}-curtain{self._curtain_number}"

    async def read_status(self) -> None:
        """Post-reconnect resync: read the open/close status and position."""
        await self._send(
            OperateCode.ReadStatusOfCurtainSwitch, [self._curtain_number]
        )
        await self._send_pct_read(self._pct_form)

    async def async_added_to_hass(self) -> None:
        """Register bus callback and request initial status."""
        await super().async_added_to_hass()
        self._dev.register_telegram_received_cb(self._telegram_received)
        await self._send(
            OperateCode.ReadStatusOfCurtainSwitch, [self._curtain_number]
        )
        self._probe_task = self.hass.async_create_task(self._probe_native())

    async def _send_pct_read(self, form: int) -> None:
        payload = [self._pct_byte] + ([0, 0] if form == 3 else [])
        await self._send(OperateCode.ReadStatusOfCurtainSwitch, payload)

    async def _probe_native(self) -> None:
        """Ask the module for a percentage; an answer switches on native mode.

        A curtain controller without percentage simply doesn't answer the
        [16 + n] read, and the entity stays time based. The MWM65B.20 (0x25E8)
        expects the 3-byte form, so that is tried second.
        """
        try:
            for form in (2, 3):
                self._probe_form = form
                await self._send_pct_read(form)
                await asyncio.sleep(_PCT_PROBE_WAIT)
                if self._native:
                    return
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Percentage probe failed for %s", self.device_identifier)

    def _note_device_type(self, telegram) -> None:
        """Learn the module's device type from a frame it sent."""
        udp = getattr(telegram, "udp_data", None)
        if not udp or len(udp) <= _UDP_DEVICE_TYPE_INDEX + 1:
            return
        try:
            code = (int(udp[_UDP_DEVICE_TYPE_INDEX]) << 8) | int(
                udp[_UDP_DEVICE_TYPE_INDEX + 1]
            )
        except (TypeError, ValueError):
            return
        self._type_code = code
        if code in _LONG_FORM_TYPE_CODES:
            self._long_form = True
            self._pct_form = 3
            _LOGGER.debug(
                "%s is type 0x%04X: using 3-byte curtain commands",
                self.device_identifier,
                code,
            )

    def _action_payload(self, action: int) -> list[int]:
        """Open / close / stop payload in the form this motor expects."""
        return [self._curtain_number, action] + ([1] if self._long_form else [])

    def _telegram_received(self, telegram) -> None:
        op = telegram.operate_code
        payload = telegram.payload or []
        if self._type_code is None:
            self._note_device_type(telegram)
        if op in (
            OperateCode.CurtainSwitchControlResponse,
            OperateCode.ReadStatusOfCurtainSwitchResponse,
        ):
            if len(payload) >= 2 and payload[0] == self._pct_byte:
                self._handle_pct_report(int(payload[1]))
                return
            if (
                self._native
                and len(payload) >= 2
                and payload[0] == self._curtain_number
            ):
                # Native motors: an open/close/stop report is the motor's
                # action, not proof it reached an end -- the position frames
                # carry that. Only use it to show the direction of travel.
                action = payload[1]
                self._status = action
                if action == _ACTION_STOP:
                    self._moving_dir = 0
                    self._native_target = None
                    # The motor stopped: read where it actually ended up.
                    self._schedule_followup_reads()
                elif action in (_ACTION_OPEN, _ACTION_CLOSE):
                    self._moving_dir = 1 if action == _ACTION_OPEN else -1
                    self._ensure_poll()
                self.schedule_update_ha_state()
                return
            if len(payload) >= 2 and payload[0] == self._curtain_number:
                self._status = payload[1]
                # A bus report of fully open/closed is ground truth -- resync
                # our estimate to it. A STOP report is ambiguous (could be
                # mid-travel) so we leave the estimated position alone.
                if self._status == _ACTION_OPEN:
                    self._position = 100
                elif self._status == _ACTION_CLOSE:
                    self._position = 0
                self.schedule_update_ha_state()

    def _cancel_move(self) -> None:
        if self._move_task and not self._move_task.done():
            self._move_task.cancel()
        self._move_task = None

    # ----- native percentage motors ------------------------------------

    def _handle_pct_report(self, value: int) -> None:
        """A [16 + n, value] frame: real position, or a track state."""
        if not self._native:
            self._native = True
            self._pct_form = 3 if self._long_form else self._probe_form
            # Any time-based move in flight no longer applies.
            self._cancel_move()
            _LOGGER.debug(
                "%s reports its own position; using native percentage",
                self.device_identifier,
            )
        if value <= 100:
            previous = self._native_position
            self._native_position = value
            self._track_state = None
            if previous is not None and value != previous and self._moving_dir == 0:
                # Moved by something else (keypad, remote, panel): show the
                # direction and follow it until it settles.
                self._moving_dir = 1 if value > previous else -1
                self._ensure_poll()
            if self._native_target is not None and value == self._native_target:
                self._moving_dir = 0
                self._native_target = None
        elif value == _PCT_TRACK_UNKNOWN:
            self._track_state = "not_measured"
        elif value == _PCT_TRACK_MEASURING:
            self._track_state = "measuring"
        else:
            return
        self.schedule_update_ha_state()

    def _ensure_poll(self) -> None:
        """Follow a native move by reading the position until it settles."""
        self._cancel_followup()
        if self._poll_task and not self._poll_task.done():
            return
        self._poll_task = self.hass.async_create_task(self._poll_native())

    async def _poll_native(self) -> None:
        start = time.monotonic()
        first = last = self._native_position
        stable = 0
        try:
            while time.monotonic() - start < _PCT_POLL_MAX:
                await asyncio.sleep(_PCT_POLL_INTERVAL)
                if self._moving_dir == 0:
                    break
                await self._send_pct_read(self._pct_form)
                if self._native_position == last:
                    moved = self._native_position != first
                    if moved or time.monotonic() - start >= _PCT_POLL_GRACE:
                        stable += 1
                        if stable >= _PCT_POLL_STABLE_READS:
                            break
                else:
                    stable = 0
                    last = self._native_position
        finally:
            if self._moving_dir != 0:
                self._moving_dir = 0
                self._native_target = None
                self.async_write_ha_state()
            self._schedule_followup_reads()

    def _cancel_poll(self) -> None:
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
        self._poll_task = None

    def _schedule_followup_reads(self) -> None:
        """Read the position a few more times after a move has settled."""
        self._cancel_followup()
        self._followup_task = self.hass.async_create_task(self._followup_reads())

    async def _followup_reads(self) -> None:
        waited = 0.0
        for at in _PCT_FOLLOWUP_READS:
            await asyncio.sleep(at - waited)
            waited = at
            await self._send_pct_read(self._pct_form)

    def _cancel_followup(self) -> None:
        if self._followup_task and not self._followup_task.done():
            self._followup_task.cancel()
        self._followup_task = None

    async def _native_move(self, target: int) -> None:
        """Send the motor to `target` percent; it stops there by itself."""
        self._cancel_move()
        payload = [self._pct_byte, target] + ([0] if self._pct_form == 3 else [])
        await self._send(OperateCode.CurtainSwitchControl, payload)
        self._native_target = target
        current = self._native_position
        if current is None or target != current:
            self._moving_dir = 1 if current is None or target > current else -1
            self._ensure_poll()
        self.async_write_ha_state()

    async def _native_action(self, action: int, target: int | None) -> None:
        """Open / close / stop on a native motor, then follow the position."""
        self._cancel_move()
        await self._send(
            OperateCode.CurtainSwitchControl, self._action_payload(action)
        )
        self._status = action
        self._native_target = target
        if action == _ACTION_STOP:
            self._moving_dir = 0
            self._cancel_poll()
            await self._send_pct_read(self._pct_form)
            self._schedule_followup_reads()
        else:
            self._moving_dir = 1 if action == _ACTION_OPEN else -1
            self._ensure_poll()
        self.async_write_ha_state()

    # ----- state ---------------------------------------------------------

    @property
    def assumed_state(self) -> bool:
        """Native motors report their position; the rest is an estimate."""
        return not self._native

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Show when a native motor has no usable track length."""
        if self._native and self._track_state:
            return {"track": self._track_state}
        return None

    @property
    def is_closed(self) -> bool | None:
        """Return True when the module reports closed."""
        if self._native:
            if self._native_position is None:
                return None
            return self._native_position == 0
        if self._status is None:
            return None
        return self._status == _ACTION_CLOSE

    @property
    def is_opening(self) -> bool:
        """Return True while an open move is in progress."""
        return self._moving_dir > 0

    @property
    def is_closing(self) -> bool:
        """Return True while a close move is in progress."""
        return self._moving_dir < 0

    @property
    def current_cover_position(self) -> int | None:
        """Return the position (0 closed, 100 open): real, or estimated."""
        if self._native:
            return self._native_position
        return self._position

    async def _start_move(self, action: int, target: int) -> None:
        """Fire `action` on the wire, then track/estimate position toward `target`."""
        self._cancel_move()
        await self._send(
            OperateCode.CurtainSwitchControl, self._action_payload(action)
        )
        self._status = action
        self._moving_dir = 1 if action == _ACTION_OPEN else -1
        self.async_write_ha_state()
        self._move_task = self.hass.async_create_task(self._track_move(target))

    async def _track_move(self, target: int) -> None:
        """Interpolate position while travelling; stop the module at a partial target."""
        direction = self._moving_dir
        start_pos = self._position
        distance = abs(target - start_pos)
        duration = self._travel_time * distance / 100
        start = time.monotonic()
        try:
            while duration > 0:
                await asyncio.sleep(0.25)
                elapsed = time.monotonic() - start
                progress = min(1.0, elapsed / duration)
                self._position = round(
                    start_pos + direction * distance * progress
                )
                self.async_write_ha_state()
                if progress >= 1.0:
                    break
            self._position = target
            if target not in (0, 100):
                # Partial position -- the module has no idea it should stop
                # here on its own, so tell it explicitly. Full open/close is
                # left to the module's own limit switches, same as before.
                await self._send(
                    OperateCode.CurtainSwitchControl,
                    self._action_payload(_ACTION_STOP),
                )
                self._status = _ACTION_STOP
        except asyncio.CancelledError:
            # Stopped mid-travel; keep the interpolated position.
            raise
        finally:
            self._moving_dir = 0
            self.async_write_ha_state()

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the curtain."""
        if self._native:
            await self._native_action(_ACTION_OPEN, 100)
            return
        await self._start_move(_ACTION_OPEN, 100)

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the curtain."""
        if self._native:
            await self._native_action(_ACTION_CLOSE, 0)
            return
        await self._start_move(_ACTION_CLOSE, 0)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Travel to a specific position (real on native motors, else estimated)."""
        target = max(0, min(100, int(kwargs.get("position", 0))))
        if self._native:
            await self._native_move(target)
            return
        if target == self._position:
            return
        if self._recalibrate and self._position not in (0, 100):
            await self._recalibrating_move_to(target)
            return
        action = _ACTION_OPEN if target > self._position else _ACTION_CLOSE
        await self._start_move(action, target)

    async def _recalibrating_move_to(self, target: int) -> None:
        """Recalibrate against whichever endpoint is closer -- fully open or
        fully closed, whichever is a shorter run from the current estimate --
        then travel to `target` from there. Only the two endpoints are a real
        reference (the module's own limit switch, or a bus-confirmed status
        report); anything in between is our estimate. Used only when
        CONF_RECALIBRATE_BEFORE_REPOSITION is enabled -- see const.py and
        GitHub issue #15.
        """
        if self._position <= 50:
            anchor_action, anchor = _ACTION_CLOSE, 0
        else:
            anchor_action, anchor = _ACTION_OPEN, 100
        await self._start_move(anchor_action, anchor)
        move_task = self._move_task
        if move_task is not None:
            try:
                await move_task
            except asyncio.CancelledError:
                # Superseded by a newer command (stop / another set_position)
                # while recalibrating -- let that command own the result.
                return
        if target != anchor:
            action = _ACTION_OPEN if target > anchor else _ACTION_CLOSE
            await self._start_move(action, target)

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the curtain."""
        if self._native:
            await self._native_action(_ACTION_STOP, None)
            return
        self._cancel_move()
        await self._send(
            OperateCode.CurtainSwitchControl, self._action_payload(_ACTION_STOP)
        )
        self._status = _ACTION_STOP
        self._moving_dir = 0
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel any in-flight travel, poll or probe task."""
        self._cancel_move()
        self._cancel_poll()
        self._cancel_followup()
        if self._probe_task and not self._probe_task.done():
            self._probe_task.cancel()
        self._probe_task = None


class ARHDLRelayPairCover(_ARHDLCoverBase):
    """Cover driven by two interlocked relay channels (open / close).

    Position is tracked optimistically from travel time. The direction relay
    is energised for the required duration and then released; STOP releases
    both channels immediately.
    """

    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )
    _attr_assumed_state = True

    def __init__(self, entry, gateway, device_cfg) -> None:
        """Initialize the relay-pair cover."""
        super().__init__(entry, gateway, device_cfg)
        self._open_channel = int(device_cfg.get(CONF_OPEN_CHANNEL, 1))
        self._close_channel = int(device_cfg.get(CONF_CLOSE_CHANNEL, 2))
        self._travel_time = max(
            1, int(device_cfg.get(CONF_TRAVEL_TIME, DEFAULT_TRAVEL_TIME))
        )
        # See CONF_RECALIBRATE_BEFORE_REPOSITION in const.py -- off by default.
        # This style never gets bus confirmation of position at all (unlike
        # the curtain module, which resyncs on a full-open/closed report), so
        # it's the more exposed of the two to drift.
        self._recalibrate = bool(
            device_cfg.get(CONF_RECALIBRATE_BEFORE_REPOSITION, False)
        )
        self._attr_unique_id = build_unique_id(
            entry.entry_id, device_cfg, suffix="cover"
        )
        # Optimistic position: 0 = closed, 100 = open. Assume closed at boot.
        self._position = 0
        self._move_task: asyncio.Task | None = None
        self._moving_dir = 0  # +1 opening, -1 closing, 0 idle

    async def _set_channel(self, channel: int, on: bool) -> None:
        await self._send(
            OperateCode.SingleChannelControl, [channel, 100 if on else 0, 0, 0]
        )

    def _cancel_move(self) -> None:
        if self._move_task and not self._move_task.done():
            self._move_task.cancel()
        self._move_task = None

    async def _release_all(self) -> None:
        await self._set_channel(self._open_channel, False)
        await self._set_channel(self._close_channel, False)

    async def _move(self, direction: int, target: int) -> None:
        """Drive in `direction` (+1 open / -1 close) until `target` position."""
        channel = self._open_channel if direction > 0 else self._close_channel
        other = self._close_channel if direction > 0 else self._open_channel
        distance = abs(target - self._position)
        if distance == 0:
            return
        duration = self._travel_time * distance / 100
        # Interlock: make sure the opposite direction is released first.
        await self._set_channel(other, False)
        await self._set_channel(channel, True)
        self._moving_dir = direction
        start = time.monotonic()
        start_pos = self._position
        try:
            while True:
                await asyncio.sleep(0.25)
                elapsed = time.monotonic() - start
                progress = min(1.0, elapsed / duration)
                self._position = round(
                    start_pos + direction * distance * progress
                )
                self.async_write_ha_state()
                if progress >= 1.0:
                    break
            self._position = target
        except asyncio.CancelledError:
            # Stopped mid-travel; keep the interpolated position.
            raise
        finally:
            self._moving_dir = 0
            await self._set_channel(channel, False)
            self.async_write_ha_state()

    @property
    def current_cover_position(self) -> int:
        """Return the optimistic position (0 closed, 100 open)."""
        return self._position

    @property
    def is_closed(self) -> bool:
        """Return True when fully closed."""
        return self._position <= 0

    @property
    def is_opening(self) -> bool:
        """Return True while travelling open."""
        return self._moving_dir > 0

    @property
    def is_closing(self) -> bool:
        """Return True while travelling closed."""
        return self._moving_dir < 0

    async def _start_move(self, target: int) -> None:
        target = max(0, min(100, target))
        self._cancel_move()
        direction = 1 if target > self._position else -1
        if target == self._position:
            await self._release_all()
            return
        self._move_task = self.hass.async_create_task(
            self._move(direction, target)
        )

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open fully."""
        await self._start_move(100)

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close fully."""
        await self._start_move(0)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Travel to a specific position."""
        target = max(0, min(100, int(kwargs.get("position", 0))))
        if (
            self._recalibrate
            and self._position not in (0, 100)
            and target != self._position
        ):
            await self._recalibrating_move_to(target)
            return
        await self._start_move(target)

    async def _recalibrating_move_to(self, target: int) -> None:
        """Recalibrate against whichever endpoint is closer -- fully open or
        fully closed, whichever is a shorter run from the current estimate --
        relying on the motor's own end-stop as a real reference point, then
        travel to `target` from there. Used only when
        CONF_RECALIBRATE_BEFORE_REPOSITION is enabled -- see const.py and
        GitHub issue #15.
        """
        anchor = 0 if self._position <= 50 else 100
        await self._start_move(anchor)
        move_task = self._move_task
        if move_task is not None:
            try:
                await move_task
            except asyncio.CancelledError:
                # Superseded by a newer command (stop / another set_position)
                # while recalibrating -- let that command own the result.
                return
        if target != anchor:
            await self._start_move(target)

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop immediately and release both relays."""
        self._cancel_move()
        await self._release_all()
        self._moving_dir = 0
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel any in-flight travel task."""
        self._cancel_move()
