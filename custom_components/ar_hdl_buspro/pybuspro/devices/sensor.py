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
        motion_uv_switch: int | None = None,
        motion_byte_index: int | None = None,
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
        # Universal-switch number a multisensor uses to PUSH motion in real
        # time (201 / 0xC9 on HDL CMS hardware). See the handler below for why
        # this is the only thing that makes a PIR usable.
        self._motion_uv_switch = motion_uv_switch or None
        # Which byte of the 0x1630 broadcast carries motion. Configurable
        # because it is an inference, not a capture: see the 0x1630 branch.
        self._motion_byte_index = motion_byte_index

        # Every distinct value seen at each index of the 0x1630 broadcast.
        # Bytes that never change are constants, padding or unrelated state;
        # the motion flag is by definition one that varies as someone moves.
        # This turns "which byte is motion" into something readable off an
        # entity attribute after walking around, rather than something to
        # guess at or catch in a live log.
        self._broadcast_byte_values: dict[int, set] = {}

        # Which telegram last CHANGED the motion state, and to what. Exposed
        # as entity attributes: when a motion entity reports something the
        # room does not (phantom trips on a fixed cycle, say), the operate code
        # and payload that caused it are the whole diagnosis, and "last
        # telegram of any kind" is not enough to identify it.
        self.motion_last_op = None
        self.motion_last_payload = None
        self.motion_last_value = None

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

    def _set_motion(self, value, op, payload) -> None:
        """Record a motion reading together with the telegram that caused it."""
        self._motion_sensor = value
        try:
            self.motion_last_op = op.name if hasattr(op, "name") else str(op)
            self.motion_last_payload = list(payload)
            self.motion_last_value = value
        except Exception:  # noqa: BLE001 - diagnostics must never break decode
            pass

    def _apply_motion_uv_switch(self, switch_number, status) -> bool:
        """Handle a universal-switch frame that actually carries motion.

        HDL CMS multisensors do NOT reliably report a PIR trip in their polled
        status frame -- motion is latched for only a short window, so a poll
        almost always lands between trips and reads zero. What they do instead
        is PUSH the trip immediately as universal switch 201 (0xC9) via
        UniversalSwitchControlResponse (0xE01D).

        Nothing here listened for that, and the existing universal-switch
        branches only match `_universal_switch_number`, which is None for a
        motion entity -- so every real-time motion push for the entity's own
        device arrived, failed both comparisons and was discarded. The visible
        result: walk past the sensor and the entity never moves off its last
        polled value.

        Returns True if this frame updated motion.
        """
        if self._motion_uv_switch is None:
            return False
        if switch_number != self._motion_uv_switch:
            return False
        self._set_motion(status, OperateCode.UniversalSwitchControlResponse, [switch_number, status])
        return True

    # Hardware whose CMS *sensor-status* frames (0x1644/0x1646/0x1647) encode
    # temperature as (degC + 20) in one unsigned byte. "generic" and None are
    # included because real installs tag CMS multisensors that way and have
    # always read correctly through that bias.
    #
    # NOTE this is a property of those THREE operate codes on this hardware,
    # not of the device as a whole. The same module's 0xE3E5 broadcast carries
    # the true value with no bias, and its floor-heating and 0xE3E7/0xE3E8
    # panel frames never carry one either. Applying the bias in the
    # `temperature` property instead -- as every release up to 4.4.7 did --
    # therefore had to be wrong for some telegram whichever way it was set:
    # a "generic" device reading 0xE3E5 lost 20 degrees, and the
    # sensors-in-one frames (biased in their own decode since 4.4.5) lost 40.
    # Each branch below now normalises to true Celsius on arrival instead.
    _CMS_BIASED_KINDS = ("generic", None, "12in1", "8in1")

    def _store_temperature(self, raw, biased: bool) -> None:
        """Normalise a raw temperature byte to degC and store it."""
        try:
            value = raw - 20 if biased else raw
        except TypeError:
            return
        self._set_whole_temperature(value)

    @property
    def _cms_biased(self) -> bool:
        """True if this device's CMS sensor-status frames carry the +20 bias."""
        return self._device in self._CMS_BIASED_KINDS

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
            self._store_temperature(payload[1], self._cms_biased)
            brightness_high = payload[2]
            brightness_low = payload[3]
            self._set_motion(payload[4], op, payload)
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
            if len(payload) < 10:
                return
            # The polled sensors-in-one frame encodes temperature with a +20
            # offset (20 == 0 degC, so negatives fit an unsigned byte), unlike
            # the 0xE3E5 broadcast which carries the actual value -- which is
            # why the bias is applied here at decode rather than in the
            # `temperature` property. Confirmed on HDL-MSP02.4C hardware:
            # raw 49 -> 29 degC.
            self._store_temperature(payload[1], biased=True)
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
            self._set_motion(payload[7], op, payload)
            self._dry_contact_1_status = payload[8]
            self._dry_contact_2_status = payload[9]
            self._call_device_updated()

        elif op == OperateCode.BroadcastSensorsInOneStatusResponse:
            # 0x1630 is the unsolicited push and does NOT share the polled
            # 0x1605 layout: it has no leading success byte, so every field
            # sits one index lower. Merging the two branches (as 4.4.2-4.4.7
            # did) read every value one byte off.
            #
            # PROVEN from a live capture on a module at 1.84, cross-checked
            # against its own 0xE3E5 broadcast from the same moment:
            #   0x1630 [45, 0, 0, 255, 255, 255, 0, 0, 0, 0, 0, 255]
            #   0xE3E5 [1, 25, 0, 0, 200, 65]  -> whole 25, float32 25.0 degC
            # payload[0] = 45 = 25 + 20. The old code read payload[1] = 0 as
            # the temperature, produced -20, and the "generic" property then
            # subtracted 20 again for -40.
            #
            # ONLY the temperature is decoded here. The remaining fields are
            # not yet confirmed for this opcode -- the capture has 0xFF at
            # indices 3-5 and zeros elsewhere, which cannot distinguish
            # motion from a dry contact from padding, and guessing an index
            # is exactly what produced phantom motion trips. They are logged
            # instead, so a couple more samples settle it.
            if not payload:
                return
            self._store_temperature(payload[0], biased=True)
            # Lux and humidity follow the same uniform one-byte shift. That
            # shift is PROVEN at index 0 (45 -> 25 degC, matching the module's
            # own 0xE3E5 at the same moment) and corroborated at index 3,
            # where 0xFF lands exactly on humidity's documented "no humidity
            # sensor fitted" sentinel. Both are also self-checking by eye:
            # cover the sensor and lux must fall.
            if len(payload) >= 3:
                self._brightness = (payload[1] << 8) | payload[2]
            if len(payload) >= 4:
                humidity = payload[3]
                self._current_humidity = None if humidity == 0xFF else humidity
            # Motion is still NOT taken from this frame. In the only capture
            # available it sits among three consecutive 0xFF bytes and a run
            # of zeros, which cannot distinguish a motion flag from a dry
            # contact or padding -- and picking an index off one sample is
            # what produced phantom trips on a 30-second cycle. It stays
            # unmapped until a capture taken WHILE someone is walking shows
            # which byte changes.
            # Record what each byte has ever been, capped so a counter byte
            # cannot grow this without bound.
            for idx, value in enumerate(payload):
                seen = self._broadcast_byte_values.setdefault(idx, set())
                if len(seen) < 12:
                    seen.add(value)
            if (
                self._motion_byte_index is not None
                and 0 <= self._motion_byte_index < len(payload)
            ):
                self._set_motion(payload[self._motion_byte_index], op, payload)
            _LOGGER_SENSOR.debug(
                "Sensor %s 0x1630 payload %s (motion byte index %s)",
                self._device_address,
                list(payload),
                self._motion_byte_index,
            )
            self._call_device_updated()

        elif op == OperateCode.BroadcastSensorStatusResponse:
            if len(payload) < 7:
                return
            self._store_temperature(payload[0], self._cms_biased)
            self._brightness = (payload[1] << 8) | payload[2]
            self._set_motion(payload[3], op, payload)
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
            self._store_temperature(payload[0], self._cms_biased)
            self._brightness = (payload[1] << 8) | payload[2]
            self._set_motion(payload[3], op, payload)
            self._sonic = payload[4]
            self._dry_contact_1_status = payload[5]
            self._dry_contact_2_status = payload[6]
            self._call_device_updated()

        elif op == OperateCode.ReadFloorHeatingStatusResponse:
            if len(payload) < 2:
                return
            self._store_temperature(payload[1], biased=False)
            self._call_device_updated()

        elif op == OperateCode.BroadcastTemperatureResponse:
            if len(payload) < 2:
                return
            # Confirmed on a module at 1.84: [channel, whole_degC, float32 LE]
            # -- the same shape as the 0xE3E7/0xE3E8 panel pair, and carrying
            # the TRUE value with no +20 bias.
            self._store_temperature(payload[1], biased=False)
            if len(payload) >= 6:
                try:
                    self._current_temperature_precise = round(
                        struct.unpack("<f", bytes(payload[2:6]))[0], 2
                    )
                except (struct.error, ValueError, TypeError):
                    pass
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
                self._set_motion(payload[3], op, payload)
                self._call_device_updated()

        elif op == OperateCode.ReadStatusOfUniversalSwitchResponse:
            switch_number = payload[0]
            status = payload[1]
            if switch_number == self._universal_switch_number:
                self._universal_switch_status = status
                self._call_device_updated()
            elif self._apply_motion_uv_switch(switch_number, status):
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
            elif self._apply_motion_uv_switch(switch_number, status):
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

        # A multisensor that pushes motion on a universal switch also answers
        # a read of it, so ask once per poll for a baseline rather than
        # waiting for the next trip.
        if self._motion_uv_switch is not None:
            uv = _ReadStatusOfUniversalSwitch(self._buspro)
            uv.subnet_id, uv.device_id = self._device_address
            uv.switch_number = self._motion_uv_switch
            await uv.send()


    @property
    def temperature(self):
        """Return the current temperature in degC.

        Every decode branch normalises to true Celsius on arrival (see
        _store_temperature), so nothing is adjusted here. Prefers the
        sub-degree float a panel or 0xE3E5 broadcast supplies over the
        whole-degree byte, which is a truncation of the same reading.
        """
        if self._current_temperature is None:
            return 0
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
    def broadcast_byte_variance(self) -> dict:
        """Return the values seen at each 0x1630 byte index, varying first.

        Read this after walking past the sensor: the index whose values go
        0 -> something and back is the motion flag.
        """
        out = {}
        for idx, values in sorted(self._broadcast_byte_values.items()):
            if len(values) > 1:
                out[f"byte_{idx}"] = sorted(values)
        return out

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
