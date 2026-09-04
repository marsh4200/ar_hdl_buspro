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
from .control import _ControlFloorHeatingStatus, _GenericControl, _ReadFloorHeatingStatus
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


    """HDL air conditioner wrapper, controlled through an IR emitter
    module's live AC panel channels (e.g. HDL-MIRC04.40, GitHub issue #17).

    Unlike Climate (a DLP floor-heating panel that owns its own bus
    address), one IR module serves up to 4 AC units sharing the module's
    address, distinguished within the payload by "HVAC No." (1-4).

    Protocol notes -- reverse-engineered from live bus captures on issue
    #17, not from an HDL protocol document. The 13-byte ControlACStatus /
    ControlACStatusResponse payload:
      - byte 0        = HVAC No.
      - byte 5 (mirrored in byte 11) = target temperature, whole degrees C
      - byte 7         = derived from mode + fan speed while on, see
        _MODE_BASE below; not set directly, computed in _send_update()
      - byte 8         = power (1 = on, 0 = off)
      - byte 9         = mode, see MODE_TO_BYTE below
      - byte 10        = fan speed, see FAN_TO_BYTE below
      - bytes 1,2,3,4,6,12 -- meaning not established, never touched.

    This class never fabricates values for the unidentified bytes. It
    only ever echoes back the last full payload actually observed on the
    bus (from this module's own status broadcast, or a best-effort
    startup read), changing just the confirmed field(s) a command asks to
    change. Until a real payload has been observed, writes are refused
    rather than guessed -- see _send_update().
    """

    # HVAC mode <-> payload[9], confirmed from a real narrated test on
    # issue #17 (module 1.42, HVAC 3): the reporter cycled through every
    # mode in a fixed order with wall-clock times ("test start 9:18pm ...
    # test ended 9:31pm"), which we matched against the real outbound
    # ControlACStatus command frames (source (1,60) -> target (1,42), not
    # the broadcast *Response echoes) by their own timestamps -- 13 named
    # actions against 13 captured command frames spanning 21:18:37 to
    # 21:30:54 (~12m17s, matching "9:18-9:31pm" almost to the second), one
    # pair of which was an exact duplicate (a retransmit, not a distinct
    # action) which accounts for the reporter's own self-flagged uncertain
    # step ("flipped alone to cool again from itself"). This superseded an
    # earlier partial mapping (0=Fan/1=Cool/4=Heat) drawn from a different
    # capture without real per-step timestamps, which turned out to be a
    # mis-alignment -- that's what produced the "byte9=4 can't be Heat and
    # Dry at once" contradiction flagged previously. This mapping is the
    # one to trust; the old one was wrong.
    MODE_TO_BYTE = {"cool": 0, "heat": 1, "fan_only": 2, "auto": 3, "dry": 4}
    BYTE_TO_MODE = {v: k for k, v in MODE_TO_BYTE.items()}

    # Fan speed <-> payload[10], confirmed the same way (three explicit,
    # isolated fan-speed button presses in the same test). The app's
    # "Auto" fan setting produced the same byte value as "Low" in this
    # capture, so it isn't exposed as a distinct option here -- only the
    # three unambiguous speeds are.
    FAN_TO_BYTE = {"high": 1, "medium": 2, "low": 3}
    BYTE_TO_FAN = {v: k for k, v in FAN_TO_BYTE.items()}

    # payload[7] tracks (mode, fan speed) while the unit is on -- in every
    # power-on frame of that same capture, byte7 == _MODE_BASE[mode] +
    # fan_byte, with zero exceptions across 11 independent frames. It's
    # likely a slot index into this installation's own programmed IR-code
    # library (per the datasheet's 24-device/100-code description) rather
    # than a portable protocol field, so these base values are only known
    # to be correct for this reporter's installation -- but since this
    # class only ever mutates a real observed payload for one specific
    # configured device, that's exactly the case that matters here.
    _MODE_BASE = {0: 32, 1: 16, 2: 32, 3: 0, 4: 64}

    def __init__(
        self, buspro, device_address, hvac_number: int, name: str = ""
    ) -> None:
        """Initialize the air conditioner."""
        super().__init__(buspro, device_address, name)
        self._buspro = buspro
        self._device_address = device_address
        self._hvac_number = hvac_number
        # Last full 13-int payload observed for this HVAC No., or None if
        # nothing has been seen yet.
        self._raw_payload: list[int] | None = None

        self.register_telegram_received_cb(self._telegram_received_cb)
        self._call_read_current_status(run_from_init=True)

    def _telegram_received_cb(self, telegram) -> None:
        if telegram.operate_code not in (
            OperateCode.ControlACStatusResponse,
            OperateCode.ReadACStatusResponse,
        ):
            return
        payload = telegram.payload or []
        # byte 0 of the payload is the HVAC No. this frame is about -- the
        # IR module's single bus address serves up to 4 of them, so ignore
        # frames for a different HVAC No. than this entity.
        if len(payload) < 12 or payload[0] != self._hvac_number:
            return
        # A HVAC No. with no AC actually wired to it reports back as all
        # 0xFF (255) -- confirmed from a real ReadACStatusResponse capture
        # (e.g. [2, 0, 27, 22, 25, 25, 25, 32, 255, 255, 255, 255, 255]).
        # Accepting that as real state would make this entity look
        # available with a plausible-but-fake temperature, and would send
        # 0xFF bytes onto the bus the moment someone tried to control it.
        # Treat it as "nothing configured here" instead.
        if all(b == 255 for b in payload[8:13]):
            if self._raw_payload is not None:
                # We previously had real data and now see the empty-slot
                # sentinel -- surface this loudly, it likely means the
                # configured HVAC No. is wrong for this module.
                self._buspro.logger.warning(
                    "AC %s HVAC %s now reports as unconfigured (all-0xFF) "
                    "after previously showing real status -- check the "
                    "HVAC No. for this entity is correct.",
                    self._device_address,
                    self._hvac_number,
                )
                self._raw_payload = None
                self._call_device_updated()
            else:
                self._buspro.logger.debug(
                    "AC %s HVAC %s reports as unconfigured (all-0xFF) -- "
                    "no AC unit wired to this HVAC No. on this module.",
                    self._device_address,
                    self._hvac_number,
                )
            return
        self._raw_payload = list(payload)
        self._call_device_updated()

    @property
    def available(self) -> bool:
        """Return True once a real payload has been observed on the bus."""
        return self._raw_payload is not None

    @property
    def is_on(self) -> bool:
        return bool(self._raw_payload) and self._raw_payload[8] == 1

    @property
    def target_temperature(self):
        if not self._raw_payload:
            return None
        return self._raw_payload[5]

    @property
    def current_temperature(self):
        """Best-effort room temperature reading.

        Byte 3 stayed constant across an entire capture while target
        temperature, mode and fan speed all changed around it, consistent
        with a slow-moving sensor reading rather than a control field --
        but this hasn't been confirmed against a known actual room
        temperature.
        """
        if not self._raw_payload:
            return None
        return self._raw_payload[3]

    @property
    def hvac_mode(self) -> str | None:
        """Return the current mode as one of MODE_TO_BYTE's keys, or None
        if the AC is off or no status has been observed yet."""
        if not self._raw_payload or not self.is_on:
            return None
        return self.BYTE_TO_MODE.get(self._raw_payload[9])

    @property
    def fan_speed(self) -> str | None:
        """Return the current fan speed as one of FAN_TO_BYTE's keys, or
        None if the AC is off, no status has been observed yet, or the
        current byte value doesn't match a known speed (e.g. "Auto")."""
        if not self._raw_payload or not self.is_on:
            return None
        return self.BYTE_TO_FAN.get(self._raw_payload[10])

    async def read_status(self) -> None:
        """Trigger a read of the current AC status."""
        req = _GenericControl(self._buspro)
        req.subnet_id, req.device_id = self._device_address
        req.operate_code = OperateCode.ReadACStatus
        req.payload = [self._hvac_number]
        await req.send()

    # How often to retry the startup read while no status has been observed
    # yet. A single one-shot attempt is fragile -- it can race the gateway
    # coming up, drop a packet, or (we suspect, but haven't confirmed) the
    # module may simply not answer ReadACStatus for a channel nobody has
    # ever operated. Retrying costs nothing (it's a tiny broadcast, and it
    # stops the moment real status arrives, from this read or from anyone
    # else operating the AC) and fixes the "entity never becomes available"
    # case whenever the read *would* have worked eventually.
    _READ_RETRY_SECONDS = 30

    def _call_read_current_status(self, run_from_init: bool = False) -> None:
        async def _read():
            if run_from_init:
                await asyncio.sleep(5)
            while self._raw_payload is None:
                try:
                    await self.read_status()
                except Exception:  # noqa: BLE001
                    self._buspro.logger.debug(
                        "AC status read failed for %s HVAC %s",
                        self._device_address,
                        self._hvac_number,
                    )
                await asyncio.sleep(self._READ_RETRY_SECONDS)
            self._buspro.logger.debug(
                "AC %s HVAC %s status observed, stopping read retries",
                self._device_address,
                self._hvac_number,
            )

        asyncio.ensure_future(_read(), loop=self._buspro.loop)

    async def _send_update(self, **changes) -> None:
        """Echo the last observed payload, changing only confirmed fields.

        Refuses to send anything until a real payload has been observed --
        there is no safe default for the unidentified bytes (see class
        docstring), so guessing them risks triggering an unintended stored
        IR code on real hardware.
        """
        if not self._raw_payload:
            self._buspro.logger.warning(
                "Cannot control AC %s HVAC %s yet: no status has been "
                "observed on the bus. Retrying a status read now -- if "
                "this keeps happening, operate this AC once from the HDL "
                "app or a physical panel, which will also seed it.",
                self._device_address,
                self._hvac_number,
            )
            # Nudge a fresh read right away rather than only waiting for
            # the next periodic retry -- if the module does answer reads,
            # this shortens "try the command again in a bit" to seconds.
            try:
                await self.read_status()
            except Exception:  # noqa: BLE001
                pass
            return

        payload = list(self._raw_payload)
        if "power" in changes:
            payload[8] = changes["power"]
        if "target_temperature" in changes:
            temperature = int(changes["target_temperature"])
            payload[5] = temperature
            payload[11] = temperature
        if "hvac_mode" in changes or "fan_speed" in changes:
            mode_byte = (
                self.MODE_TO_BYTE[changes["hvac_mode"]]
                if "hvac_mode" in changes
                else payload[9]
            )
            fan_byte = (
                self.FAN_TO_BYTE[changes["fan_speed"]]
                if "fan_speed" in changes
                else payload[10]
            )
            payload[9] = mode_byte
            payload[10] = fan_byte
            # Only recompute byte 7 when the unit is (or is becoming) on --
            # _MODE_BASE was only confirmed from power-on frames.
            if payload[8] == 1:
                payload[7] = self._MODE_BASE[mode_byte] + fan_byte

        ctrl = _GenericControl(self._buspro)
        ctrl.subnet_id, ctrl.device_id = self._device_address
        ctrl.operate_code = OperateCode.ControlACStatus
        ctrl.payload = payload
        await ctrl.send()

        # Optimistically update local state; the module's own broadcast
        # reply will confirm (or correct) it shortly after.
        self._raw_payload = payload
        self._call_device_updated()

    async def turn_on(self) -> None:
        """Turn the AC on (leaves mode/fan at their last real setting)."""
        await self._send_update(power=1)

    async def turn_off(self) -> None:
        """Turn the AC off."""
        await self._send_update(power=0)

    async def set_target_temperature(self, temperature: int) -> None:
        """Set the target temperature, in whole degrees Celsius."""
        await self._send_update(target_temperature=temperature)

    async def set_hvac_mode(self, mode: str) -> None:
        """Set the HVAC mode -- one of MODE_TO_BYTE's keys ("cool",
        "heat", "fan_only", "auto", "dry"), or "off" to turn the unit off.
        """
        if mode == "off":
            await self._send_update(power=0)
            return
        if mode not in self.MODE_TO_BYTE:
            raise ValueError(f"Unknown AC mode: {mode!r}")
        await self._send_update(power=1, hvac_mode=mode)

    async def set_fan_speed(self, speed: str) -> None:
        """Set the fan speed -- one of FAN_TO_BYTE's keys ("low",
        "medium", "high")."""
        if speed not in self.FAN_TO_BYTE:
            raise ValueError(f"Unknown AC fan speed: {speed!r}")
        await self._send_update(fan_speed=speed)
