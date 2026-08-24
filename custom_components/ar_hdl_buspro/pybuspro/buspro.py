"""Top-level Buspro client object."""
from __future__ import annotations

import asyncio
import logging

from .helpers.enums import OperateCode
from .transport.network_interface import NetworkInterface


class StateUpdater:
    """Periodic state refresher (optional)."""

    def __init__(self, buspro: "Buspro", sleep: int = 10) -> None:
        self.buspro = buspro
        self.run_forever = True
        self.run_task: asyncio.Task | None = None
        self.sleep = sleep

    async def start(self) -> None:
        """Start the periodic loop."""
        self.run_task = self.buspro.loop.create_task(self.run())

    async def run(self) -> None:
        """Run the periodic sync."""
        await asyncio.sleep(0)
        self.buspro.logger.info(
            "Starting StateUpdater with %s seconds interval", self.sleep
        )
        while True:
            await asyncio.sleep(self.sleep)
            await self.buspro.sync()


class Buspro:
    """Client for an HDL Buspro gateway."""

    def __init__(
        self,
        gateway_address_send_receive: tuple[tuple[str, int], tuple[str, int]],
        loop_: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        """Initialize the Buspro client."""
        self.loop = loop_ or asyncio.get_event_loop()
        self.state_updater: StateUpdater | None = None
        self.started = False
        self.network_interface: NetworkInterface | None = None
        self.logger = logging.getLogger("ar_hdl_buspro.buspro")
        self.telegram_logger = logging.getLogger("ar_hdl_buspro.telegram")

        self.callback_all_messages = None
        self._telegram_received_cbs: list[dict] = []

        # Optional hook fired when the UDP transport is lost unexpectedly
        # (not on a clean stop()). Set by the integration's gateway wrapper.
        self.on_connection_lost = None

        # Source-IP allowlist for incoming UDP frames. When non-empty, the
        # network interface drops datagrams from any other IP, so telegrams
        # broadcast by *other* HDL gateways/software on the same L2 segment
        # cannot pollute this instance with phantom devices. Populated by the
        # integration's gateway wrapper after resolving the configured host.
        self.allowed_source_ips: set[str] = set()

        self.gateway_address_send_receive = gateway_address_send_receive

    async def start(self, state_updater: bool = False) -> None:
        """Connect to the gateway and start listening for telegrams."""
        self.network_interface = NetworkInterface(
            self, self.gateway_address_send_receive
        )
        self.network_interface.register_callback(self._callback_all_messages)
        await self.network_interface.start()

        if state_updater:
            self.state_updater = StateUpdater(self)
            await self.state_updater.start()

        self.started = True

    async def stop(self) -> None:
        """Disconnect from the gateway."""
        await self._stop_network_interface()
        self.started = False

    def _callback_all_messages(self, telegram) -> None:
        """Invoke per-device callbacks for an incoming telegram."""
        if telegram is None:
            return
        self.telegram_logger.debug(telegram)

        if self.callback_all_messages is not None:
            self.callback_all_messages(telegram)

        for cb in list(self._telegram_received_cbs):
            device_address = cb["device_address"]
            if (
                device_address == telegram.target_address
                or device_address == telegram.source_address
            ):
                if telegram.operate_code is not OperateCode.TIME_IF_FROM_LOGIC_OR_SECURITY:
                    postfix = cb.get("postfix")
                    try:
                        if postfix is not None:
                            cb["callback"](telegram, postfix)
                        else:
                            cb["callback"](telegram)
                    except Exception as err:  # noqa: BLE001
                        self.logger.warning(
                            "Telegram callback error: %s", err
                        )

    def _notify_connection_lost(self) -> None:
        """Called by the transport when the socket dies unexpectedly."""
        if self.on_connection_lost is None:
            return
        try:
            self.on_connection_lost()
        except Exception as err:  # noqa: BLE001
            self.logger.warning("on_connection_lost callback error: %s", err)

    async def _stop_network_interface(self) -> None:
        if self.network_interface is not None:
            await self.network_interface.stop()
            self.network_interface = None

    def register_telegram_received_all_messages_cb(self, telegram_received_cb) -> None:
        """Register a global telegram callback."""
        self.callback_all_messages = telegram_received_cb

    def register_telegram_received_device_cb(
        self, telegram_received_cb, device_address, postfix=None
    ) -> None:
        """Register a per-device telegram callback."""
        self._telegram_received_cbs.append(
            {
                "callback": telegram_received_cb,
                "device_address": device_address,
                "postfix": postfix,
            }
        )

    def unregister_telegram_received_device_cb(
        self, telegram_received_cb, device_address, postfix=None
    ) -> None:
        """Unregister a per-device telegram callback."""
        try:
            self._telegram_received_cbs.remove(
                {
                    "callback": telegram_received_cb,
                    "device_address": device_address,
                    "postfix": postfix,
                }
            )
        except ValueError:
            pass

    @staticmethod
    async def sync():  # pragma: no cover - kept for API parity
        """Hook for the optional StateUpdater."""
        raise NotImplementedError
