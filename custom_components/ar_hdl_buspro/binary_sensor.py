"""Binary sensor platform for the AR HDL BUSPRO integration."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from . import ARHDLData
from .const import (
    BINARY_KIND_DRY_CONTACT,
    BINARY_KIND_DRY_CONTACT_1,
    BINARY_KIND_DRY_CONTACT_2,
    BINARY_KIND_MOTION,
    BINARY_KIND_SINGLE_CHANNEL,
    BINARY_KIND_UNIVERSAL_SWITCH,
    CONF_BINARY_KIND,
    CONF_DEVICE_ID,
    CONF_DEVICE_TYPE,
    CONF_DEVICES,
    CONF_NAME,
    CONF_SCAN_INTERVAL,
    CONF_SUBNET_ID,
    CONF_SUB_NUMBER,
    DEFAULT_SCAN_INTERVAL,
    DEVICE_TYPE_BINARY_SENSOR,
    DOMAIN,
)
from .entity import ARHDLBaseEntity, build_device_info, build_unique_id
from .gateway import ARHDLGateway
from .pybuspro.devices.sensor import Sensor as PyBusproSensor

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up AR HDL BUSPRO binary sensors."""
    data: ARHDLData = hass.data[DOMAIN][entry.entry_id]
    devices = entry.options.get(CONF_DEVICES, [])

    entities: list[ARHDLBinarySensor] = []
    for device_cfg in devices:
        if device_cfg.get(CONF_DEVICE_TYPE) != DEVICE_TYPE_BINARY_SENSOR:
            continue
        entities.append(ARHDLBinarySensor(entry, data.gateway, device_cfg))

    if entities:
        async_add_entities(entities)


DEVICE_CLASS_BY_KIND = {
    BINARY_KIND_MOTION: BinarySensorDeviceClass.MOTION,
    BINARY_KIND_DRY_CONTACT_1: None,
    BINARY_KIND_DRY_CONTACT_2: None,
    BINARY_KIND_UNIVERSAL_SWITCH: None,
    BINARY_KIND_SINGLE_CHANNEL: None,
    BINARY_KIND_DRY_CONTACT: None,
}


class ARHDLBinarySensor(ARHDLBaseEntity, BinarySensorEntity):
    """Representation of an HDL Buspro binary sensor."""

    def __init__(
        self,
        entry: ConfigEntry,
        gateway: ARHDLGateway,
        device_cfg: dict[str, Any],
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(entry, gateway, device_cfg)

        subnet = int(device_cfg[CONF_SUBNET_ID])
        device = int(device_cfg[CONF_DEVICE_ID])
        self._kind: str = device_cfg[CONF_BINARY_KIND]
        sub_number = int(device_cfg.get(CONF_SUB_NUMBER, 0))

        # Map binary kind to the underlying Sensor library parameters.
        universal_switch_number = None
        channel_number = None
        switch_number = None
        if self._kind == BINARY_KIND_UNIVERSAL_SWITCH:
            universal_switch_number = sub_number
        elif self._kind == BINARY_KIND_SINGLE_CHANNEL:
            channel_number = sub_number
        elif self._kind == BINARY_KIND_DRY_CONTACT:
            switch_number = sub_number

        self._sensor = PyBusproSensor(
            gateway.hdl,
            (subnet, device),
            universal_switch_number=universal_switch_number,
            channel_number=channel_number,
            switch_number=switch_number,
            name=device_cfg.get(CONF_NAME, ""),
        )

        # See sensor.py: per-entity scan_interval is ignored by HA for
        # config-entry platforms, so we run our own timer instead.
        self._scan_interval = int(
            device_cfg.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )

        self._attr_unique_id = build_unique_id(
            entry.entry_id, device_cfg, suffix=self._kind
        )
        self._attr_device_info = build_device_info(entry, device_cfg)
        self._attr_translation_key = self._kind
        self._attr_device_class = DEVICE_CLASS_BY_KIND.get(self._kind)

    async def async_added_to_hass(self) -> None:
        """Register update callback and optional polling timer."""
        await super().async_added_to_hass()

        async def _after_update(_device) -> None:
            self.async_write_ha_state()

        self._sensor.register_device_updated_cb(_after_update)

        if self._scan_interval > 0:

            async def _poll(_now) -> None:
                await self._sensor.read_sensor_status()

            self.async_on_remove(
                async_track_time_interval(
                    self.hass, _poll, timedelta(seconds=self._scan_interval)
                )
            )

    @property
    def is_on(self) -> bool | None:
        """Return the binary state."""
        if self._kind == BINARY_KIND_MOTION:
            return self._sensor.movement
        if self._kind == BINARY_KIND_DRY_CONTACT_1:
            return self._sensor.dry_contact_1_is_on
        if self._kind == BINARY_KIND_DRY_CONTACT_2:
            return self._sensor.dry_contact_2_is_on
        if self._kind == BINARY_KIND_UNIVERSAL_SWITCH:
            return self._sensor.universal_switch_is_on
        if self._kind == BINARY_KIND_SINGLE_CHANNEL:
            return self._sensor.single_channel_is_on
        if self._kind == BINARY_KIND_DRY_CONTACT:
            return self._sensor.switch_status
        return None
