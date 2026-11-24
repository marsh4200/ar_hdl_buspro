"""Air conditioner controlled through an HDL touch panel's AC page.

HDL Enviro / Granite style panels (HDL-MPTLC43.46-A, HDL-MPTL4C.48, ...) own
one or more "AC" slots. Each slot's settings are exposed on the bus one field
at a time, addressed to the PANEL (not to the IR emitter that ultimately
drives the physical unit):

    0xE3D8  ControlPanelAC          write   [field, value, ac_channel]
    0xE3D9  ControlPanelACResponse  confirm [field, value, ac_channel]
    0xE3DA  ReadPanelAC             read    [field, ac_channel, ac_channel]
    0xE3DB  ReadPanelACResponse     reply   [field, value, ac_channel]

The panel also emits 0xE3D9 on its own when someone changes a setting on the
panel's screen, so panel-side changes arrive without polling.

Field map (captured on an Enviro panel, two AC slots, power write physically
verified from Home Assistant; mode/target/fan response shapes captured from
on-panel changes):

    3  power            0 = off, 1 = on
    4  cooling target   whole degC
    5  fan              0 = auto, 1 = low, 2 = medium, 3 = high
    6  mode             0 = cool, 1 = heat
    7  heating target   whole degC

Not mapped (never guessed, never written): swing, min/max setpoint limits,
fields 8 and 19 (stable in captures, meaning unknown), compressor state.

Room temperature comes from the panel's own sensor over the channel-addressed
ReadTemperature pair (0xE3E7 [channel] -> 0xE3E8), which this integration
already decodes for the Granite Display.

State is never updated optimistically: a write is followed by a read-back of
the same field, and only the panel's 0xE3D9 / 0xE3DB replies change state.
"""
from __future__ import annotations

import asyncio
import random
import struct

from ..helpers.enums import OperateCode
from .control import _GenericControl
from .device import Device

FIELD_POWER = 3
FIELD_COOL_TARGET = 4
FIELD_FAN = 5
FIELD_MODE = 6
FIELD_HEAT_TARGET = 7
STATUS_FIELDS = (
    FIELD_POWER,
    FIELD_COOL_TARGET,
    FIELD_FAN,
    FIELD_MODE,
    FIELD_HEAT_TARGET,
)

MODE_TO_VALUE = {"cool": 0, "heat": 1}
VALUE_TO_MODE = {v: k for k, v in MODE_TO_VALUE.items()}
FAN_TO_VALUE = {"auto": 0, "low": 1, "medium": 2, "high": 3}
VALUE_TO_FAN = {v: k for k, v in FAN_TO_VALUE.items()}

# Sanity window for a setpoint byte. Anything outside is treated as noise
# rather than a real setting (the panel's own limits are not yet known).
_MIN_VALID_TEMP = 5
_MAX_VALID_TEMP = 40

_STATE_OPS = (
    OperateCode.ControlPanelACResponse,
    OperateCode.ReadPanelACResponse,
)


class PanelAirConditioner(Device):
    """One AC slot on an HDL touch panel."""

    # Retry the full status read until the panel answers at least once.
    _STARTUP_RETRY_SECONDS = 30
    # Slow refresh once running, as a backstop for a missed 0xE3D9 push.
    # Five single-field reads every few minutes is trivial bus load.
    _REFRESH_SECONDS = 300
    # Room temperature poll (the panel does not push 0xE3E8 by itself).
    _TEMPERATURE_SECONDS = 60
    # Gap between the individual field reads so they don't hit the bus as
    # one burst.
    _FIELD_READ_GAP = 0.15
    # Delay before reading a field back after writing it.
    _READBACK_DELAY = 1.0

    def __init__(
        self,
        buspro,
        device_address,
        ac_channel: int,
        temperature_channel: int = 1,
        name: str = "",
    ) -> None:
        """Initialize the panel AC slot.

        temperature_channel = 0 disables the room-temperature read.
        """
        super().__init__(buspro, device_address, name)
        self._ac_channel = int(ac_channel)
        self._temperature_channel = int(temperature_channel or 0)

        self._power: int | None = None
        self._cool_target: int | None = None
        self._heat_target: int | None = None
        self._fan: int | None = None
        self._mode: int | None = None
        self._current_temperature: float | None = None

        self._stopped = False
        self.register_telegram_received_cb(self._telegram_received_cb)
        self._start_background_reads()

    # ----- identity ---------------------------------------------------------
    @property
    def device_identifier(self) -> str:
        subnet, device = self._device_address
        return f"panel-ac-{subnet}-{device}-{self._ac_channel}"

    # ----- state ------------------------------------------------------------
    @property
    def available(self) -> bool:
        """True once the panel has reported this slot's power state."""
        return self._power is not None

    @property
    def is_on(self) -> bool:
        return self._power == 1

    @property
    def hvac_mode(self) -> str | None:
        """Stored mode ("cool"/"heat"), whether or not the unit is on."""
        return VALUE_TO_MODE.get(self._mode)

    @property
    def fan_speed(self) -> str | None:
        return VALUE_TO_FAN.get(self._fan)

    @property
    def cool_target_temperature(self) -> int | None:
        return self._cool_target

    @property
    def heat_target_temperature(self) -> int | None:
        return self._heat_target

    @property
    def target_temperature(self) -> int | None:
        """Setpoint for the stored mode."""
        if self._mode == MODE_TO_VALUE["heat"]:
            return self._heat_target
        if self._mode == MODE_TO_VALUE["cool"]:
            return self._cool_target
        return None

    @property
    def current_temperature(self) -> float | None:
        return self._current_temperature

    # ----- telegram decode --------------------------------------------------
    def _telegram_received_cb(self, telegram) -> None:
        payload = telegram.payload
        if not isinstance(payload, (list, tuple)):
            return
        op = telegram.operate_code
        if op in _STATE_OPS:
            self._decode_field(payload)
        elif (
            op == OperateCode.ReadTemperatureResponse
            and self._temperature_channel
        ):
            self._decode_temperature(payload)

    def _decode_field(self, payload) -> None:
        if len(payload) < 3:
            return
        field, value, channel = payload[0], payload[1], payload[2]
        if channel != self._ac_channel:
            return

        attr = None
        if field == FIELD_POWER and value in (0, 1):
            attr = "_power"
        elif field == FIELD_MODE and value in VALUE_TO_MODE:
            attr = "_mode"
        elif field == FIELD_FAN and value in VALUE_TO_FAN:
            attr = "_fan"
        elif field == FIELD_COOL_TARGET and self._valid_temp(value):
            attr = "_cool_target"
        elif field == FIELD_HEAT_TARGET and self._valid_temp(value):
            attr = "_heat_target"
        if attr is None:
            # Unknown field, other channel, or out-of-range value: ignore and
            # keep the last confirmed state.
            return
        if getattr(self, attr) != value:
            setattr(self, attr, value)
            self._call_device_updated()

    def _decode_temperature(self, payload) -> None:
        # [channel, signed_whole_degrees, <optional float32 LE degC>]
        if len(payload) < 2 or payload[0] != self._temperature_channel:
            return
        whole = payload[1]
        if whole > 127:
            whole -= 256
        value: float = whole
        if len(payload) >= 6:
            try:
                value = round(struct.unpack("<f", bytes(payload[2:6]))[0], 1)
            except (struct.error, ValueError, TypeError):
                value = whole
        if self._current_temperature != value:
            self._current_temperature = value
            self._call_device_updated()

    @staticmethod
    def _valid_temp(value) -> bool:
        return isinstance(value, int) and _MIN_VALID_TEMP <= value <= _MAX_VALID_TEMP

    # ----- bus I/O ----------------------------------------------------------
    async def _send(self, operate_code, payload) -> None:
        ctrl = _GenericControl(self._buspro)
        ctrl.subnet_id, ctrl.device_id = self._device_address
        ctrl.operate_code = operate_code
        ctrl.payload = list(payload)
        await ctrl.send()

    async def _read_field(self, field: int) -> None:
        await self._send(
            OperateCode.ReadPanelAC, [field, self._ac_channel, self._ac_channel]
        )

    async def _write_field(self, field: int, value: int) -> None:
        await self._send(OperateCode.ControlPanelAC, [field, value, self._ac_channel])

    async def read_status(self) -> None:
        """Read every mapped field for this slot (and room temperature)."""
        for field in STATUS_FIELDS:
            await self._read_field(field)
            await asyncio.sleep(self._FIELD_READ_GAP)
        await self.read_temperature()

    async def read_temperature(self) -> None:
        if not self._temperature_channel:
            return
        await self._send(OperateCode.ReadTemperature, [self._temperature_channel])

    def _schedule_readback(self, *fields: int) -> None:
        async def _readback():
            await asyncio.sleep(self._READBACK_DELAY)
            for field in fields:
                try:
                    await self._read_field(field)
                except Exception:  # noqa: BLE001
                    pass
                await asyncio.sleep(self._FIELD_READ_GAP)

        asyncio.ensure_future(_readback(), loop=self._buspro.loop)

    def _start_background_reads(self) -> None:
        async def _status_loop():
            await asyncio.sleep(4 + random.uniform(0, 3))
            while not self._stopped:
                try:
                    await self.read_status()
                except Exception:  # noqa: BLE001
                    self._buspro.logger.debug(
                        "Panel AC read failed for %s", self.device_identifier
                    )
                delay = (
                    self._REFRESH_SECONDS
                    if self.available
                    else self._STARTUP_RETRY_SECONDS
                )
                await asyncio.sleep(delay)

        async def _temperature_loop():
            if not self._temperature_channel:
                return
            await asyncio.sleep(10 + random.uniform(0, 5))
            while not self._stopped:
                try:
                    await self.read_temperature()
                except Exception:  # noqa: BLE001
                    pass
                await asyncio.sleep(self._TEMPERATURE_SECONDS)

        asyncio.ensure_future(_status_loop(), loop=self._buspro.loop)
        asyncio.ensure_future(_temperature_loop(), loop=self._buspro.loop)

    def stop(self) -> None:
        """Stop background polling and detach from the bus."""
        self._stopped = True
        self.unregister_telegram_received_cb(self._telegram_received_cb)

    # ----- commands ---------------------------------------------------------
    async def turn_on(self) -> None:
        await self._write_field(FIELD_POWER, 1)
        self._schedule_readback(FIELD_POWER)

    async def turn_off(self) -> None:
        await self._write_field(FIELD_POWER, 0)
        self._schedule_readback(FIELD_POWER)

    async def set_hvac_mode(self, mode: str) -> None:
        """Set "cool"/"heat" (turning the unit on), or "off"."""
        if mode == "off":
            await self.turn_off()
            return
        if mode not in MODE_TO_VALUE:
            raise ValueError(f"Unsupported panel AC mode: {mode!r}")
        # Power on first: mode writes are only confirmed while the unit is on.
        if not self.is_on:
            await self._write_field(FIELD_POWER, 1)
            await asyncio.sleep(0.3)
        await self._write_field(FIELD_MODE, MODE_TO_VALUE[mode])
        self._schedule_readback(FIELD_POWER, FIELD_MODE)

    async def set_fan_speed(self, speed: str) -> None:
        if speed not in FAN_TO_VALUE:
            raise ValueError(f"Unsupported panel AC fan speed: {speed!r}")
        await self._write_field(FIELD_FAN, FAN_TO_VALUE[speed])
        self._schedule_readback(FIELD_FAN)

    async def set_target_temperature(self, temperature: int) -> None:
        """Write the setpoint for the currently stored mode."""
        temperature = int(temperature)
        field = (
            FIELD_HEAT_TARGET
            if self._mode == MODE_TO_VALUE["heat"]
            else FIELD_COOL_TARGET
        )
        await self._write_field(field, temperature)
        self._schedule_readback(field)
