"""Climate platform for the AR HDL BUSPRO integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    FAN_AUTO,
    FAN_HIGH,
    FAN_LOW,
    FAN_MEDIUM,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ARHDLData
from .const import (
    AC_HVAC_MODES,
    CLIMATE_KIND_AC_IR,
    CONF_AC_HVAC_MODES,
    CONF_CLIMATE_KIND,
    CONF_DEVICE_ID,
    CONF_DEVICE_TYPE,
    CONF_DEVICES,
    CONF_HVAC_NUMBER,
    CONF_NAME,
    CONF_PRESET_MODES,
    CONF_RELAY_CHANNEL,
    CONF_RELAY_DEVICE,
    CONF_RELAY_SUBNET,
    CONF_SUBNET_ID,
    DEVICE_TYPE_CLIMATE,
    DOMAIN,
    PRESET_AWAY,
    PRESET_HOME,
    PRESET_NONE,
    PRESET_SLEEP,
)
from .entity import ARHDLBaseEntity, build_device_info, build_unique_id
from .gateway import ARHDLGateway
from .pybuspro.devices.climate import AirConditioner as PyBusproAirConditioner
from .pybuspro.devices.climate import Climate as PyBusproClimate
from .pybuspro.devices.climate import ControlFloorHeatingStatus
from .pybuspro.devices.sensor import Sensor as PyBusproSensor
from .pybuspro.helpers.enums import OnOffStatus

_LOGGER = logging.getLogger(__name__)

HA_PRESET_TO_HDL = {
    PRESET_NONE: 1,   # Normal
    PRESET_HOME: 2,   # Day
    PRESET_SLEEP: 3,  # Night
    PRESET_AWAY: 4,   # Away
}
HDL_TO_HA_PRESET = {v: k for k, v in HA_PRESET_TO_HDL.items()}

# AirConditioner's own mode keys ("cool"/"heat"/"fan_only"/"auto"/"dry")
# line up 1:1 with HVACMode's values, so this is just HVACMode(key) --
# spelled out as a dict for a clear error if that mapping ever drifts.
HA_HVAC_MODE_TO_AC_MODE = {
    HVACMode.COOL: "cool",
    HVACMode.HEAT: "heat",
    HVACMode.FAN_ONLY: "fan_only",
    HVACMode.AUTO: "auto",
    HVACMode.DRY: "dry",
}
AC_MODE_TO_HA_HVAC_MODE = {v: k for k, v in HA_HVAC_MODE_TO_AC_MODE.items()}
AC_MODE_TO_HVAC_ACTION = {
    "cool": HVACAction.COOLING,
    "heat": HVACAction.HEATING,
    "fan_only": HVACAction.FAN,
    "dry": HVACAction.DRYING,
    "auto": HVACAction.IDLE,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up AR HDL BUSPRO climate entities."""
    data: ARHDLData = hass.data[DOMAIN][entry.entry_id]
    devices = entry.options.get(CONF_DEVICES, [])

    entities: list[ClimateEntity] = []
    for device_cfg in devices:
        if device_cfg.get(CONF_DEVICE_TYPE) != DEVICE_TYPE_CLIMATE:
            continue
        if device_cfg.get(CONF_CLIMATE_KIND) == CLIMATE_KIND_AC_IR:
            entities.append(ARHDLAcClimate(entry, data.gateway, device_cfg))
        else:
            entities.append(ARHDLDlpClimate(entry, data.gateway, device_cfg))

    if entities:
        async_add_entities(entities)


class ARHDLDlpClimate(ARHDLBaseEntity, ClimateEntity):
    """Representation of an HDL Buspro floor-heating climate device."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 1
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]

    def __init__(
        self,
        entry: ConfigEntry,
        gateway: ARHDLGateway,
        device_cfg: dict[str, Any],
    ) -> None:
        """Initialize the climate device."""
        super().__init__(entry, gateway, device_cfg)

        subnet = int(device_cfg[CONF_SUBNET_ID])
        device = int(device_cfg[CONF_DEVICE_ID])

        self._climate = PyBusproClimate(
            gateway.hdl, (subnet, device), device_cfg.get(CONF_NAME, "")
        )
        self._preset_modes_cfg: list[str] = list(
            device_cfg.get(CONF_PRESET_MODES, [])
        )

        # Optional relay sensor for heating-vs-idle distinction
        self._relay_sensor: PyBusproSensor | None = None
        self._relay_sensor_is_on: bool | None = None
        relay_subnet = int(device_cfg.get(CONF_RELAY_SUBNET, 0))
        relay_device = int(device_cfg.get(CONF_RELAY_DEVICE, 0))
        relay_channel = int(device_cfg.get(CONF_RELAY_CHANNEL, 0))
        if relay_subnet and relay_device and relay_channel:
            self._relay_sensor = PyBusproSensor(
                gateway.hdl,
                (relay_subnet, relay_device),
                channel_number=relay_channel,
            )

        self._attr_unique_id = build_unique_id(entry.entry_id, device_cfg)
        self._attr_device_info = build_device_info(entry, device_cfg)
        self._attr_name = None

        features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )
        if self._preset_modes_cfg:
            features |= ClimateEntityFeature.PRESET_MODE
        self._attr_supported_features = features

    async def async_added_to_hass(self) -> None:
        """Register update callbacks."""
        await super().async_added_to_hass()

        async def _after_update(_device) -> None:
            self.async_write_ha_state()

        self._climate.register_device_updated_cb(_after_update)

        if self._relay_sensor is not None:

            async def _after_relay(device) -> None:
                self._relay_sensor_is_on = device.single_channel_is_on
                self.async_write_ha_state()

            self._relay_sensor.register_device_updated_cb(_after_relay)

    # ----- state ----------------------------------------------------------
    @property
    def current_temperature(self) -> float | None:
        """Return current measured temperature."""
        return self._climate.temperature

    @property
    def target_temperature(self) -> float | None:
        """Return setpoint."""
        return self._climate.target_temperature

    @property
    def preset_modes(self) -> list[str] | None:
        """Return configured preset modes (or None to hide picker)."""
        if not self._preset_modes_cfg:
            return None
        return [m for m in self._preset_modes_cfg if m in HA_PRESET_TO_HDL]

    @property
    def preset_mode(self) -> str | None:
        """Return current preset mode."""
        mode = self._climate.mode
        return HDL_TO_HA_PRESET.get(mode, PRESET_NONE)

    @property
    def hvac_mode(self) -> HVACMode:
        """Return current HVAC mode."""
        return HVACMode.HEAT if self._climate.is_on else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction:
        """Return current HVAC action (heating/idle/off)."""
        if not self._climate.is_on:
            return HVACAction.OFF
        if self._relay_sensor is None:
            # Without a relay sensor we can only say "heating" generically.
            return HVACAction.HEATING
        return HVACAction.HEATING if self._relay_sensor_is_on else HVACAction.IDLE

    # ----- commands -------------------------------------------------------
    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set the preset (mode)."""
        if preset_mode not in HA_PRESET_TO_HDL:
            preset_mode = PRESET_NONE
        ctrl = ControlFloorHeatingStatus()
        ctrl.mode = HA_PRESET_TO_HDL[preset_mode]
        await self._climate.control_heating_status(ctrl)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode."""
        ctrl = ControlFloorHeatingStatus()
        if hvac_mode == HVACMode.OFF:
            ctrl.status = OnOffStatus.OFF.value
        elif hvac_mode == HVACMode.HEAT:
            ctrl.status = OnOffStatus.ON.value
        else:
            _LOGGER.warning("Unsupported HVAC mode: %s", hvac_mode)
            return
        await self._climate.control_heating_status(ctrl)

    async def async_turn_on(self) -> None:
        """Turn heating on."""
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        """Turn heating off."""
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set target temperature for the active preset."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return

        ctrl = ControlFloorHeatingStatus()
        target = int(temperature)
        preset = HDL_TO_HA_PRESET.get(self._climate.mode, PRESET_NONE)

        if preset == PRESET_HOME:
            ctrl.day_temperature = target
        elif preset == PRESET_SLEEP:
            ctrl.night_temperature = target
        elif preset == PRESET_AWAY:
            ctrl.away_temperature = target
        else:
            ctrl.normal_temperature = target

        await self._climate.control_heating_status(ctrl)


class ARHDLAcClimate(ARHDLBaseEntity, ClimateEntity):
    """An air conditioner controlled through an IR emitter module's live AC
    panel channels (e.g. HDL-MIRC04.40, GitHub issue #17).

    Power, target temperature, HVAC mode (Cool/Heat/Fan/Auto/Dry) and fan
    speed (Auto/Low/Medium/High) are all confirmed and exposed, per HDL's
    own official AC control spec as well as real captures from this
    issue. See the AirConditioner docstring in pybuspro/devices/climate.py
    for the full writeup and provenance.
    """

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 1
    # Fallback covering every mode this class knows about; __init__
    # narrows this to CONF_AC_HVAC_MODES when a device is configured with
    # fewer (e.g. a cooling-only unit with no physical Heat mode).
    _attr_hvac_modes = [HVACMode.OFF] + [
        AC_MODE_TO_HA_HVAC_MODE[m] for m in AC_HVAC_MODES
    ]
    _attr_fan_modes = [FAN_AUTO, FAN_LOW, FAN_MEDIUM, FAN_HIGH]

    def __init__(
        self,
        entry: ConfigEntry,
        gateway: ARHDLGateway,
        device_cfg: dict[str, Any],
    ) -> None:
        """Initialize the air conditioner."""
        super().__init__(entry, gateway, device_cfg)

        subnet = int(device_cfg[CONF_SUBNET_ID])
        device = int(device_cfg[CONF_DEVICE_ID])
        hvac_number = int(device_cfg.get(CONF_HVAC_NUMBER, 1))

        self._ac = PyBusproAirConditioner(
            gateway.hdl,
            (subnet, device),
            hvac_number,
            device_cfg.get(CONF_NAME, ""),
        )

        # Only offer the modes this specific unit actually has (config
        # flow default is all of them, unchanged from before this option
        # existed). Unknown entries in stored config are ignored rather
        # than raising, and OFF is always offered regardless.
        configured_modes = device_cfg.get(CONF_AC_HVAC_MODES, AC_HVAC_MODES)
        self._attr_hvac_modes = [HVACMode.OFF] + [
            AC_MODE_TO_HA_HVAC_MODE[m]
            for m in configured_modes
            if m in AC_MODE_TO_HA_HVAC_MODE
        ]

        self._attr_unique_id = build_unique_id(entry.entry_id, device_cfg)
        self._attr_device_info = build_device_info(entry, device_cfg)
        self._attr_name = None
        self._attr_supported_features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.FAN_MODE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )

    async def async_added_to_hass(self) -> None:
        """Register update callbacks."""
        await super().async_added_to_hass()

        async def _after_update(_device) -> None:
            self.async_write_ha_state()

        self._ac.register_device_updated_cb(_after_update)

    # ----- state ----------------------------------------------------------
    @property
    def available(self) -> bool:
        """Unavailable until a real status has been observed on the bus.

        There's no safe default for the protocol bytes this entity doesn't
        understand (see class docstring), so it refuses to guess rather
        than show a possibly-wrong state.
        """
        return super().available and self._ac.available

    @property
    def current_temperature(self) -> float | None:
        """Return the module's own room-temperature reading, if available."""
        return self._ac.current_temperature

    @property
    def target_temperature(self) -> float | None:
        """Return setpoint."""
        return self._ac.target_temperature

    @property
    def hvac_mode(self) -> HVACMode:
        """Return current HVAC mode."""
        if not self._ac.is_on:
            return HVACMode.OFF
        return AC_MODE_TO_HA_HVAC_MODE.get(self._ac.hvac_mode, HVACMode.OFF)

    @property
    def hvac_action(self) -> HVACAction:
        """Return current HVAC action."""
        if not self._ac.is_on:
            return HVACAction.OFF
        return AC_MODE_TO_HVAC_ACTION.get(self._ac.hvac_mode, HVACAction.IDLE)

    @property
    def fan_mode(self) -> str | None:
        """Return current fan speed ("auto"/"low"/"medium"/"high"), or
        None if off or not yet known."""
        if not self._ac.is_on:
            return None
        return self._ac.fan_speed

    # ----- commands -------------------------------------------------------
    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode."""
        if hvac_mode == HVACMode.OFF:
            await self._ac.turn_off()
            return
        mode = HA_HVAC_MODE_TO_AC_MODE.get(hvac_mode)
        if mode is None:
            _LOGGER.warning("Unsupported HVAC mode: %s", hvac_mode)
            return
        await self._ac.set_hvac_mode(mode)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set fan speed."""
        if fan_mode not in (FAN_AUTO, FAN_LOW, FAN_MEDIUM, FAN_HIGH):
            _LOGGER.warning("Unsupported fan mode: %s", fan_mode)
            return
        await self._ac.set_fan_speed(fan_mode)

    async def async_turn_on(self) -> None:
        """Turn the AC on."""
        await self._ac.turn_on()

    async def async_turn_off(self) -> None:
        """Turn the AC off."""
        await self._ac.turn_off()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        await self._ac.set_target_temperature(int(temperature))
