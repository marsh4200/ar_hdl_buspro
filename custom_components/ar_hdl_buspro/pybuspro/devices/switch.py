"""Switch device wrapper."""
from __future__ import annotations

from ..helpers.enums import OperateCode
from ..helpers.generics import Generics
from .control import _SingleChannelControl
from .device import Device


class Switch(Device):
    """An HDL Buspro switch channel (non-dimmable on/off)."""

    def __init__(
        self,
        buspro,
        device_address,
        channel_number: int,
        name: str = "",
        delay_read_current_state_seconds: int = 0,
    ) -> None:
        """Initialize the switch."""
        super().__init__(buspro, device_address, name)
        self._buspro = buspro
        self._device_address = device_address
        self._channel = channel_number
        self._brightness = 0
        self.register_telegram_received_cb(self._telegram_received_cb)
        self._call_read_current_status_of_channels(run_from_init=True)

    def _telegram_received_cb(self, telegram) -> None:
        if telegram.operate_code == OperateCode.SingleChannelControlResponse:
            channel = telegram.payload[0]
            brightness = telegram.payload[2]
            if channel == self._channel:
                self._brightness = brightness
                self._call_device_updated()
        elif telegram.operate_code == OperateCode.ReadStatusOfChannelsResponse:
            if self._channel <= telegram.payload[0]:
                self._brightness = telegram.payload[self._channel]
                self._call_device_updated()
        elif telegram.operate_code == OperateCode.SceneControlResponse:
            self._call_read_current_status_of_channels()

    async def set_on(self) -> None:
        """Turn the switch on."""
        await self._set(100, 0)

    async def set_off(self) -> None:
        """Turn the switch off."""
        await self._set(0, 0)

    async def read_status(self):  # pragma: no cover
        raise NotImplementedError

    @property
    def supports_brightness(self) -> bool:
        return False

    @property
    def is_on(self) -> bool:
        return self._brightness != 0

    @property
    def device_identifier(self) -> str:
        return f"{self._device_address}-{self._channel}"

    async def _set(self, intensity: int, running_time_seconds: int) -> None:
        self._brightness = intensity
        minutes, seconds = Generics.calculate_minutes_seconds(running_time_seconds)
        scc = _SingleChannelControl(self._buspro)
        scc.subnet_id, scc.device_id = self._device_address
        scc.channel_number = self._channel
        scc.channel_level = intensity
        scc.running_time_minutes = minutes
        scc.running_time_seconds = seconds
        await scc.send()
