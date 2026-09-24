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
        self._locked_warned_at: float | None = None
        self._init_udp_client()
        self._th = TelegramHelper(buspro)

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
            # Warn ONCE per foreign source IP, at WARNING, not debug.
            # A dropped frame here is indistinguishable from a device that
            # never answered: the entity just sits at its default state. With
            # only a debug line on a non-default logger, an installation whose
            # gateway IP changed -- or one with a second gateway relaying part
            # of the bus -- looks exactly like broken hardware, with nothing
            # in the log to say otherwise. Once per IP keeps it out of the way
            # while still being impossible to miss the first time.
            seen = self.buspro.dropped_source_ips
            if address[0] not in seen:
                seen.add(address[0])
                self.buspro.logger.warning(
                    "Ignoring HDL telegrams from %s: it is not this entry's "
                    "gateway (%s). If devices on this bus are reached through "
                    "%s, their broadcasts are being discarded -- check the "
                    "gateway host in the integration options.",
                    address[0],
                    ", ".join(sorted(allowed)) or "unset",
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

    def _send_permitted(self) -> bool:
        """Return True if this client is licensed to drive the bus.

        This is the integration's functional enforcement point, and it is
        here rather than only on entity availability for a reason: Home
        Assistant does not block service calls to entities that report
        `unavailable`, so gating availability alone leaves every automation,
        script and `light.turn_on` call working indefinitely on an expired
        install. Everything that reaches the wire - control telegrams and
        status reads alike - passes through this method.

        The unlock provider is installed by the integration's gateway
        wrapper (see gateway.py) and yields a token that has to be *minted*
        for this Server ID and status; a bare truthy value does not satisfy
        it.
        """
        provider = getattr(self.buspro, "unlock_provider", None)
        if provider is None:
            # No provider installed at all: this client was built outside
            # the integration's setup path.
            return False

        try:
            token = provider()
        except Exception:  # noqa: BLE001 - a broken provider must not send
            return False

        if token is None:
            return False

        server_id = getattr(self.buspro, "license_server_id", "") or ""
        try:
            return any(
                token.matches(server_id, status)
                for status in ("licensed", "trial")
            )
        except Exception:  # noqa: BLE001
            return False

    def _warn_locked_once(self) -> None:
        """Log the locked-out warning at most once per hour."""
        import time

        now = time.monotonic()
        last = getattr(self, "_locked_warned_at", None)
        if last is not None and now - last < 3600:
            return
        self._locked_warned_at = now
        self.buspro.logger.warning(
            "AR HDL BUSPRO is not licensed on this Home Assistant instance, "
            "so bus traffic is suspended: commands and status reads are "
            "being discarded. Enter a licence key in the integration's "
            "options, or request one at https://activatelicense.arsmarthome.co.za"
        )

    async def send_telegram(self, telegram) -> None:
        """Serialize and send a telegram."""
        if not self._send_permitted():
            self._warn_locked_once()
            return

        message = self._th.build_send_buffer(telegram)
        if message is None:
            return
        gateway_address_send, _ = self.gateway_address_send_receive
        self.buspro.logger.debug(
            self._th.build_telegram_from_udp_data(message, gateway_address_send)
        )
        if self.udp_client is not None:
            await self.udp_client.send_message(message)
