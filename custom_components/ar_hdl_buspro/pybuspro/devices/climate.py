"""Climate (floor heating) device wrapper."""
from __future__ import annotations

import asyncio

from ..helpers.enums import (
    OperateCode,
    SuccessOrFailure,
    TemperatureMode,
    TemperatureType,
)
from ..helpers.generics import Generics
from .control import _ControlFloorHeatingStatus, _ReadFloorHeatingStatus
from .device import Device


class ControlFloorHeatingStatus:
    """Container for floor-heating control fields (None means 'unchanged')."""

    def __init__(self) -> None:
        self.temperature_type = None
        self.status = None
        self.mode = None
        self.normal_temperature = None
        self.day_temperature = None
        self.night_temperature = None
        self.away_temperature = None


class Climate(Device):
    """HDL floor-heating panel wrapper."""

    def __init__(self, buspro, device_address, name: str = "") -> None:
        """Initialize the climate device."""
        super().__init__(buspro, device_address, name)
        self._buspro = buspro
        self._device_address = device_address

        self._temperature_type = None  # Celsius/Fahrenheit
        self._status = None            # On/Off
        self._mode = None              # 1..5
        self._current_temperature = None
        self._normal_temperature = None
        self._day_temperature = None
        self._night_temperature = None
        self._away_temperature = None

        self.register_telegram_received_cb(self._telegram_received_cb)
        self._call_read_current_heating_status(run_from_init=True)

    def _telegram_received_cb(self, telegram) -> None:
        op = telegram.operate_code
        payload = telegram.payload or []
        if op == OperateCode.ReadFloorHeatingStatusResponse:
            self._temperature_type = payload[0]
            self._current_temperature = payload[1]
            self._status = payload[2]
            self._mode = payload[3]
            self._normal_temperature = payload[4]
            self._day_temperature = payload[5]
            self._night_temperature = payload[6]
            self._away_temperature = payload[7]
            self._call_device_updated()

        elif op == OperateCode.ControlFloorHeatingStatusResponse:
            # payload[0] is the raw success byte (0xF8 = success); compare the
            # int, not the enum member (bytes) -- the enum compare is never True.
            success_or_fail = payload[0]
            if success_or_fail == SuccessOrFailure.Success.value[0]:
                self._temperature_type = payload[1]
                self._status = payload[2]
                self._mode = payload[3]
                self._normal_temperature = payload[4]
                self._day_temperature = payload[5]
                self._night_temperature = payload[6]
                self._away_temperature = payload[7]
            self._call_device_updated()

        elif op == OperateCode.BroadcastTemperatureResponse:
            self._current_temperature = payload[1]
            self._call_device_updated()

    async def read_heating_status(self) -> None:
        """Trigger a read of the current heating status."""
        req = _ReadFloorHeatingStatus(self._buspro)
        req.subnet_id, req.device_id = self._device_address
        await req.send()

    def _telegram_received_control_heating_status_cb(self, telegram, floor_heating_status) -> None:
        """Two-step "merge then write" callback for control_heating_status."""
        if telegram.operate_code != OperateCode.ReadFloorHeatingStatusResponse:
            return

        self.unregister_telegram_received_cb(
            self._telegram_received_control_heating_status_cb, floor_heating_status
        )

        payload = telegram.payload
        temperature_type = payload[0]
        status = payload[2]
        mode = payload[3]
        normal_temperature = payload[4]
        day_temperature = payload[5]
        night_temperature = payload[6]
        away_temperature = payload[7]

        # Override fields the caller wants to change
        for attr in (
            "temperature_type",
            "status",
            "mode",
            "normal_temperature",
            "day_temperature",
            "night_temperature",
            "away_temperature",
        ):
            new_value = getattr(floor_heating_status, attr, None)
            if new_value is not None:
                if attr == "temperature_type":
                    temperature_type = new_value
                elif attr == "status":
                    status = new_value
                elif attr == "mode":
                    mode = new_value
                elif attr == "normal_temperature":
                    normal_temperature = new_value
                elif attr == "day_temperature":
                    day_temperature = new_value
                elif attr == "night_temperature":
                    night_temperature = new_value
                elif attr == "away_temperature":
                    away_temperature = new_value

        cfhs = _ControlFloorHeatingStatus(self._buspro)
        cfhs.subnet_id, cfhs.device_id = self._device_address
        cfhs.temperature_type = temperature_type
        cfhs.status = status
        cfhs.mode = mode
        cfhs.normal_temperature = normal_temperature
        cfhs.day_temperature = day_temperature
        cfhs.night_temperature = night_temperature
        cfhs.away_temperature = away_temperature

        async def _send():
            await cfhs.send()

        asyncio.ensure_future(_send(), loop=self._buspro.loop)

    async def control_heating_status(
        self, floor_heating_status: ControlFloorHeatingStatus
    ) -> None:
        """Apply the partial floor-heating status (None fields untouched)."""
        self.register_telegram_received_cb(
            self._telegram_received_control_heating_status_cb, floor_heating_status
        )
        req = _ReadFloorHeatingStatus(self._buspro)
        req.subnet_id, req.device_id = self._device_address
        await req.send()

    def _call_read_current_heating_status(self, run_from_init: bool = False) -> None:
        async def _read():
            if run_from_init:
                await asyncio.sleep(5)
            req = _ReadFloorHeatingStatus(self._buspro)
            req.subnet_id, req.device_id = self._device_address
            try:
                await req.send()
            except Exception:  # noqa: BLE001
                self._buspro.logger.debug(
                    "Initial climate read failed for %s", self._device_address
                )

        asyncio.ensure_future(_read(), loop=self._buspro.loop)

    @property
    def unit_of_measurement(self):
        return Generics().get_enum_value(TemperatureType, self._temperature_type)

    @property
    def is_on(self) -> bool:
        return self._status == 1

    @property
    def mode(self):
        return self._mode

    @property
    def temperature(self):
        return self._current_temperature

    @property
    def day_temperature(self):
        return self._day_temperature

    @property
    def night_temperature(self):
        return self._night_temperature

    @property
    def away_temperature(self):
        return self._away_temperature

    @property
    def device_identifier(self) -> str:
        return f"{self._device_address}"

    @property
    def target_temperature(self):
        """Return the active setpoint based on current mode."""
        if self._mode == TemperatureMode.Normal.value:
            return self._normal_temperature
        if self._mode == TemperatureMode.Day.value:
            return self._day_temperature
        if self._mode == TemperatureMode.Away.value:
            return self._away_temperature
        if self._mode == TemperatureMode.Night.value:
            return self._night_temperature
        return self._normal_temperature
