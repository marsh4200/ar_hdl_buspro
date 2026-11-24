"""Light platform for the AR HDL BUSPRO integration."""

# Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).
# All rights reserved. Proprietary and confidential.
# Licensed software - see LICENSE in the repository root. Unauthorised
# copying, redistribution, modification, or circumvention of the licence
# check in licensing.py is prohibited.
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ATTR_RGBWW_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ARHDLData
from .colour import (
    MAX_KELVIN,
    MIN_KELVIN,
    dali_colour_values,
    group_levels,
    group_state,
)
from .const import (
    CONF_BLUE_CHANNEL,
    CONF_GREEN_CHANNEL,
    CONF_LIGHT_COLOR,
    CONF_RED_CHANNEL,
    CONF_RGB_MODE,
    CONF_WHITE_CHANNEL,
    DEVICE_TYPE_RGB_LIGHT,
    LIGHT_COLOR_CCT,
    LIGHT_COLOR_RGB,
    LIGHT_COLOR_RGBCW,
    LIGHT_COLOR_RGBW,
    LIGHT_COLOR_RGBWAF,
    LIGHT_COLOR_TO_DALI2_TYPE,
    RGB_MODE_RGBW,
    CONF_CHANNEL,
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
        dtype = device_cfg.get(CONF_DEVICE_TYPE)
        if dtype == DEVICE_TYPE_RGB_LIGHT:
            entities.append(ARHDLRgbGroupLight(entry, data.gateway, device_cfg))
            continue
        if dtype != DEVICE_TYPE_LIGHT:
            continue
        if (
            device_cfg.get(CONF_LIGHT_COLOR) in LIGHT_COLOR_TO_DALI2_TYPE
            and device_cfg.get(CONF_DIMMABLE, True)
        ):
            entities.append(ARHDLDaliColourLight(entry, data.gateway, device_cfg))
        else:
            # Ordinary dimmer / relay channel: unchanged.
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


# ---------------------------------------------------------------------------
# DALI-2 colour channel
# ---------------------------------------------------------------------------
_COLOUR_MODE_FOR_KIND = {
    LIGHT_COLOR_CCT: ColorMode.COLOR_TEMP,
    LIGHT_COLOR_RGB: ColorMode.RGB,
    LIGHT_COLOR_RGBW: ColorMode.RGBW,
    LIGHT_COLOR_RGBCW: ColorMode.RGBWW,
    LIGHT_COLOR_RGBWAF: ColorMode.RGBW,
}
_DEFAULT_COLOUR = {
    LIGHT_COLOR_RGB: (255, 255, 255),
    LIGHT_COLOR_RGBW: (0, 0, 0, 255),
    LIGHT_COLOR_RGBCW: (0, 0, 0, 255, 255),
    LIGHT_COLOR_RGBWAF: (0, 0, 0, 255),
}
_COLOUR_ATTR = {
    ColorMode.RGB: ATTR_RGB_COLOR,
    ColorMode.RGBW: ATTR_RGBW_COLOR,
    ColorMode.RGBWW: ATTR_RGBWW_COLOR,
}


class ARHDLDaliColourLight(ARHDLLight, RestoreEntity):
    """A DALI-2 channel set up as CCT / RGB / RGBW / RGBCW / RGBWAF.

    Brightness works exactly like any dimmer channel (0x0031, confirmed by
    the module's reply). Colour is sent with HDL Studio's DALI-2 colour
    command (0xE45C). The controller doesn't report colour back, so the
    colour shown is the last one set from here (kept across restarts).
    """

    def __init__(self, entry, gateway, device_cfg) -> None:
        super().__init__(entry, gateway, device_cfg)
        self._kind = device_cfg[CONF_LIGHT_COLOR]
        self._dali_type = LIGHT_COLOR_TO_DALI2_TYPE[self._kind]
        mode = _COLOUR_MODE_FOR_KIND[self._kind]
        self._attr_color_mode = mode
        self._attr_supported_color_modes = {mode}
        self._colour = _DEFAULT_COLOUR.get(self._kind)
        self._kelvin = 4000
        if mode == ColorMode.COLOR_TEMP:
            self._attr_min_color_temp_kelvin = MIN_KELVIN
            self._attr_max_color_temp_kelvin = MAX_KELVIN

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is None:
            return
        attrs = last.attributes
        if self._attr_color_mode == ColorMode.COLOR_TEMP:
            kelvin = attrs.get(ATTR_COLOR_TEMP_KELVIN)
            if kelvin:
                self._kelvin = int(kelvin)
        else:
            colour = attrs.get(_COLOUR_ATTR[self._attr_color_mode])
            if colour and len(colour) == len(self._colour):
                self._colour = tuple(int(c) for c in colour)

    @property
    def rgb_color(self):
        return self._colour if self._attr_color_mode == ColorMode.RGB else None

    @property
    def rgbw_color(self):
        return self._colour if self._attr_color_mode == ColorMode.RGBW else None

    @property
    def rgbww_color(self):
        return self._colour if self._attr_color_mode == ColorMode.RGBWW else None

    @property
    def color_temp_kelvin(self):
        return self._kelvin if self._attr_color_mode == ColorMode.COLOR_TEMP else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        colour_key = _COLOUR_ATTR.get(self._attr_color_mode)
        colour_change = (colour_key and colour_key in kwargs) or (
            self._attr_color_mode == ColorMode.COLOR_TEMP
            and ATTR_COLOR_TEMP_KELVIN in kwargs
        )
        if not colour_change:
            # Brightness / plain on: the ordinary, confirmed dimmer path.
            await super().async_turn_on(**kwargs)
            return
        if colour_key and colour_key in kwargs:
            self._colour = tuple(int(c) for c in kwargs[colour_key])
        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            self._kelvin = int(kwargs[ATTR_COLOR_TEMP_KELVIN])
        if ATTR_BRIGHTNESS in kwargs:
            pct = round(kwargs[ATTR_BRIGHTNESS] / 255 * 100)
        elif self.is_on:
            pct = int(self._light.current_brightness)
        else:
            pct = self._light.previous_brightness or 100
        pct = max(1, min(100, pct))
        values = dali_colour_values(self._kind, self._colour, self._kelvin)
        await self._light.set_dali_colour(pct, self._dali_type, values)
        self.async_write_ha_state()
        self._led_sync.push_soon(lambda: self.is_on)


# ---------------------------------------------------------------------------
# RGB / RGBW group made of plain dimmer channels
# ---------------------------------------------------------------------------
class _GroupResync:
    """Lets the base entity re-read / check every channel of the group."""

    def __init__(self, lights) -> None:
        self._lights = lights

    @property
    def responsive(self) -> bool:
        return all(light.responsive for light in self._lights)

    async def read_status(self) -> None:
        # One status read answers every channel of the module.
        await self._lights[0].read_status()


class ARHDLRgbGroupLight(ARHDLBaseEntity, LightEntity, RestoreEntity):
    """One colour light driven by 3 (RGB) or 4 (RGBW) dimmer channels.

    Each channel is an ordinary dimmer channel on the bus (0x0031 to set,
    0x0032 / 0x0034 replies to follow it), so the colour shown always comes
    from what the module reports - a keypad or scene changing the channels
    moves the colour wheel too.
    """

    _attr_has_entity_name = False

    def __init__(self, entry, gateway, device_cfg) -> None:
        super().__init__(entry, gateway, device_cfg)
        subnet = int(device_cfg[CONF_SUBNET_ID])
        device = int(device_cfg[CONF_DEVICE_ID])
        self._rgbw = device_cfg.get(CONF_RGB_MODE) == RGB_MODE_RGBW
        keys = [CONF_RED_CHANNEL, CONF_GREEN_CHANNEL, CONF_BLUE_CHANNEL]
        if self._rgbw:
            keys.append(CONF_WHITE_CHANNEL)
        name = device_cfg.get(CONF_NAME) or f"HDL {subnet}.{device} RGB"
        self._lights = [
            PyBusproLight(gateway.hdl, (subnet, device), int(device_cfg[k]), name)
            for k in keys
        ]
        self._resync_device = _GroupResync(self._lights)
        mode = ColorMode.RGBW if self._rgbw else ColorMode.RGB
        self._attr_color_mode = mode
        self._attr_supported_color_modes = {mode}
        self._attr_unique_id = build_unique_id(entry.entry_id, device_cfg)
        self._attr_device_info = build_device_info(entry, device_cfg, gateway.device_id)
        self._attr_name = name
        # Last colour/brightness while on, used by a plain "turn on".
        self._last_colour: tuple[int, ...] = (255,) * len(self._lights)
        self._last_brightness = 255

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            key = ATTR_RGBW_COLOR if self._rgbw else ATTR_RGB_COLOR
            colour = last.attributes.get(key)
            if colour and len(colour) == len(self._lights):
                self._last_colour = tuple(int(c) for c in colour)
            if last.attributes.get(ATTR_BRIGHTNESS):
                self._last_brightness = int(last.attributes[ATTR_BRIGHTNESS])

        async def _after_update(_device) -> None:
            self._remember()
            self.async_write_ha_state()

        for light in self._lights:
            light.register_device_updated_cb(_after_update)

    # ----- state ------------------------------------------------------------
    def _levels(self) -> list[int]:
        # Some firmware reports 255 for "on"; clamp to the 0-100 scale.
        return [min(100, max(0, int(l.current_brightness))) for l in self._lights]

    def _remember(self) -> None:
        on, brightness, colour = group_state(self._levels())
        if on:
            self._last_colour = colour
            self._last_brightness = brightness

    @property
    def is_on(self) -> bool:
        return group_state(self._levels())[0]

    @property
    def brightness(self) -> int | None:
        on, brightness, _colour = group_state(self._levels())
        return brightness if on else None

    @property
    def rgb_color(self):
        if self._rgbw:
            return None
        on, _b, colour = group_state(self._levels())
        return colour if on else self._last_colour

    @property
    def rgbw_color(self):
        if not self._rgbw:
            return None
        on, _b, colour = group_state(self._levels())
        return colour if on else self._last_colour

    # ----- commands ---------------------------------------------------------
    async def _apply(self, levels) -> None:
        for light, level in zip(self._lights, levels):
            await light.set_brightness(level, 0)
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        key = ATTR_RGBW_COLOR if self._rgbw else ATTR_RGB_COLOR
        colour = tuple(kwargs[key]) if key in kwargs else self._last_colour
        if ATTR_BRIGHTNESS in kwargs:
            brightness = int(kwargs[ATTR_BRIGHTNESS])
        elif self.is_on:
            brightness = self.brightness or self._last_brightness
        else:
            brightness = self._last_brightness or 255
        if brightness <= 0:
            await self.async_turn_off()
            return
        if not any(colour):
            colour = (255,) * len(self._lights)
        self._last_colour = tuple(int(c) for c in colour)
        self._last_brightness = brightness
        await self._apply(group_levels(colour, brightness))

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._remember()
        await self._apply([0] * len(self._lights))
