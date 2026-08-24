"""Sensor platform for the AR HDL BUSPRO integration."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import LIGHT_LUX, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from . import ARHDLData
from .const import (
    CONF_DEVICE_HW_KIND,
    CONF_DEVICE_ID,
    CONF_DEVICE_TYPE,
    CONF_DEVICES,
    CONF_NAME,
    CONF_SCAN_INTERVAL,
    CONF_SENSOR_KIND,
    CONF_SUBNET_ID,
    CONF_TEMP_FAHRENHEIT,
    CONF_TEMP_OFFSET,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TEMP_OFFSET,
    DEVICE_HW_GENERIC,
    DEVICE_TYPE_SENSOR,
    DOMAIN,
    SENSOR_KIND_ILLUMINANCE,
    SENSOR_KIND_TEMPERATURE,
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
    """Set up AR HDL BUSPRO sensors."""
    data: ARHDLData = hass.data[DOMAIN][entry.entry_id]
    devices = entry.options.get(CONF_DEVICES, [])

    entities: list[ARHDLSensor] = []
    for device_cfg in devices:
        if device_cfg.get(CONF_DEVICE_TYPE) != DEVICE_TYPE_SENSOR:
            continue
        entities.append(ARHDLSensor(entry, data.gateway, device_cfg))

    if entities:
        async_add_entities(entities)


class ARHDLSensor(ARHDLBaseEntity, SensorEntity):
    """Representation of an HDL Buspro sensor."""

    def __init__(
        self,
        entry: ConfigEntry,
        gateway: ARHDLGateway,
        device_cfg: dict[str, Any],
    ) -> None:
        """Initialize the sensor."""
        super().__init__(entry, gateway, device_cfg)

        subnet = int(device_cfg[CONF_SUBNET_ID])
        device = int(device_cfg[CONF_DEVICE_ID])

        self._sensor_kind: str = device_cfg.get(
            CONF_SENSOR_KIND, SENSOR_KIND_TEMPERATURE
        )
        self._hw_kind = device_cfg.get(CONF_DEVICE_HW_KIND, DEVICE_HW_GENERIC)
        self._offset = int(device_cfg.get(CONF_TEMP_OFFSET, DEFAULT_TEMP_OFFSET))
        # Some HDL sensors/panels are configured to report degF on the bus.
        self._reports_fahrenheit = bool(
            device_cfg.get(CONF_TEMP_FAHRENHEIT, False)
        )

        # NOTE: setting `self.scan_interval` on an entity does nothing for
        # config-entry platforms -- HA ignores it. We poll ourselves with a
        # timer in async_added_to_hass instead.
        self._scan_interval = int(
            device_cfg.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )

        # The vendored Sensor class accepts a `device` kwarg to indicate hw kind.
        # Translate our hw kind to the legacy string the library understands.
        legacy_device_kind = (
            self._hw_kind if self._hw_kind in ("dlp", "12in1", "sensors_in_one") else None
        )

        self._sensor = PyBusproSensor(
            gateway.hdl,
            (subnet, device),
            device=legacy_device_kind,
            name=device_cfg.get(CONF_NAME, ""),
        )

        # Entity metadata
        self._attr_unique_id = build_unique_id(
            entry.entry_id, device_cfg, suffix=self._sensor_kind
        )
        self._attr_device_info = build_device_info(entry, device_cfg)
        # With has_entity_name and a translation_key we get nicely named entities
        # like "Living Room Temperature".
        self._attr_translation_key = self._sensor_kind

        if self._sensor_kind == SENSOR_KIND_TEMPERATURE:
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif self._sensor_kind == SENSOR_KIND_ILLUMINANCE:
            self._attr_device_class = SensorDeviceClass.ILLUMINANCE
            self._attr_native_unit_of_measurement = LIGHT_LUX
            self._attr_state_class = SensorStateClass.MEASUREMENT

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
    def available(self) -> bool:
        """Return True if connection is up AND we have a real reading."""
        if not super().available:
            return False
        if self._sensor_kind == SENSOR_KIND_TEMPERATURE:
            return self._sensor._current_temperature is not None  # noqa: SLF001
        if self._sensor_kind == SENSOR_KIND_ILLUMINANCE:
            return self._sensor._brightness is not None  # noqa: SLF001
        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose the last raw telegram for payload-layout diagnostics."""
        op = getattr(self._sensor, "last_telegram_op", None)
        if op is None:
            return None
        return {
            "last_telegram": op,
            "raw_payload": getattr(self._sensor, "last_telegram_payload", None),
        }

    @property
    def native_value(self) -> float | int | None:
        """Return the sensor reading."""
        if self._sensor_kind == SENSOR_KIND_TEMPERATURE:
            value = self._sensor.temperature
            if value is None or value == 0:
                # Either no reading yet, or a 0°C reading. Apply offset only
                # for real readings.
                return value
            if self._reports_fahrenheit:
                # Hardware reports degF; convert before offset so the offset
                # stays in degC like everywhere else.
                value = round((value - 32) * 5 / 9, 1)
            return value + self._offset
        if self._sensor_kind == SENSOR_KIND_ILLUMINANCE:
            return self._sensor.brightness
        return None
