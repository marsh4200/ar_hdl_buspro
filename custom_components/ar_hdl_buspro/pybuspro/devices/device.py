"""Base device class for the pybuspro library."""
from __future__ import annotations

import asyncio
import random

from .control import _ReadStatusOfChannels

# How often to retry the startup channel-status read while nothing real has
# been observed yet (see _call_read_current_status_of_channels below). A
# single one-shot attempt is fragile: on a full HA restart, every Switch and
# Light on the integration schedules its read for the same ~3s mark, so a
# handful of channels can turn into a burst of simultaneous UDP requests
# right as the gateway/network stack is still warming up -- lose one packet
# (request or response, no retry) and that channel is stuck showing its
# default "off" forever, until someone operates it manually. Mirrors the
# retry-until-first-status pattern already proven safe for AC/IR channels
# in climate.py's AirConditioner (see its docstring for why *continuous*
# polling was tried and reverted instead -- this stops the moment a real
# reading arrives, same as that one).
_CHANNEL_STATUS_RETRY_SECONDS = 20


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
        # Set by Switch/Light once a real ReadStatusOfChannelsResponse or
        # SingleChannelControlResponse for this channel has been seen --
        # tells _call_read_current_status_of_channels' startup retry loop
        # when to stop. Deliberately separate from "brightness == 0" since
        # that's a legitimate real reading, not just "never confirmed".
        self._got_initial_status = False

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
        """Schedule a read of the device's channel status.

        At startup (run_from_init=True) this retries every
        _CHANNEL_STATUS_RETRY_SECONDS until a real status telegram is seen
        for this channel, then stops for good -- see the module-level
        docstring above for why a single attempt isn't reliable enough.
        A scene-triggered re-read (run_from_init=False, called after this
        device's initial status is already known) stays a single attempt,
        since it's just picking up a likely change, not establishing the
        only source of truth for the entity's starting state.
        """

        async def _read():
            if run_from_init:
                # Stagger the very first attempt so a restart with many
                # channels doesn't fire them all in the same UDP burst.
                await asyncio.sleep(3 + random.uniform(0, 2))
                while not self._got_initial_status:
                    reader = _ReadStatusOfChannels(self._buspro)
                    reader.subnet_id, reader.device_id = self._device_address
                    try:
                        await reader.send()
                    except Exception:  # noqa: BLE001
                        self._buspro.logger.debug(
                            "Startup status read failed for %s",
                            self._device_address,
                        )
                    await asyncio.sleep(_CHANNEL_STATUS_RETRY_SECONDS)
            else:
                reader = _ReadStatusOfChannels(self._buspro)
                reader.subnet_id, reader.device_id = self._device_address
                try:
                    await reader.send()
                except Exception:  # noqa: BLE001
                    self._buspro.logger.debug(
                        "Status read failed for %s", self._device_address
                    )

        asyncio.ensure_future(_read(), loop=self._buspro.loop)
