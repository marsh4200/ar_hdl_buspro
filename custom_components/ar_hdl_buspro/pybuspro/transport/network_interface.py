"""Network interface tying UDP transport to the telegram helper."""
from __future__ import annotations

from ..helpers.telegram_helper import TelegramHelper
from .udp_client import UDPClient


class NetworkInterface:
    """Manage the UDP socket and translate to/from telegrams."""

    def __init__(self, buspro, gateway_address_send_receive) -> None:
        """Initialize the interface."""
        self.buspro = buspro
        self.gateway_address_send_receive = gateway_address_send_receive
        self.udp_client: UDPClient | None = None
        self.callback = None
        self._init_udp_client()
        self._th = TelegramHelper()

    def _init_udp_client(self) -> None:
        self.udp_client = UDPClient(
            self.buspro,
            self.gateway_address_send_receive,
            self._udp_request_received,
        )

    def _udp_request_received(self, data, address) -> None:
        """Handle incoming UDP datagrams.

        HDL gateways broadcast every bus telegram to 255.255.255.255:6000,
        and UDP broadcasts cross IP-subnet boundaries on the same L2 segment.
        Without a source filter we would ingest telegrams from *every* HDL
        gateway and HDL software instance on the wire -- which shows up as
        phantom devices and entities that belong to a different installation.
        When the owning Buspro client has resolved the gateway's IP(s), drop
        anything that didn't come from them.
        """
        allowed = getattr(self.buspro, "allowed_source_ips", None)
        if allowed and address and address[0] not in allowed:
            self.buspro.logger.debug(
                "Dropping UDP frame from foreign source %s (gateway filter)",
                address[0],
            )
            return
        if self.callback is None:
            return
        telegram = self._th.build_telegram_from_udp_data(data, address)
        if telegram is not None:
            self.callback(telegram)

    async def _send_message(self, message) -> None:
        if self.udp_client is not None:
            await self.udp_client.send_message(message)

    # Public API
    def register_callback(self, callback) -> None:
        """Register a telegram-received callback."""
        self.callback = callback

    async def start(self) -> None:
        """Start the UDP transport."""
        if self.udp_client is not None:
            await self.udp_client.start()

    async def stop(self) -> None:
        """Stop the UDP transport."""
        if self.udp_client is not None:
            await self.udp_client.stop()
            self.udp_client = None

    async def send_telegram(self, telegram) -> None:
        """Serialize and send a telegram."""
        message = self._th.build_send_buffer(telegram)
        if message is None:
            return
        gateway_address_send, _ = self.gateway_address_send_receive
        self.buspro.logger.debug(
            self._th.build_telegram_from_udp_data(message, gateway_address_send)
        )
        if self.udp_client is not None:
            await self.udp_client.send_message(message)
