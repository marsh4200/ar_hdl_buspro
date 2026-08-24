"""Universal switch device wrapper."""
from __future__ import annotations

import asyncio

from ..helpers.enums import OnOff, OperateCode, SwitchStatusOnOff
from .control import _ReadStatusOfUniversalSwitch, _UniversalSwitch
from .device import Device


class UniversalSwitch(Device):
    """An HDL Buspro universal switch."""

    def __init__(
        self,
        buspro,
        device_address,
        switch_number: int,
        name: str = "",
        delay_read_current_state_seconds: int = 0,
    ) -> None:
        """Initialize the universal switch."""
        super().__init__(buspro, device_address, name)
        self._buspro = buspro
        self._device_address = device_address
        self._switch_number = switch_number
        self._switch_status = SwitchStatusOnOff.OFF
        self.register_telegram_received_cb(self._telegram_received_cb)
        self._call_read_current_status_of_universal_switch(run_from_init=True)

    def _telegram_received_cb(self, telegram) -> None:
        if telegram.operate_code == OperateCode.UniversalSwitchControlResponse:
            switch_number = telegram.payload[0]
            status = telegram.payload[1]
            if switch_number == self._switch_number:
                self._switch_status = status
                self._call_device_updated()
        elif telegram.operate_code == OperateCode.ReadStatusOfUniversalSwitchResponse:
            if self._switch_number <= telegram.payload[0]:
                self._switch_status = telegram.payload[1]
                self._call_device_updated()

    async def set_on(self) -> None:
        """Turn the universal switch on."""
        await self._set(OnOff.ON)

    async def set_off(self) -> None:
        """Turn the universal switch off."""
        await self._set(OnOff.OFF)

    async def read_status(self):  # pragma: no cover
        raise NotImplementedError

    @property
    def is_on(self) -> bool:
        """Return True if on. Status may be a raw int (from a telegram) or an
        OnOff/SwitchStatusOnOff enum (after a local set), so normalize."""
        status = self._switch_status
        if hasattr(status, "value"):
            status = status.value
        return bool(status)

    @property
    def device_identifier(self) -> str:
        return f"{self._device_address}-{self._switch_number}"

    async def _set(self, switch_status) -> None:
        self._switch_status = switch_status
        ctrl = _UniversalSwitch(self._buspro)
        ctrl.subnet_id, ctrl.device_id = self._device_address
        ctrl.switch_number = self._switch_number
        ctrl.switch_status = self._switch_status
        await ctrl.send()

    def _call_read_current_status_of_universal_switch(self, run_from_init: bool = False) -> None:
        async def _read():
            if run_from_init:
                await asyncio.sleep(1)
            req = _ReadStatusOfUniversalSwitch(self._buspro)
            req.subnet_id, req.device_id = self._device_address
            req.switch_number = self._switch_number
            try:
                await req.send()
            except Exception:  # noqa: BLE001
                self._buspro.logger.debug(
                    "Initial universal switch read failed for %s",
                    self._device_address,
                )

        asyncio.ensure_future(_read(), loop=self._buspro.loop)
