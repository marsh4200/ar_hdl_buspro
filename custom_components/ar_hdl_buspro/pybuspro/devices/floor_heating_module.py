"""HDL floor heating MODULE (HDL-MFH06.432 / MFH06.332 / AFH06.432).

A DIN-rail module with 6 independent heating channels behind one bus
address. It does NOT speak the DLP panel floor-heating protocol
(0x1944/0x1946, see climate.py) - it never answers that read, which is why a
module added as an ordinary "floor heating" climate entity sat dead.

Protocol, from HDL Studio 1.9.65 (frmFH.btnRefreshStatus_Click,
btnSaveTemp_Click, getControValue, ShowFloorHeatingInfo):

    0x1C5E read  [channel]                       -> 0x1C5F
    0x1C5C write [channel, b0..b6, 0, 0]         -> 0x1C5D
    Both replies: [channel, b0..b12] (Studio copies 13 bytes from payload[1]).

Status bytes (b0..b12):
    b0   low nibble  = channel on (1) / off (0)
         high nibble = work type: 0 heat, 1 cool, 2 / 3 = "P1" / "P2"
                       (G2 modules), 4 = auto (MFH06.332 0x00D1 only)
    b1   temperature unit: 0 = Celsius, 1 = Fahrenheit
    b2   mode 1..5: 1 normal, 2 day, 3 night, 4 away, 5 timer
    b3   normal setpoint
    b4   day setpoint
    b5   night setpoint
    b6   away setpoint
    b7   status flag (0/1, shown as text in Studio)
    b8   current temperature, sign + magnitude: bit 7 = negative,
         0x80 alone = 0
    b9   status flag (0/1, shown as text in Studio)
    b10  output 0-100 % (valve / PWM demand; >100 shown as 0)
    b11, b12  not shown by Studio's status page

The write is always a whole block (b0..b6), so this class only writes after
it has read the channel's real block, and changes just the field asked for.
"""
from __future__ import annotations

import asyncio
import random

from ..helpers.enums import OperateCode
from .control import _GenericControl
from .device import Device

WORK_HEAT = 0
WORK_COOL = 1
WORK_TYPES = {0: "heat", 1: "cool", 2: "p1", 3: "p2", 4: "auto"}

MODE_NORMAL, MODE_DAY, MODE_NIGHT, MODE_AWAY, MODE_TIMER = 1, 2, 3, 4, 5
# Mode -> index of its setpoint in the status block. Timer runs the
# day/night schedule; Studio edits the same four fields, so the normal
# setpoint stands in for it here (same as the DLP climate entity).
_SETPOINT_INDEX = {MODE_NORMAL: 3, MODE_DAY: 4, MODE_NIGHT: 5, MODE_AWAY: 6, MODE_TIMER: 3}

STATUS_LEN = 13


def decode_status(payload, channel: int) -> list[int] | None:
    """0x1C5F / 0x1C5D payload -> the 13 status bytes, or None."""
    try:
        p = [int(b) & 0xFF for b in payload]
    except (TypeError, ValueError):
        return None
    if len(p) < 1 + 7 or p[0] != channel:
        return None
    block = p[1:1 + STATUS_LEN]
    if all(b == 0xFF for b in block[:7]):
        return None  # channel not fitted / not configured
    return block + [0] * (STATUS_LEN - len(block))


def signed_temp(raw: int) -> int:
    """b8 sign + magnitude -> degrees."""
    raw &= 0xFF
    if raw == 0x80:
        return 0
    return -(raw & 0x7F) if raw & 0x80 else raw


def encode_control(channel: int, block: list[int]) -> list[int]:
    """Status block -> 0x1C5C payload (10 bytes, as HDL Studio sends it)."""
    return [channel & 0xFF] + [int(b) & 0xFF for b in block[:7]] + [0, 0]


class FloorHeatingModule(Device):
    """One channel of an HDL floor heating module."""

    _STARTUP_RETRY_SECONDS = 30
    _REFRESH_SECONDS = 60   # current temperature / output live in the block
    _READBACK_DELAY = 1.0
    _WAIT_FOR_STATUS = 3.0

    def __init__(
        self, buspro, device_address, channel: int = 1, name: str = "", start: bool = True
    ) -> None:
        super().__init__(buspro, device_address, name)
        self._channel = max(1, int(channel or 1))
        self._block: list[int] | None = None
        self._got_status: asyncio.Event | None = None
        self._stopped = False
        self.register_telegram_received_cb(self._telegram_received_cb)
        if start:
            self._start_background_reads()

    # ----- identity / state -------------------------------------------------
    @property
    def device_identifier(self) -> str:
        subnet, device = self._device_address
        return f"fh-module-{subnet}-{device}-{self._channel}"

    @property
    def channel(self) -> int:
        return self._channel

    @property
    def available(self) -> bool:
        return self._block is not None

    @property
    def raw_status(self) -> list[int] | None:
        return list(self._block) if self._block else None

    @property
    def is_on(self) -> bool:
        return bool(self._block) and (self._block[0] & 0x0F) == 1

    @property
    def work_type(self) -> str | None:
        return WORK_TYPES.get(self._block[0] >> 4) if self._block else None

    @property
    def fahrenheit(self) -> bool:
        return bool(self._block) and self._block[1] == 1

    @property
    def mode(self) -> int | None:
        if not self._block:
            return None
        return self._block[2] if 1 <= self._block[2] <= 5 else MODE_NORMAL

    def setpoint(self, mode: int) -> int | None:
        if not self._block:
            return None
        return self._block[_SETPOINT_INDEX.get(mode, 3)]

    @property
    def target_temperature(self) -> int | None:
        return self.setpoint(self.mode or MODE_NORMAL)

    @property
    def current_temperature(self) -> int | None:
        return signed_temp(self._block[8]) if self._block else None

    @property
    def output_percent(self) -> int | None:
        if not self._block:
            return None
        value = self._block[10]
        return value if value <= 100 else 0

    # ----- decode -----------------------------------------------------------
    def _telegram_received_cb(self, telegram) -> None:
        op = telegram.operate_code
        if op not in (
            OperateCode.ReadFloorHeatingModuleResponse,
            OperateCode.ControlFloorHeatingModuleResponse,
        ):
            return
        block = decode_status(telegram.payload, self._channel)
        if block is None:
            return
        if self._got_status is not None:
            self._got_status.set()
        if block != self._block:
            self._block = block
            self._call_device_updated()

    # ----- bus I/O ----------------------------------------------------------
    async def _send(self, operate_code, payload) -> None:
        ctrl = _GenericControl(self._buspro)
        ctrl.subnet_id, ctrl.device_id = self._device_address
        ctrl.operate_code = operate_code
        ctrl.payload = list(payload)
        await ctrl.send()

    async def read_status(self) -> None:
        await self._send(OperateCode.ReadFloorHeatingModule, [self._channel])

    async def _ensure_status(self) -> bool:
        """Make sure the real block is known before writing over it."""
        if self._block is not None:
            return True
        self._got_status = asyncio.Event()
        try:
            await self.read_status()
            await asyncio.wait_for(self._got_status.wait(), self._WAIT_FOR_STATUS)
        except Exception:  # noqa: BLE001 - timeout or send failure
            pass
        finally:
            self._got_status = None
        return self._block is not None

    async def control(
        self,
        on: bool | None = None,
        work_type: int | None = None,
        mode: int | None = None,
        setpoint: int | None = None,
    ) -> bool:
        """Change some fields; the rest are written back unchanged.

        `setpoint` goes to the active mode's setpoint (after any mode change
        in the same call). Returns False if the channel never answered.
        """
        if not await self._ensure_status():
            self._buspro.logger.warning(
                "Floor heating %s: no status from the module, not writing",
                self.device_identifier,
            )
            return False
        block = list(self._block)
        on_bit = block[0] & 0x0F
        work = block[0] >> 4
        if on is not None:
            on_bit = 1 if on else 0
        if work_type is not None:
            work = int(work_type) & 0x0F
        block[0] = (work << 4) | on_bit
        if not 1 <= block[2] <= 5:
            block[2] = MODE_NORMAL  # what Studio writes for an unset mode
        if mode is not None and 1 <= int(mode) <= 5:
            block[2] = int(mode)
        if setpoint is not None:
            block[_SETPOINT_INDEX.get(block[2], 3)] = int(round(setpoint)) & 0xFF
        await self._send(
            OperateCode.ControlFloorHeatingModule, encode_control(self._channel, block)
        )
        self._schedule_readback()
        return True

    def _schedule_readback(self) -> None:
        async def _readback():
            await asyncio.sleep(self._READBACK_DELAY)
            try:
                await self.read_status()
            except Exception:  # noqa: BLE001
                pass

        asyncio.ensure_future(_readback(), loop=self._buspro.loop)

    def _start_background_reads(self) -> None:
        async def _loop():
            # Stagger the six channels of one module so they don't all ask
            # at the same instant.
            await asyncio.sleep(4 + 0.3 * self._channel + random.uniform(0, 3))
            while not self._stopped:
                try:
                    await self.read_status()
                except Exception:  # noqa: BLE001
                    self._buspro.logger.debug(
                        "Floor heating read failed for %s", self.device_identifier
                    )
                await asyncio.sleep(
                    self._REFRESH_SECONDS if self.available else self._STARTUP_RETRY_SECONDS
                )

        asyncio.ensure_future(_loop(), loop=self._buspro.loop)

    def stop(self) -> None:
        self._stopped = True
