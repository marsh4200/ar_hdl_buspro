"""Sensor device wrapper supporting many HDL sensor variants."""
from __future__ import annotations

import asyncio
import logging
import random
import struct

_LOGGER_SENSOR = logging.getLogger(__name__)

from ..helpers.enums import OnOffStatus, OperateCode, SuccessOrFailure
from .control import (
    _ReadDryContactStatus,
    _ReadFloorHeatingStatus,
    _ReadMotionSensorStatus,
    _ReadSensorsInOneStatus,
    _ReadSensorStatus,
    _ReadStatusOfChannels,
    _ReadStatusOfUniversalSwitch,
    _ReadTemperature,
)
from .device import Device

# How often to re-issue the startup status read while this sensor has still
# never produced a single reading. A one-shot read is fragile: on a full HA
# restart every Sensor schedules its read for the same ~5s mark, so a burst
# of simultaneous UDP requests hits the gateway while the network stack is
# still warming up -- lose one packet (request or response, no retry) and a
# temperature sensor sits "unavailable" and a motion sensor sits "clear"
# forever, with nothing in the log to say why. This is the exact same
# retry-until-first-reading pattern device.py already uses for channel
# status (_CHANNEL_STATUS_RETRY_SECONDS); sensors were simply never given
# it. It stops for good the moment any real reading arrives, so it is not
# polling -- a sensor that works costs one extra frame at most.
_SENSOR_STATUS_RETRY_SECONDS = 20


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
        temperature_channel: int = 1,
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
        # Channel used by the MPTL/panel family's channel-addressed
        # temperature read (0xE3E7). The onboard sensor answers on ch 1 on
        # every panel observed so far.
        self._temperature_channel = temperature_channel or 1

        self._current_temperature = None
        # Sub-degree temperature from the panel's float32 field, when the
        # telegram carries one. Preferred over the whole-degree byte, which
        # is a truncation of it.
        self._current_temperature_precise = None
        self._current_humidity = None
        self._brightness = None
        self._motion_sensor = None
        self._sonic = None
        self._dry_contact_1_status = None
        self._dry_contact_2_status = None
        self._universal_switch_status = OnOffStatus.OFF
        self._channel_status = 0
        self._switch_status = 0
        # True once any telegram for this device has actually been decoded.
        # Drives the startup retry loop -- see _SENSOR_STATUS_RETRY_SECONDS.
        self._got_reading = False

        self.register_telegram_received_cb(self._telegram_received_cb)
        self._call_read_current_status_of_sensor(run_from_init=True)

    def _set_whole_temperature(self, value) -> None:
        """Store a whole-degree temperature byte, keeping sub-degree detail.

        A device can report the same physical reading on two telegrams at
        different resolutions -- the Granite Display sends both
        ReadFloorHeatingStatusResponse (whole byte 26) and
        ReadTemperatureResponse (float 26.99) for one 26.99 degC room. Blindly
        dropping the float on every coarse frame made the entity flap between
        26 and 26.99 in the recorder. So keep the float while it still
        truncates to the incoming whole degree, and discard it once the
        coarse reading has genuinely moved on.
        """
        self._current_temperature = value
        precise = self._current_temperature_precise
        if precise is None:
            return
        try:
            if int(precise) != int(value):
                self._current_temperature_precise = None
        except (TypeError, ValueError):
            self._current_temperature_precise = None

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
            if len(payload) < 8:
                return
            # NOTE: payload[0] is a raw int byte; SuccessOrFailure values are
            # bytes objects, so it must be compared against .value[0] (0xF8).
            # Comparing against the enum member directly is always False --
            # that upstream pybuspro bug meant direct sensor reads never
            # triggered a state update in HA.
            success_or_fail = payload[0]
            self._set_whole_temperature(payload[1])
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

        elif op in (
            OperateCode.ReadSensorsInOneStatusResponse,
            OperateCode.BroadcastSensorsInOneStatusResponse,
        ):
            if len(payload) < 10:
                return
            self._set_whole_temperature(payload[1])
            # Lux occupies the same slots as in the 12in1 reply (hi, lo).
            self._brightness = (payload[2] << 8) | payload[3]
            # Humidity (%RH), confirmed at payload[4] -- see the
            # SENSOR_KIND_HUMIDITY comment in const.py for the source.
            # 0xFF is a documented sentinel for "no humidity sensor wired
            # to this sensors-in-one module" (independently confirmed in
            # a second HDL Buspro implementation, Frequencies/home_assistant_
            # buspro) -- treat it the same as "not read yet" rather than
            # showing a bogus 255% reading.
            humidity = payload[4]
            self._current_humidity = None if humidity == 0xFF else humidity
            self._motion_sensor = payload[7]
            self._dry_contact_1_status = payload[8]
            self._dry_contact_2_status = payload[9]
            self._call_device_updated()

        elif op == OperateCode.BroadcastSensorStatusResponse:
            if len(payload) < 7:
                return
            self._set_whole_temperature(payload[0])
            self._brightness = (payload[1] << 8) | payload[2]
            self._motion_sensor = payload[3]
            self._sonic = payload[4]
            self._dry_contact_1_status = payload[5]
            self._dry_contact_2_status = payload[6]
            self._call_device_updated()

        elif op == OperateCode.BroadcastSensorStatusAutoResponse:
            if len(payload) < 7:
                return
            # NOTE: no -20 here. The bias belongs in exactly one place -- the
            # `temperature` property -- and it used to be applied BOTH here
            # and (for other hw kinds) there, so a 12in1 reporting via this
            # broadcast got biased once and via ReadSensorStatusResponse not
            # at all. Storing the raw byte on every path and biasing once on
            # read is the only way those two agree.
            self._set_whole_temperature(payload[0])
            self._brightness = (payload[1] << 8) | payload[2]
            self._motion_sensor = payload[3]
            self._sonic = payload[4]
            self._dry_contact_1_status = payload[5]
            self._dry_contact_2_status = payload[6]
            self._call_device_updated()

        elif op == OperateCode.ReadFloorHeatingStatusResponse:
            if len(payload) < 2:
                return
            self._set_whole_temperature(payload[1])
            self._call_device_updated()

        elif op == OperateCode.BroadcastTemperatureResponse:
            if len(payload) < 2:
                return
            self._set_whole_temperature(payload[1])
            self._call_device_updated()

        elif op == OperateCode.ReadTemperatureResponse:
            # MPTL/panel family channel-addressed temperature (0xE3E8):
            #   [channel, signed_whole_degrees, <float32 LE degrees>]
            # Confirmed on a Granite Display (type 0x0890) -- see the
            # ReadTemperature comment in helpers/enums.py for the capture.
            #
            # Panels answer on every channel they own, all reporting the one
            # onboard sensor, so only accept our configured channel rather
            # than letting eight channels fight over one entity.
            if len(payload) >= 2 and payload[0] == self._temperature_channel:
                whole = payload[1]
                if whole > 127:  # signed: sub-zero temperatures
                    whole -= 256
                self._current_temperature = whole
                if len(payload) >= 6:
                    try:
                        self._current_temperature_precise = round(
                            struct.unpack("<f", bytes(payload[2:6]))[0], 2
                        )
                    except (struct.error, ValueError, TypeError):
                        self._current_temperature_precise = None
                else:
                    self._current_temperature_precise = None
                self._call_device_updated()

        elif op == OperateCode.ReadMotionSensorStatusResponse:
            # CMS-PIR style motion-only module: motion flag at index 3.
            if len(payload) >= 4:
                self._motion_sensor = payload[3]
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
        elif self._device == "pir":
            # CMS-PIR style motion-only module: answers 0xDB00 and nothing
            # else. Polled with the generic _ReadSensorStatus below it never
            # replies at all, so its entity stays "clear" for good.
            req = _ReadMotionSensorStatus(self._buspro)
            req.subnet_id, req.device_id = self._device_address
            await req.send()
        elif self._device == "panel":
            # MPTL/Granite Display family: channel-addressed temperature.
            req = _ReadTemperature(self._buspro)
            req.subnet_id, req.device_id = self._device_address
            req.channel_number = self._temperature_channel
            await req.send()
        else:
            req = _ReadSensorStatus(self._buspro)
            req.subnet_id, req.device_id = self._device_address
            await req.send()

    # Hardware that encodes temperature as (degC + 20) in a single unsigned
    # byte, so 0 means -20 degC. This is a property of the CMS multi-sensor
    # firmware ONLY -- DLP/MPTL panels, sensors-in-one modules and floor
    # heating all report plain Celsius.
    #
    # This list used to be inverted: the property returned the raw byte for
    # "dlp" and "12in1" and subtracted 20 from EVERYTHING ELSE, which is the
    # opposite of the truth and of both reference implementations (one of
    # which carries the line `#removed this offset of 20 degree`). The
    # visible result on a Granite Display reporting a real 26 degC was a
    # sensor pinned at 6 degC that dipped to 5 and never rose past 6 -- the
    # room temperature, minus twenty, forever. A 12in1 was wrong in the
    # other direction, and inconsistently so: it got the bias applied in the
    # BroadcastSensorStatusAutoResponse decode but not on its poll reply.
    _TEMPERATURE_BIAS_20_KINDS = ("12in1", "8in1")

    @property
    def temperature(self):
        """Return the current temperature in degC.

        Prefers the sub-degree float a panel supplies on 0xE3E8 over the
        whole-degree byte, which is a truncation of the same reading.
        """
        if self._current_temperature is None:
            return 0
        if self._device in self._TEMPERATURE_BIAS_20_KINDS:
            return self._current_temperature - 20
        if self._current_temperature_precise is not None:
            return self._current_temperature_precise
        return self._current_temperature

    @property
    def brightness(self) -> int:
        """Return the current brightness."""
        if self._brightness is None:
            return 0
        return self._brightness

    @property
    def humidity(self) -> int:
        """Return the current relative humidity percentage.

        Only ever populated from ReadSensorsInOneStatusResponse (the
        "sensors_in_one" hw kind) -- see SENSOR_KIND_HUMIDITY in const.py.
        """
        if self._current_humidity is None:
            return 0
        return self._current_humidity

    @property
    def movement(self) -> bool | None:
        """Return True if motion has been detected, None if never read.

        Returning False when nothing has ever been received is how a motion
        entity that has never seen a single telegram in its life ends up
        confidently reporting "Clear" -- indistinguishable, in the UI and in
        the recorder, from a working sensor watching an empty room. Return
        None instead so HA shows "Unknown" and a broken sensor is visible as
        broken. Both reference implementations behave this way.
        """
        if self._motion_sensor is None and self._sonic is None:
            return None
        return bool(self._motion_sensor) or bool(self._sonic)

    @property
    def has_reading(self) -> bool:
        """Return True once any payload for this device has been decoded.

        Used by the startup retry loop to know when to stop re-reading. This
        can't be inferred from the stored values, because 0 and OFF are
        legitimate readings that are indistinguishable from the initial
        defaults -- so it's recorded when a decode actually happens instead.
        """
        return self._got_reading

    def _call_device_updated(self) -> None:
        """Record that a real decode happened, then notify as usual."""
        self._got_reading = True
        super()._call_device_updated()

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
        """Schedule a status read for this sensor.

        At startup this retries every _SENSOR_STATUS_RETRY_SECONDS until a
        real telegram has been decoded, then stops permanently -- the same
        contract device.py uses for channel status. Previously this was a
        single attempt with no retry, so one dropped UDP packet during the
        startup burst left the entity stranded at its default state until HA
        was restarted.
        """

        async def _read():
            if not run_from_init:
                try:
                    await self.read_sensor_status()
                except Exception:  # noqa: BLE001
                    self._buspro.logger.debug(
                        "Sensor status read failed for %s", self._device_address
                    )
                return

            # Stagger the first attempt so a restart with many sensors
            # doesn't fire them all in one UDP burst.
            await asyncio.sleep(5 + random.uniform(0, 2))
            while not self._got_reading:
                try:
                    await self.read_sensor_status()
                except Exception:  # noqa: BLE001
                    self._buspro.logger.debug(
                        "Initial sensor status read failed for %s",
                        self._device_address,
                    )
                await asyncio.sleep(_SENSOR_STATUS_RETRY_SECONDS)

        asyncio.ensure_future(_read(), loop=self._buspro.loop)
