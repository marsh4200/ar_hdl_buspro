"""Light platform for the AR HDL BUSPRO integration."""

# Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).
# All rights reserved. Proprietary and confidential.
# Licensed software - see LICENSE in the repository root. Unauthorised
# copying, redistribution, modification, or circumvention of the licence
# check in licensing.py is prohibited.
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ARHDLData
from .const import (
    CONF_CHANNEL,
    CONF_DALI_MEMBERS,
    CONF_DEVICE_ID,
    CONF_DEVICE_TYPE,
    CONF_DEVICES,
    CONF_DIMMABLE,
    CONF_KEYPAD_LEDS,
    CONF_NAME,
    CONF_RUNNING_TIME,
    CONF_SUBNET_ID,
    DEFAULT_RUNNING_TIME,
    DEVICE_TYPE_LIGHT,
    DOMAIN,
)
from .entity import ARHDLBaseEntity, build_device_info, build_unique_id
from .gateway import ARHDLGateway
from .keypad_led import KeypadLedSync
from .pybuspro.devices.light import Light as PyBusproLight

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up AR HDL BUSPRO lights from a config entry."""
    data: ARHDLData = hass.data[DOMAIN][entry.entry_id]
    devices = entry.options.get(CONF_DEVICES, [])

    entities: list[LightEntity] = []
    for device_cfg in devices:
        if device_cfg.get(CONF_DEVICE_TYPE) != DEVICE_TYPE_LIGHT:
            continue
        if device_cfg.get(CONF_DALI_MEMBERS):
            entities.append(ARHDLDaliGroupLight(entry, data.gateway, device_cfg))
            continue
        entities.append(ARHDLLight(entry, data.gateway, device_cfg))

    if entities:
        async_add_entities(entities)


class ARHDLLight(ARHDLBaseEntity, LightEntity):
    """Representation of an HDL Buspro light channel."""

    def __init__(
        self,
        entry: ConfigEntry,
        gateway: ARHDLGateway,
        device_cfg: dict[str, Any],
    ) -> None:
        """Initialize the light."""
        super().__init__(entry, gateway, device_cfg)

        subnet = int(device_cfg[CONF_SUBNET_ID])
        device = int(device_cfg[CONF_DEVICE_ID])
        channel = int(device_cfg[CONF_CHANNEL])

        self._dimmable = bool(device_cfg.get(CONF_DIMMABLE, True))
        # Per HDL specifics: setting a running time on a dimmable channel
        # produces strange behavior, so we zero it out for dimmable channels.
        self._running_time = (
            0
            if self._dimmable
            else int(device_cfg.get(CONF_RUNNING_TIME, DEFAULT_RUNNING_TIME))
        )

        self._light = PyBusproLight(
            gateway.hdl, (subnet, device), channel, device_cfg.get(CONF_NAME, "")
        )
        # Keypad buttons whose LED should follow this light (keypad_led.py).
        self._led_sync = KeypadLedSync(gateway.hdl, device_cfg.get(CONF_KEYPAD_LEDS))
        # Lets ARHDLBaseEntity re-read this channel once after a reconnect,
        # instead of showing whatever it was doing before the link dropped
        # (see _handle_gateway_availability in entity.py).
        self._resync_device = self._light

        self._attr_unique_id = build_unique_id(entry.entry_id, device_cfg)
        self._attr_device_info = build_device_info(entry, device_cfg, gateway.device_id)
        # A single HDL module (subnet.device) can drive several independent
        # channels, and they all share one HA "device" (see build_device_info).
        # has_entity_name=True + _attr_name=None would then show every channel
        # under the same device-name label with no way to tell them apart, so
        # each channel gets its own name instead (its configured name, falling
        # back to "HDL <addr> ch<N>" if it was never set).
        self._attr_has_entity_name = False
        self._attr_name = device_cfg.get(CONF_NAME) or f"HDL {subnet}.{device} ch{channel}"

        if self._dimmable:
            self._attr_color_mode = ColorMode.BRIGHTNESS
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
        else:
            self._attr_color_mode = ColorMode.ONOFF
            self._attr_supported_color_modes = {ColorMode.ONOFF}

    async def async_added_to_hass(self) -> None:
        """Register update callback when added to HA."""
        await super().async_added_to_hass()

        async def _after_update(_device) -> None:
            self.async_write_ha_state()
            # The channel changed on the bus: bring linked keypad LEDs along.
            # Set once the keypad has settled, with the live state.
            self._led_sync.follow(lambda: self.is_on)

        self._light.register_device_updated_cb(_after_update)

    @property
    def brightness(self) -> int | None:
        """Return current brightness 0–255."""
        if not self._dimmable:
            return None
        # HDL reports 0-100 for dimmers, but some relay firmware reports 255
        # for "on"; clamp so HA never sees an out-of-range brightness.
        pct = min(100, max(0, int(self._light.current_brightness)))
        return round(pct / 100 * 255)

    @property
    def is_on(self) -> bool:
        """Return True if the light is on."""
        return bool(self._light.is_on)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on."""
        if self._dimmable and ATTR_BRIGHTNESS in kwargs:
            brightness_pct = int(kwargs[ATTR_BRIGHTNESS] / 255 * 100)
        else:
            brightness_pct = 100

        # Restore previous brightness on simple "on" press if we have one
        if (
            self._dimmable
            and brightness_pct == 100
            and ATTR_BRIGHTNESS not in kwargs
            and not self.is_on
            and self._light.previous_brightness is not None
        ):
            brightness_pct = self._light.previous_brightness

        await self._light.set_brightness(brightness_pct, self._running_time)
        # Show the new state now instead of waiting for the module's reply;
        # under rapid clicking that reply can be a second or more behind.
        self.async_write_ha_state()
        self._led_sync.push_soon(lambda: self.is_on)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        await self._light.set_off(self._running_time)
        self.async_write_ha_state()
        self._led_sync.push_soon(lambda: self.is_on)


# How long a DALI fade can take before the members are read back after a
# group command (the controller doesn't report member levels for a group).
_DALI_GROUP_READBACK = 2.0


class ARHDLDaliGroupLight(ARHDLBaseEntity, LightEntity):
    """A DALI group (HDL zone) on an HDL DALI controller.

    Controlled with the normal single-channel command on channel 64 + group.
    Its state is the live state of its member channels: on when any member
    is on, brightness = the brightest member. After a group command the
    members are read back once the fade has had time to finish.
    """

    _attr_color_mode = ColorMode.BRIGHTNESS

    def __init__(
        self,
        entry: ConfigEntry,
        gateway: ARHDLGateway,
        device_cfg: dict[str, Any],
    ) -> None:
        """Initialize the group light."""
        super().__init__(entry, gateway, device_cfg)
        self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
        subnet = int(device_cfg[CONF_SUBNET_ID])
        device = int(device_cfg[CONF_DEVICE_ID])
        channel = int(device_cfg[CONF_CHANNEL])
        name = device_cfg.get(CONF_NAME, "")
        self._group = PyBusproLight(gateway.hdl, (subnet, device), channel, name)
        self._members = [
            PyBusproLight(gateway.hdl, (subnet, device), int(ch), name)
            for ch in device_cfg.get(CONF_DALI_MEMBERS, [])
        ]
        # Commanded level shown until the members report back.
        self._pending: int | None = None
        self._last_level: int | None = None
        self._readback: asyncio.Task | None = None
        # Availability and the post-reconnect resync follow the members'
        # module (one read returns every channel).
        self._resync_device = self._members[0] if self._members else self._group

        self._attr_unique_id = build_unique_id(entry.entry_id, device_cfg)
        self._attr_device_info = build_device_info(entry, device_cfg, gateway.device_id)
        self._attr_has_entity_name = False
        self._attr_name = name or f"HDL {subnet}.{device} ch{channel}"

    async def async_added_to_hass(self) -> None:
        """Follow the member channels."""
        await super().async_added_to_hass()

        async def _member_updated(_device) -> None:
            self._pending = None
            self.async_write_ha_state()

        for member in self._members:
            member.register_device_updated_cb(_member_updated)

    async def async_will_remove_from_hass(self) -> None:
        """Cancel a pending read-back."""
        if self._readback and not self._readback.done():
            self._readback.cancel()

    def _level(self) -> int:
        """Group level 0-100 from the members (or the pending command)."""
        if self._pending is not None:
            return self._pending
        levels = [
            min(100, max(0, int(m.current_brightness))) for m in self._members
        ]
        return max(levels, default=0)

    @property
    def brightness(self) -> int:
        """Return the brightest member as 0-255."""
        return round(self._level() / 100 * 255)

    @property
    def is_on(self) -> bool:
        """Return True when any member is on."""
        return self._level() > 0

    async def _command(self, level: int) -> None:
        if level > 0:
            self._last_level = level
        self._pending = level
        await self._group.set_brightness(level, 0)
        self.async_write_ha_state()
        if self._readback and not self._readback.done():
            self._readback.cancel()
        self._readback = self.hass.async_create_task(self._read_back())

    async def _read_back(self) -> None:
        await asyncio.sleep(_DALI_GROUP_READBACK)
        if self._members:
            await self._members[0].read_status()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the group on (restores its last level on a plain "on")."""
        if ATTR_BRIGHTNESS in kwargs:
            level = int(kwargs[ATTR_BRIGHTNESS] / 255 * 100)
        elif not self.is_on and self._last_level:
            level = self._last_level
        else:
            level = 100
        await self._command(max(1, level))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the group off."""
        await self._command(0)
