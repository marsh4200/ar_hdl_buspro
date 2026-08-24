"""Switch platform for the AR HDL BUSPRO integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ARHDLData
from .const import (
    CONF_CHANNEL,
    CONF_DEVICE_ID,
    CONF_DEVICE_TYPE,
    CONF_DEVICES,
    CONF_NAME,
    CONF_SUBNET_ID,
    DEVICE_TYPE_SWITCH,
    DOMAIN,
)
from .entity import ARHDLBaseEntity, build_device_info, build_unique_id
from .gateway import ARHDLGateway
from .pybuspro.devices.switch import Switch as PyBusproSwitch

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up AR HDL BUSPRO switches."""
    data: ARHDLData = hass.data[DOMAIN][entry.entry_id]
    devices = entry.options.get(CONF_DEVICES, [])

    entities: list[ARHDLSwitch] = []
    for device_cfg in devices:
        if device_cfg.get(CONF_DEVICE_TYPE) != DEVICE_TYPE_SWITCH:
            continue
        entities.append(ARHDLSwitch(entry, data.gateway, device_cfg))

    if entities:
        async_add_entities(entities)


class ARHDLSwitch(ARHDLBaseEntity, SwitchEntity):
    """Representation of an HDL Buspro switch channel."""

    def __init__(
        self,
        entry: ConfigEntry,
        gateway: ARHDLGateway,
        device_cfg: dict[str, Any],
    ) -> None:
        """Initialize the switch."""
        super().__init__(entry, gateway, device_cfg)

        subnet = int(device_cfg[CONF_SUBNET_ID])
        device = int(device_cfg[CONF_DEVICE_ID])
        channel = int(device_cfg[CONF_CHANNEL])

        self._switch = PyBusproSwitch(
            gateway.hdl, (subnet, device), channel, device_cfg.get(CONF_NAME, "")
        )

        self._attr_unique_id = build_unique_id(entry.entry_id, device_cfg)
        self._attr_device_info = build_device_info(entry, device_cfg)
        # See light.py: several channels can share one HA device, so each one
        # needs its own visible name rather than deferring to the device name.
        self._attr_has_entity_name = False
        self._attr_name = device_cfg.get(CONF_NAME) or f"HDL {subnet}.{device} ch{channel}"

    async def async_added_to_hass(self) -> None:
        """Register update callback."""
        await super().async_added_to_hass()

        async def _after_update(_device) -> None:
            self.async_write_ha_state()

        self._switch.register_device_updated_cb(_after_update)

    @property
    def is_on(self) -> bool:
        """Return True if switch is on."""
        return bool(self._switch.is_on)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        await self._switch.set_on()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        await self._switch.set_off()
