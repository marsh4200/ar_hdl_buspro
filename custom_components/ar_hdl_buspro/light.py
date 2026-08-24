"""Light platform for the AR HDL BUSPRO integration."""
from __future__ import annotations

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
    CONF_DEVICE_ID,
    CONF_DEVICE_TYPE,
    CONF_DEVICES,
    CONF_DIMMABLE,
    CONF_NAME,
    CONF_RUNNING_TIME,
    CONF_SUBNET_ID,
    DEFAULT_RUNNING_TIME,
    DEVICE_TYPE_LIGHT,
    DOMAIN,
)
from .entity import ARHDLBaseEntity, build_device_info, build_unique_id
from .gateway import ARHDLGateway
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

    entities: list[ARHDLLight] = []
    for device_cfg in devices:
        if device_cfg.get(CONF_DEVICE_TYPE) != DEVICE_TYPE_LIGHT:
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

        self._attr_unique_id = build_unique_id(entry.entry_id, device_cfg)
        self._attr_device_info = build_device_info(entry, device_cfg)
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

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        await self._light.set_off(self._running_time)
