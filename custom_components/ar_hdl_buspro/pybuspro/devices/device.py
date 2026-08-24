"""Base device class for the pybuspro library."""
from __future__ import annotations

import asyncio

from .control import _ReadStatusOfChannels


class Device:
    """Base class for HDL Buspro devices."""

    def __init__(self, buspro, device_address, name: str = "") -> None:
        """Initialize a device wrapper.

        device_address is a (subnet_id, device_id) tuple.
        """
        self._device_address = device_address
        self._buspro = buspro
        self._name = name
        self.device_updated_cbs: list = []

    @property
    def name(self) -> str:
        """Return the device name."""
        return self._name

    def register_telegram_received_cb(self, telegram_received_cb, postfix=None) -> None:
        """Register a per-device telegram callback."""
        self._buspro.register_telegram_received_device_cb(
            telegram_received_cb, self._device_address, postfix
        )

    def unregister_telegram_received_cb(self, telegram_received_cb, postfix=None) -> None:
        """Unregister a per-device telegram callback."""
        self._buspro.unregister_telegram_received_device_cb(
            telegram_received_cb, self._device_address, postfix
        )

    def register_device_updated_cb(self, device_updated_cb) -> None:
        """Register a callback fired when the device state changes."""
        self.device_updated_cbs.append(device_updated_cb)

    def unregister_device_updated_cb(self, device_updated_cb) -> None:
        """Unregister a device-updated callback."""
        if device_updated_cb in self.device_updated_cbs:
            self.device_updated_cbs.remove(device_updated_cb)

    async def _device_updated(self) -> None:
        for cb in list(self.device_updated_cbs):
            try:
                await cb(self)
            except Exception:  # noqa: BLE001
                self._buspro.logger.exception("Device-updated callback failed")

    async def _send_telegram(self, telegram) -> None:
        if self._buspro.network_interface is None:
            return
        await self._buspro.network_interface.send_telegram(telegram)

    def _call_device_updated(self) -> None:
        """Schedule device_updated callbacks on the running loop."""
        asyncio.ensure_future(self._device_updated(), loop=self._buspro.loop)

    def _call_read_current_status_of_channels(self, run_from_init: bool = False) -> None:
        """Schedule a read of the device's channel status."""

        async def _read():
            if run_from_init:
                await asyncio.sleep(3)
            reader = _ReadStatusOfChannels(self._buspro)
            reader.subnet_id, reader.device_id = self._device_address
            try:
                await reader.send()
            except Exception:  # noqa: BLE001
                self._buspro.logger.debug(
                    "Initial status read failed for %s", self._device_address
                )

        asyncio.ensure_future(_read(), loop=self._buspro.loop)
