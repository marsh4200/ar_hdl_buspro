"""Network interface tying UDP transport to the telegram helper."""
from __future__ import annotations

import logging

from ..helpers.telegram_helper import HA_SOURCE_ADDRESS, TelegramHelper
from .udp_client import UDPClient

# Same frame from two different IPs within this many seconds = one frame.
_DUP_WINDOW = 0.5


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
        if self.callback is None:
            return
        allowed = getattr(self.buspro, "allowed_source_ips", None)
        foreign = bool(allowed and address and address[0] not in allowed)

        # The same bus frame can arrive twice: once from the configured
        # gateway and once from a second HDL IP device on the same bus (a
        # wireless mesh gateway, a second IP module). Drop the copy that
        # comes from a DIFFERENT IP within a short window, so replies and
        # keypad presses are never handled twice. Repeats from the same IP
        # (a button pressed twice quickly) are real and kept.
        if address and len(data) > 16:
            now = self.buspro.loop.time()
            recent = self.__dict__.setdefault("_recent_frames", {})
            if len(recent) > 256:
                for key in [k for k, v in recent.items() if now - v[0] > _DUP_WINDOW]:
                    del recent[key]
            key = bytes(data[16:])
            seen = recent.get(key)
            if seen is not None and now - seen[0] < _DUP_WINDOW and seen[1] != address[0]:
                return
            recent[key] = (now, address[0])

        telegram = self._th.build_telegram_from_udp_data(data, address)
        if telegram is None:
            return

        if foreign and not self._relevant_to_this_entry(telegram):
            # Not one of our devices and not addressed to Home Assistant:
            # another installation, or HDL software on a PC. Drop it, and
            # say so once per IP.
            dropped = self.buspro.dropped_source_ips
            if address[0] not in dropped:
                dropped.add(address[0])
                self.buspro.logger.warning(
                    "Ignoring HDL telegrams from %s for devices that are not "
                    "configured here (gateway is %s). Telegrams from %s for "
                    "this entry's own devices are still accepted.",
                    address[0],
                    ", ".join(sorted(allowed)) or "unset",
                    address[0],
                )
            return
        if foreign:
            relays = self.__dict__.setdefault("_relay_ips_logged", set())
            if address[0] not in relays:
                relays.add(address[0])
                self.buspro.logger.info(
                    "Accepting telegrams for this entry's devices relayed via %s "
                    "(a second HDL IP device on the bus, e.g. a wireless gateway)",
                    address[0],
                )
        self.callback(telegram)

    def _relevant_to_this_entry(self, telegram) -> bool:
        """Is a telegram from a non-gateway IP one we should still act on?

        Up to 5.0.17 everything from another IP was dropped. On a site whose
        wireless devices reach the network through their own HDL IP gateway,
        that silently discarded their replies and button presses - lights
        changed but Home Assistant never heard about it. Now such a telegram
        is accepted when it comes FROM a device configured in this entry, or
        is addressed TO Home Assistant (keypad buttons, LED confirmations,
        read replies). Phantom devices from other installations still never
        appear: their sources are not configured here.
        """
        target = telegram.target_address
        if target is not None and tuple(target) == tuple(HA_SOURCE_ADDRESS):
            return True
        source = telegram.source_address
        if source is None:
            return False
        source = tuple(source)
        for cb in getattr(self.buspro, "_telegram_received_cbs", ()):
            addr = cb.get("device_address")
            if addr is not None and tuple(addr) == source:
                return True
        return False

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
        if self.buspro.logger.isEnabledFor(logging.DEBUG):
            # Re-parsing every outbound frame just to log it is only worth
            # doing when someone is actually reading the debug log.
            gateway_address_send, _ = self.gateway_address_send_receive
            self.buspro.logger.debug(
                self._th.build_telegram_from_udp_data(message, gateway_address_send)
            )

        if self.udp_client is not None:
            await self.udp_client.send_message(message)
