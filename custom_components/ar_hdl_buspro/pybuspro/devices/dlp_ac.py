"""Air conditioner on a DLP panel's AC page.

A DLP's AC page does NOT speak the Enviro/Granite field-per-frame protocol
(0xE3D8/0xE3DA, see panel_ac.py) - that is why an AC imported from a DLP as
"ac_panel" never answered. HDL Studio drives it with one of these, chosen by
the panel's type code (DeviceCommon ACCommon.ReadCurrentStatus /
SaveCurrentStatus and frmDLP.btnSaveControl_Click in HDL Studio 1.9.65):

Classic DLP (every DLP not listed below)
    0xE0EC read, no payload -> 0xE0ED, 10 bytes:
        [0 power, 1 cool setpoint, 2 mode<<4 | fan, 3 lock, 4 room temp,
         5 heat setpoint, 6 running status, 7 auto setpoint, 8 dry setpoint,
         9 swing]
    0xE0EE write, 8 bytes (the read block without room temp / running):
        [power, cool, mode<<4 | fan, lock, heat, auto, dry, swing]

OLED TILE DLP "high precision" (0x088F, 0x08BE, 0x08C1, 0x08C2, 0x08C4,
0x08C5 - HDL Studio's mintDLPwithAcControlHighPrecision)
    0x19AE read [ac] -> 0x19AF:
        [0 ac, 1 power, 2 cool, 3 heat, 4 auto, 5 dry, 6 mode, 7 fan,
         8 fan enable, 9 -, 10 working mode, 11 working wind, 12 working fan,
         13 room temp, 14 room decimal, 15 cool dec, 16 heat dec,
         17 auto dec, 18 dry dec]
    0x19B0 write, 14 bytes:
        [ac, power, cool, heat, auto, dry, mode, fan, fan enable,
         temperature unit, cool dec, heat dec, auto dec, dry dec]

(0x08DF uses the HVAC protocol 0x1938/0x193A - AirConditioner in climate.py.)

Mode and fan values are HDL's usual AC order (the same combo-box lists in
Studio's DLP AC page): mode 0 cool, 1 heat, 2 fan, 3 auto, 4 dry; fan
0 auto, 1 high, 2 medium, 3 low.

Every write is a whole block, so this class only ever writes after it has
read the panel's real block, and changes just the field asked for.
"""
from __future__ import annotations

import asyncio
import random

from ..helpers.enums import OperateCode
from .control import _GenericControl
from .device import Device

PROTOCOL_AUTO = "auto"
PROTOCOL_CLASSIC = "dlp_classic"
PROTOCOL_PRECISE = "dlp_precise"

MODE_TO_VALUE = {"cool": 0, "heat": 1, "fan_only": 2, "auto": 3, "dry": 4}
VALUE_TO_MODE = {v: k for k, v in MODE_TO_VALUE.items()}
FAN_TO_VALUE = {"auto": 0, "high": 1, "medium": 2, "low": 3}
VALUE_TO_FAN = {v: k for k, v in FAN_TO_VALUE.items()}
SUPPORTED_MODES = ["cool", "heat", "fan_only", "auto", "dry"]

# Which setpoint field belongs to which mode (fan has none).
_SETPOINT_FOR_MODE = {"cool": "cool", "heat": "heat", "auto": "auto", "dry": "dry"}

_MIN_VALID_TEMP = 0
_MAX_VALID_TEMP = 60


def _valid_temp(value) -> bool:
    return isinstance(value, int) and _MIN_VALID_TEMP <= value <= _MAX_VALID_TEMP


def decode_classic(payload) -> dict | None:
    """0xE0ED payload -> state dict, or None if it isn't a usable block."""
    try:
        p = [int(b) & 0xFF for b in payload]
    except (TypeError, ValueError):
        return None
    if len(p) < 9:
        return None
    if all(b == 0xFF for b in p[:9]):
        return None  # no AC page on this panel
    mode, fan = p[2] >> 4, p[2] & 0x0F
    if p[0] not in (0, 1) or mode not in VALUE_TO_MODE or fan not in VALUE_TO_FAN:
        return None
    room = p[4] - 256 if p[4] > 127 else p[4]
    return {
        "power": p[0],
        "cool": p[1],
        "mode": mode,
        "fan": fan,
        "lock": p[3],
        "room": float(room),
        "heat": p[5],
        "running": p[6],
        "auto": p[7],
        "dry": p[8],
        "swing": p[9] if len(p) > 9 else 0,
    }


def encode_classic(state: dict) -> list[int]:
    """State dict -> 0xE0EE payload."""
    return [
        int(state["power"]) & 0xFF,
        int(state["cool"]) & 0xFF,
        ((int(state["mode"]) & 0x0F) << 4) | (int(state["fan"]) & 0x0F),
        int(state.get("lock", 0)) & 0xFF,
        int(state["heat"]) & 0xFF,
        int(state["auto"]) & 0xFF,
        int(state["dry"]) & 0xFF,
        int(state.get("swing", 0)) & 0xFF,
    ]


def decode_precise(payload) -> dict | None:
    """0x19AF payload -> state dict, or None if it isn't a usable block."""
    try:
        p = [int(b) & 0xFF for b in payload]
    except (TypeError, ValueError):
        return None
    if len(p) < 14:
        return None
    if all(b == 0xFF for b in p[1:9]):
        return None
    if p[1] not in (0, 1) or p[6] not in VALUE_TO_MODE or p[7] not in VALUE_TO_FAN:
        return None

    def dec(i):
        return p[i] if len(p) > i and p[i] <= 9 else 0

    room = p[13] - 256 if p[13] > 127 else p[13]
    return {
        "ac": p[0],
        "power": p[1],
        "cool": p[2],
        "heat": p[3],
        "auto": p[4],
        "dry": p[5],
        "mode": p[6],
        "fan": p[7],
        "fan_enable": p[8],
        "room": room + dec(14) / 10,
        "cool_dec": dec(15),
        "heat_dec": dec(16),
        "auto_dec": dec(17),
        "dry_dec": dec(18),
    }


def encode_precise(state: dict) -> list[int]:
    """State dict -> 0x19B0 payload (14 bytes, whole-degree unit = Celsius)."""
    return [
        int(state.get("ac", 0)) & 0xFF,
        int(state["power"]) & 0xFF,
        int(state["cool"]) & 0xFF,
        int(state["heat"]) & 0xFF,
        int(state["auto"]) & 0xFF,
        int(state["dry"]) & 0xFF,
        int(state["mode"]) & 0xFF,
        int(state["fan"]) & 0xFF,
        int(state.get("fan_enable", 0)) & 0xFF,
        0,  # temperature unit: Celsius
        int(state.get("cool_dec", 0)) & 0xFF,
        int(state.get("heat_dec", 0)) & 0xFF,
        int(state.get("auto_dec", 0)) & 0xFF,
        int(state.get("dry_dec", 0)) & 0xFF,
    ]


class DlpAirConditioner(Device):
    """The AC page of one DLP panel."""

    _STARTUP_RETRY_SECONDS = 30
    _REFRESH_SECONDS = 60   # room temperature lives in the status block
    _READBACK_DELAY = 1.0

    def __init__(
        self,
        buspro,
        device_address,
        protocol: str = PROTOCOL_AUTO,
        name: str = "",
        start: bool = True,
    ) -> None:
        super().__init__(buspro, device_address, name)
        if protocol not in (PROTOCOL_AUTO, PROTOCOL_CLASSIC, PROTOCOL_PRECISE):
            protocol = PROTOCOL_AUTO
        self._wanted = protocol
        # Protocol the panel actually answered (None until it does).
        self._protocol: str | None = None
        self._state: dict | None = None
        self._read_attempt = 0
        self._stopped = False
        self.register_telegram_received_cb(self._telegram_received_cb)
        if start:
            self._start_background_reads()

    # ----- identity / state -------------------------------------------------
    @property
    def device_identifier(self) -> str:
        subnet, device = self._device_address
        return f"dlp-ac-{subnet}-{device}"

    @property
    def protocol(self) -> str | None:
        return self._protocol

    @property
    def available(self) -> bool:
        return self._state is not None

    @property
    def supported_modes(self) -> list[str]:
        return list(SUPPORTED_MODES)

    @property
    def is_on(self) -> bool:
        return bool(self._state) and self._state["power"] == 1

    @property
    def hvac_mode(self) -> str | None:
        return VALUE_TO_MODE.get(self._state["mode"]) if self._state else None

    @property
    def fan_speed(self) -> str | None:
        return VALUE_TO_FAN.get(self._state["fan"]) if self._state else None

    def _setpoint(self, slot: str):
        if not self._state:
            return None
        value = self._state.get(slot)
        dec = self._state.get(f"{slot}_dec", 0) or 0
        return value + dec / 10 if dec else value

    @property
    def target_temperature(self):
        slot = _SETPOINT_FOR_MODE.get(self.hvac_mode or "")
        return self._setpoint(slot) if slot else None

    @property
    def cool_target_temperature(self):
        return self._setpoint("cool")

    @property
    def heat_target_temperature(self):
        return self._setpoint("heat")

    @property
    def current_temperature(self):
        return self._state.get("room") if self._state else None

    # ----- decode -----------------------------------------------------------
    def _telegram_received_cb(self, telegram) -> None:
        op = telegram.operate_code
        payload = telegram.payload
        if not isinstance(payload, (list, tuple)):
            return
        if op == OperateCode.ReadDLPPreciseACResponse:
            if self._wanted == PROTOCOL_CLASSIC:
                return
            state = decode_precise(payload)
            proto = PROTOCOL_PRECISE
        elif op == OperateCode.ReadDLPACResponse:
            # A panel that speaks the precise protocol is driven that way,
            # even if it also answers the classic read.
            if self._wanted == PROTOCOL_PRECISE or self._protocol == PROTOCOL_PRECISE:
                return
            state = decode_classic(payload)
            proto = PROTOCOL_CLASSIC
        elif op in (
            OperateCode.ControlDLPACResponse,
            OperateCode.ControlDLPPreciseACResponse,
        ):
            # Reply layout to a write isn't documented: read the block back.
            self._schedule_readback()
            return
        else:
            return
        if state is None:
            return
        self._protocol = proto
        if state != self._state:
            self._state = state
            self._call_device_updated()

    # ----- bus I/O ----------------------------------------------------------
    async def _send(self, operate_code, payload) -> None:
        ctrl = _GenericControl(self._buspro)
        ctrl.subnet_id, ctrl.device_id = self._device_address
        ctrl.operate_code = operate_code
        ctrl.payload = list(payload)
        await ctrl.send()

    async def read_status(self) -> None:
        """Read the AC block (both protocols until the panel picks one)."""
        self._read_attempt += 1
        proto = self._protocol or self._wanted
        if proto in (PROTOCOL_AUTO, PROTOCOL_PRECISE):
            ac = self._state.get("ac", 0) if self._state and "ac" in self._state else (
                # HDL Studio's DLP page asks with [0]; ACCommon uses the AC
                # number. Alternate until the panel answers one of them.
                0 if self._read_attempt % 2 else 1
            )
            await self._send(OperateCode.ReadDLPPreciseAC, [ac])
        if proto in (PROTOCOL_AUTO, PROTOCOL_CLASSIC):
            if proto == PROTOCOL_AUTO:
                await asyncio.sleep(0.15)
            await self._send(OperateCode.ReadDLPAC, [])

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
            await asyncio.sleep(4 + random.uniform(0, 3))
            while not self._stopped:
                try:
                    await self.read_status()
                except Exception:  # noqa: BLE001
                    self._buspro.logger.debug(
                        "DLP AC read failed for %s", self.device_identifier
                    )
                await asyncio.sleep(
                    self._REFRESH_SECONDS if self.available else self._STARTUP_RETRY_SECONDS
                )

        asyncio.ensure_future(_loop(), loop=self._buspro.loop)

    def stop(self) -> None:
        self._stopped = True
        self.unregister_telegram_received_cb(self._telegram_received_cb)

    # ----- commands ---------------------------------------------------------
    async def _write(self, **changes) -> None:
        if not self._state or not self._protocol:
            self._buspro.logger.warning(
                "DLP AC %s.%s has not reported its AC page yet; command not sent",
                *self._device_address,
            )
            try:
                await self.read_status()
            except Exception:  # noqa: BLE001
                pass
            return
        state = dict(self._state)
        state.update(changes)
        if self._protocol == PROTOCOL_PRECISE:
            await self._send(OperateCode.ControlDLPPreciseAC, encode_precise(state))
        else:
            await self._send(OperateCode.ControlDLPAC, encode_classic(state))
        # Show it now; the read-back a moment later corrects it if the panel
        # didn't take it.
        self._state = state
        self._call_device_updated()
        self._schedule_readback()

    async def turn_on(self) -> None:
        await self._write(power=1)

    async def turn_off(self) -> None:
        await self._write(power=0)

    async def set_hvac_mode(self, mode: str) -> None:
        if mode == "off":
            await self.turn_off()
            return
        if mode not in MODE_TO_VALUE:
            raise ValueError(f"Unsupported DLP AC mode: {mode!r}")
        await self._write(power=1, mode=MODE_TO_VALUE[mode])

    async def set_fan_speed(self, speed: str) -> None:
        if speed not in FAN_TO_VALUE:
            raise ValueError(f"Unsupported DLP AC fan speed: {speed!r}")
        await self._write(fan=FAN_TO_VALUE[speed])

    async def set_target_temperature(self, temperature) -> None:
        slot = _SETPOINT_FOR_MODE.get(self.hvac_mode or "", "cool")
        whole = int(temperature)
        changes = {slot: whole}
        if self._protocol == PROTOCOL_PRECISE:
            changes[f"{slot}_dec"] = int(round((float(temperature) - whole) * 10)) % 10
        await self._write(**changes)


class AutoPanelAc:
    """A panel AC slot whose protocol is found by asking the panel.

    Used for "ac_panel" entries. Before 5.1 every panel AC was imported that
    way, including DLP ACs that never answer the Enviro protocol. This runs
    the Enviro reader exactly as before and, alongside it, the DLP and HVAC
    readers; whichever the panel answers is used and the others are stopped.
    An Enviro/Granite panel answers its own protocol first and is unchanged.
    """

    # After the first non-Enviro answer, wait this long for a better one
    # (a high-precision DLP may answer both DLP reads).
    _SETTLE_SECONDS = 5.0
    # With an Enviro reader in the running, never settle on another protocol
    # before it has had its startup read and one retry (panel_ac.py retries
    # every 30 s), so one lost frame can't send a working Enviro panel down
    # the wrong protocol.
    _ENVIRO_GRACE_SECONDS = 45.0

    def __init__(self, buspro, enviro, dlp, hvac) -> None:
        self._buspro = buspro
        self._started = buspro.loop.time()
        self._candidates = {"enviro": enviro, "dlp": dlp, "hvac": hvac}
        self._active = None
        self._active_name: str | None = None
        self._settling = False
        self.device_updated_cbs: list = []
        for name, dev in self._candidates.items():
            if dev is not None:
                dev.register_device_updated_cb(self._make_cb(name))

    def _make_cb(self, name):
        async def _cb(_device):
            self._candidate_updated(name)
            if self._active_name in (None, name):
                await self._device_updated()

        return _cb

    def _candidate_updated(self, name: str) -> None:
        if self._active is not None:
            return
        dev = self._candidates.get(name)
        if dev is None or not dev.available:
            return
        if name == "enviro":
            self._choose("enviro")
            return
        if not self._settling:
            self._settling = True

            async def _settle():
                delay = self._SETTLE_SECONDS
                if self._candidates.get("enviro") is not None:
                    grace_left = self._ENVIRO_GRACE_SECONDS - (
                        self._buspro.loop.time() - self._started
                    )
                    delay = max(delay, grace_left)
                await asyncio.sleep(delay)
                if self._active is not None:
                    return
                enviro = self._candidates.get("enviro")
                dlp = self._candidates.get("dlp")
                hvac = self._candidates.get("hvac")
                if enviro is not None and enviro.available:
                    self._choose("enviro")
                elif dlp is not None and dlp.available and dlp.protocol == PROTOCOL_PRECISE:
                    self._choose("dlp")
                elif hvac is not None and hvac.available:
                    self._choose("hvac")
                elif dlp is not None and dlp.available:
                    self._choose("dlp")
                await self._device_updated()

            asyncio.ensure_future(_settle(), loop=self._buspro.loop)

    def _choose(self, name: str) -> None:
        self._active_name = name
        self._active = self._candidates[name]
        for other, dev in self._candidates.items():
            if other != name and dev is not None and hasattr(dev, "stop"):
                dev.stop()
        self._buspro.logger.info(
            "Panel AC %s answers the %s protocol",
            getattr(self._active, "_device_address", "?"),
            name if name != "dlp" else self._active.protocol,
        )

    # ----- callbacks ----------------------------------------------------------
    def register_device_updated_cb(self, cb) -> None:
        self.device_updated_cbs.append(cb)

    def unregister_device_updated_cb(self, cb) -> None:
        if cb in self.device_updated_cbs:
            self.device_updated_cbs.remove(cb)

    async def _device_updated(self) -> None:
        for cb in list(self.device_updated_cbs):
            try:
                await cb(self)
            except Exception:  # noqa: BLE001
                self._buspro.logger.exception("Device-updated callback failed")

    # ----- delegation -----------------------------------------------------------
    @property
    def detected(self) -> str | None:
        if self._active_name == "dlp":
            return self._active.protocol
        return self._active_name

    @property
    def _view(self):
        """The backend whose state is shown: the chosen one, else Enviro."""
        if self._active is not None:
            return self._active
        for name in ("enviro", "dlp", "hvac"):
            dev = self._candidates.get(name)
            if dev is not None and dev.available:
                return dev
        return self._candidates.get("enviro") or next(
            d for d in self._candidates.values() if d is not None
        )

    @property
    def available(self) -> bool:
        return self._view.available

    @property
    def supported_modes(self) -> list[str]:
        view = self._view
        if self._active_name == "enviro" or (
            self._active is None and view is self._candidates.get("enviro")
        ):
            return ["cool", "heat"]
        return list(SUPPORTED_MODES)

    @property
    def is_on(self) -> bool:
        return self._view.is_on

    @property
    def hvac_mode(self):
        return self._view.hvac_mode

    @property
    def fan_speed(self):
        return self._view.fan_speed

    @property
    def target_temperature(self):
        return self._view.target_temperature

    @property
    def current_temperature(self):
        return self._view.current_temperature

    @property
    def cool_target_temperature(self):
        return getattr(self._view, "cool_target_temperature", None)

    @property
    def heat_target_temperature(self):
        return getattr(self._view, "heat_target_temperature", None)

    async def read_status(self) -> None:
        if self._active is not None:
            await self._active.read_status()
            return
        for dev in self._candidates.values():
            if dev is not None:
                await dev.read_status()

    def stop(self) -> None:
        for dev in self._candidates.values():
            if dev is not None and hasattr(dev, "stop"):
                dev.stop()

    def _target(self):
        if self._active is not None:
            return self._active
        view = self._view
        if not view.available:
            self._buspro.logger.warning(
                "Panel AC has not answered any AC protocol yet; command not sent"
            )
            return None
        return view

    async def turn_on(self) -> None:
        dev = self._target()
        if dev is not None:
            await dev.turn_on()

    async def turn_off(self) -> None:
        dev = self._target()
        if dev is not None:
            await dev.turn_off()

    async def set_hvac_mode(self, mode: str) -> None:
        dev = self._target()
        if dev is not None:
            await dev.set_hvac_mode(mode)

    async def set_fan_speed(self, speed: str) -> None:
        dev = self._target()
        if dev is not None:
            await dev.set_fan_speed(speed)

    async def set_target_temperature(self, temperature) -> None:
        dev = self._target()
        if dev is not None:
            await dev.set_target_temperature(int(temperature))
