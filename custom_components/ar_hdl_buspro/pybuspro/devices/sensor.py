"""Sensor device wrapper supporting many HDL sensor variants."""
from __future__ import annotations

import asyncio
import logging

_LOGGER_SENSOR = logging.getLogger(__name__)

from ..helpers.enums import OnOffStatus, OperateCode, SuccessOrFailure
from .control import (
    _ReadDryContactStatus,
    _ReadFloorHeatingStatus,
    _ReadSensorsInOneStatus,
    _ReadSensorStatus,
    _ReadStatusOfChannels,
    _ReadStatusOfUniversalSwitch,
)
from .device import Device


class Sensor(Device):
    """A general-purpose HDL Buspro sensor wrapper."""

    def __init__(
        self,
        buspro,
        device_address,
        universal_switch_number: int | None = None,
        channel_number: int | None = None,
        device: str | None = None,
        switch_number: int | None = None,
        name: str = "",
        delay_read_current_state_seconds: int = 0,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(buspro, device_address, name)
        self._buspro = buspro
        self._device_address = device_address
        self._universal_switch_number = universal_switch_number
        self._channel_number = channel_number
        self._name = name
        self._device = device
        self._switch_number = switch_number

        self._current_temperature = None
        self._brightness = None
        self._motion_sensor = None
        self._sonic = None
        self._dry_contact_1_status = None
        self._dry_contact_2_status = None
        self._universal_switch_status = OnOffStatus.OFF
        self._channel_status = 0
        self._switch_status = 0

        self.register_telegram_received_cb(self._telegram_received_cb)
        self._call_read_current_status_of_sensor(run_from_init=True)

    def _telegram_received_cb(self, telegram) -> None:  # noqa: PLR0912 - mirrors HDL protocol
        op = telegram.operate_code
        payload = telegram.payload or []

        # Keep the raw last telegram for diagnostics (exposed as entity
        # attributes so payload layouts can be verified from the HA UI).
        try:
            self.last_telegram_op = op.name if hasattr(op, "name") else str(op)
            self.last_telegram_payload = list(payload)
        except Exception:  # noqa: BLE001 - diagnostics must never break decode
            pass

        if op == OperateCode.ReadSensorStatusResponse:
            # NOTE: payload[0] is a raw int byte; SuccessOrFailure values are
            # bytes objects, so it must be compared against .value[0] (0xF8).
            # Comparing against the enum member directly is always False --
            # that upstream pybuspro bug meant direct sensor reads never
            # triggered a state update in HA.
            success_or_fail = payload[0]
            self._current_temperature = payload[1]
            brightness_high = payload[2]
            brightness_low = payload[3]
            self._motion_sensor = payload[4]
            self._sonic = payload[5]
            self._dry_contact_1_status = payload[6]
            self._dry_contact_2_status = payload[7]
            # Store lux unconditionally, like temperature/motion above. Some
            # sensor firmware doesn't use 0xF8 in the success byte, which
            # previously left illuminance permanently unavailable while the
            # other readings from the very same reply worked fine.
            # Lux is a 16-bit big-endian value (high byte * 256 + low).
            self._brightness = (brightness_high << 8) | brightness_low
            if success_or_fail != SuccessOrFailure.Success.value[0]:
                _LOGGER_SENSOR.debug(
                    "Sensor %s ReadSensorStatusResponse success byte 0x%02X",
                    self._device_address,
                    success_or_fail,
                )
            self._call_device_updated()

        elif op == OperateCode.ReadSensorsInOneStatusResponse:
            self._current_temperature = payload[1]
            # Lux occupies the same slots as in the 12in1 reply (hi, lo).
            self._brightness = (payload[2] << 8) | payload[3]
            self._motion_sensor = payload[7]
            self._dry_contact_1_status = payload[8]
            self._dry_contact_2_status = payload[9]
            self._call_device_updated()

        elif op == OperateCode.BroadcastSensorStatusResponse:
            self._current_temperature = payload[0]
            self._brightness = (payload[1] << 8) | payload[2]
            self._motion_sensor = payload[3]
            self._sonic = payload[4]
            self._dry_contact_1_status = payload[5]
            self._dry_contact_2_status = payload[6]
            self._call_device_updated()

        elif op == OperateCode.BroadcastSensorStatusAutoResponse:
            self._current_temperature = payload[0]
            if self._device == "12in1":
                self._current_temperature -= 20
            self._brightness = (payload[1] << 8) | payload[2]
            self._motion_sensor = payload[3]
            self._sonic = payload[4]
            self._dry_contact_1_status = payload[5]
            self._dry_contact_2_status = payload[6]
            self._call_device_updated()

        elif op == OperateCode.ReadFloorHeatingStatusResponse:
            self._current_temperature = payload[1]
            self._call_device_updated()

        elif op == OperateCode.BroadcastTemperatureResponse:
            self._current_temperature = payload[1]
            self._call_device_updated()

        elif op == OperateCode.ReadStatusOfUniversalSwitchResponse:
            switch_number = payload[0]
            status = payload[1]
            if switch_number == self._universal_switch_number:
                self._universal_switch_status = status
                self._call_device_updated()

        elif op == OperateCode.BroadcastStatusOfUniversalSwitch:
            if (
                self._universal_switch_number is not None
                and self._universal_switch_number <= payload[0]
            ):
                self._universal_switch_status = payload[self._universal_switch_number]
                self._call_device_updated()

        elif op == OperateCode.UniversalSwitchControlResponse:
            switch_number = payload[0]
            status = payload[1]
            if switch_number == self._universal_switch_number:
                self._universal_switch_status = status
                self._call_device_updated()

        elif op == OperateCode.ReadStatusOfChannelsResponse:
            if (
                self._channel_number is not None
                and self._channel_number <= payload[0]
            ):
                self._channel_status = payload[self._channel_number]
                self._call_device_updated()

        elif op == OperateCode.SingleChannelControlResponse:
            if self._channel_number == payload[0]:
                self._channel_status = payload[2]
                self._call_device_updated()

        elif op == OperateCode.ReadDryContactStatusResponse:
            if self._switch_number == payload[1]:
                self._switch_status = payload[2]
                self._call_device_updated()

    async def read_sensor_status(self) -> None:
        """Read the appropriate kind of status for this sensor variant."""
        if self._universal_switch_number is not None:
            req = _ReadStatusOfUniversalSwitch(self._buspro)
            req.subnet_id, req.device_id = self._device_address
            req.switch_number = self._universal_switch_number
            await req.send()
        elif self._channel_number is not None:
            req = _ReadStatusOfChannels(self._buspro)
            req.subnet_id, req.device_id = self._device_address
            await req.send()
        elif self._device == "dlp":
            req = _ReadFloorHeatingStatus(self._buspro)
            req.subnet_id, req.device_id = self._device_address
            await req.send()
        elif self._device == "dry_contact" or self._switch_number is not None:
            req = _ReadDryContactStatus(self._buspro)
            req.subnet_id, req.device_id = self._device_address
            req.switch_number = self._switch_number or 1
            await req.send()
        elif self._device == "sensors_in_one":
            req = _ReadSensorsInOneStatus(self._buspro)
            req.subnet_id, req.device_id = self._device_address
            await req.send()
        else:
            req = _ReadSensorStatus(self._buspro)
            req.subnet_id, req.device_id = self._device_address
            await req.send()

    @property
    def temperature(self):
        """Return the current temperature, applying hardware-specific offset."""
        if self._current_temperature is None:
            return 0
        if self._device in ("dlp", "12in1"):
            return self._current_temperature
        return self._current_temperature - 20

    @property
    def brightness(self) -> int:
        """Return the current brightness."""
        if self._brightness is None:
            return 0
        return self._brightness

    @property
    def movement(self) -> bool:
        """Return True if motion has been detected."""
        return bool(self._motion_sensor) or bool(self._sonic)

    @property
    def dry_contact_1_is_on(self) -> bool:
        return self._dry_contact_1_status == 1

    @property
    def dry_contact_2_is_on(self) -> bool:
        return self._dry_contact_2_status == 1

    @property
    def universal_switch_is_on(self) -> bool:
        return self._universal_switch_status == 1

    @property
    def single_channel_is_on(self) -> bool:
        return self._channel_status > 0

    @property
    def switch_status(self) -> bool:
        return self._switch_status == 1

    @property
    def device_identifier(self) -> str:
        """Return a stable identifier including selectors."""
        return (
            f"{self._device_address}-"
            f"{self._universal_switch_number}-"
            f"{self._channel_number}-"
            f"{self._switch_number}"
        )

    def _call_read_current_status_of_sensor(self, run_from_init: bool = False) -> None:
        async def _read():
            if run_from_init:
                await asyncio.sleep(5)
            try:
                await self.read_sensor_status()
            except Exception:  # noqa: BLE001
                self._buspro.logger.debug(
                    "Initial sensor status read failed for %s",
                    self._device_address,
                )

        asyncio.ensure_future(_read(), loop=self._buspro.loop)
