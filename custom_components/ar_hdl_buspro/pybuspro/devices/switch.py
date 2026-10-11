"""Switch device wrapper."""
from __future__ import annotations

from ..helpers.enums import OperateCode
from ..helpers.generics import Generics
from .control import _ReadStatusOfChannels, _SingleChannelControl
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
        self._join_module()
        self.register_telegram_received_cb(self._telegram_received_cb)
        # Live status at startup comes from the module-level read queued by
        # _join_module (one paced read per module, retried until answered).

    def _telegram_received_cb(self, telegram) -> None:
        # Anything from this module proves it is alive.
        self._heard_from_module(
            telegram.operate_code == OperateCode.ReadStatusOfChannelsResponse
        )
        if telegram.operate_code == OperateCode.SingleChannelControlResponse:
            if len(telegram.payload) < 3:
                return
            channel = telegram.payload[0]
            brightness = telegram.payload[2]
            if channel == self._channel:
                if not self._accept_reply(brightness):
                    return  # late reply to an earlier click
                self._brightness = brightness
                self._got_initial_status = True
                self._mark_confirmed()
                self._call_device_updated()
        elif telegram.operate_code == OperateCode.ReadStatusOfChannelsResponse:
            payload = telegram.payload
            if payload and self._channel <= payload[0] and self._channel < len(payload):
                if not self._accept_reply(payload[self._channel]):
                    return
                self._brightness = telegram.payload[self._channel]
                self._got_initial_status = True
                self._mark_confirmed()
                self._call_device_updated()
        elif telegram.operate_code == OperateCode.SceneControlResponse:
            self._call_read_current_status_of_channels()

    async def set_on(self) -> None:
        """Turn the switch on."""
        await self._set(100, 0)

    async def set_off(self) -> None:
        """Turn the switch off."""
        await self._set(0, 0)

    async def read_status(self) -> None:
        """Re-read this channel's live status from the module.

        Used right after the gateway link comes back (see ARHDLBaseEntity.
        _handle_gateway_availability in entity.py). Goes through the paced
        per-module queue, so every channel of a module asking at once still
        puts one read on the bus, and it is retried until the module answers.
        """
        self._request_live_status()

    @property
    def supports_brightness(self) -> bool:
        return False

    @staticmethod
    def _same_level(a, b) -> bool:
        """A relay is on or off; 100 and 255 both mean on."""
        return bool(a) == bool(b)

    @property
    def is_on(self) -> bool:
        return self._brightness != 0

    @property
    def device_identifier(self) -> str:
        return f"{self._device_address}-{self._channel}"

    async def _set(self, intensity: int, running_time_seconds: int) -> None:
        self._brightness = intensity
        if self._defer_if_busy(intensity, running_time_seconds):
            return  # shown now, sent when the previous command is answered
        minutes, seconds = Generics.calculate_minutes_seconds(running_time_seconds)
        self._note_command(intensity)
        scc = _SingleChannelControl(self._buspro)
        scc.subnet_id, scc.device_id = self._device_address
        scc.channel_number = self._channel
        scc.channel_level = intensity
        scc.running_time_minutes = minutes
        scc.running_time_seconds = seconds
        await scc.send()
        self._start_ack_watch(scc)
