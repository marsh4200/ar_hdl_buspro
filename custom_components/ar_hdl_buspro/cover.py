"""Cover (curtain) platform for the AR HDL BUSPRO integration.

Two hardware styles are supported:

- curtain_module: a real HDL curtain module (MW02 / MWM70B family) driven via
  CurtainSwitchControl (0xE3E0) with actions stop=0 / open=1 / close=2, status
  read via ReadStatusOfCurtainSwitch (0xE3E2). CurtainSwitchControl carries no
  percentage on the wire, so SET_POSITION is estimated from the configured
  travel time: a full open/close still just fires the command and lets the
  module's own limit switches decide when to stop (unchanged from before),
  while a partial position drives in the right direction and sends an
  explicit stop after a proportionally-scaled delay. This is a client-side
  estimate, not a real position readback -- it can drift if the curtain is
  moved by something this integration doesn't see (a wall switch, a remote,
  another controller).
- relay_pair: the very common install style where a curtain motor hangs off
  two interlocked relay channels (one drives open, one drives close). We pulse
  the direction channel for the configured travel time, then release it, and
  track position optimistically.
"""
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
        self._attr_device_info = build_device_info(entry, device_cfg)
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

    Position is estimated from the configured travel time -- see the module
    docstring. `is_closed` still reflects a real bus report when we have one;
    everything position-related (`current_cover_position`, `is_opening`,
    `is_closing`) is a local estimate, hence `_attr_assumed_state`.
    """

    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )
    _attr_assumed_state = True

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

    async def async_added_to_hass(self) -> None:
        """Register bus callback and request initial status."""
        await super().async_added_to_hass()
        self._dev.register_telegram_received_cb(self._telegram_received)
        await self._send(
            OperateCode.ReadStatusOfCurtainSwitch, [self._curtain_number]
        )

    def _telegram_received(self, telegram) -> None:
        op = telegram.operate_code
        payload = telegram.payload or []
        if op in (
            OperateCode.CurtainSwitchControlResponse,
            OperateCode.ReadStatusOfCurtainSwitchResponse,
        ):
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

    @property
    def is_closed(self) -> bool | None:
        """Return True when the module reports closed."""
        if self._status is None:
            return None
        return self._status == _ACTION_CLOSE

    @property
    def is_opening(self) -> bool:
        """Return True while an estimated open move is in progress."""
        return self._moving_dir > 0

    @property
    def is_closing(self) -> bool:
        """Return True while an estimated close move is in progress."""
        return self._moving_dir < 0

    @property
    def current_cover_position(self) -> int:
        """Return the estimated position (0 closed, 100 open)."""
        return self._position

    async def _start_move(self, action: int, target: int) -> None:
        """Fire `action` on the wire, then track/estimate position toward `target`."""
        self._cancel_move()
        await self._send(
            OperateCode.CurtainSwitchControl, [self._curtain_number, action]
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
                    [self._curtain_number, _ACTION_STOP],
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
        await self._start_move(_ACTION_OPEN, 100)

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the curtain."""
        await self._start_move(_ACTION_CLOSE, 0)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Travel to a specific estimated position."""
        target = max(0, min(100, int(kwargs.get("position", 0))))
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
        self._cancel_move()
        await self._send(
            OperateCode.CurtainSwitchControl, [self._curtain_number, _ACTION_STOP]
        )
        self._status = _ACTION_STOP
        self._moving_dir = 0
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel any in-flight travel task."""
        self._cancel_move()


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
