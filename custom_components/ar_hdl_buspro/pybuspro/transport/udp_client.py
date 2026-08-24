"""UDP transport for HDL Buspro telegrams."""
from __future__ import annotations

import asyncio
import socket
from typing import Callable


class UDPClient:
    """Async UDP client for HDL Buspro."""

    class UDPClientFactory(asyncio.DatagramProtocol):
        """Datagram protocol forwarding incoming data to a callback."""

        def __init__(self, buspro, data_received_callback: Callable | None = None) -> None:
            self.buspro = buspro
            self.transport: asyncio.DatagramTransport | None = None
            self.data_received_callback = data_received_callback

        def connection_made(self, transport) -> None:
            self.transport = transport

        def datagram_received(self, data, address) -> None:
            if self.data_received_callback is not None:
                try:
                    self.data_received_callback(data, address)
                except Exception as err:  # noqa: BLE001
                    self.buspro.logger.warning(
                        "Error in datagram callback: %s", err
                    )

        def error_received(self, exc) -> None:
            self.buspro.logger.warning("UDP error received: %s", exc)

        def connection_lost(self, exc) -> None:
            self.buspro.logger.info("UDP transport closed: %s", exc)
            owner = getattr(self, "owner", None)
            # Only treat this as a failure if it wasn't a deliberate stop().
            if owner is not None and not owner.closing:
                self.buspro._notify_connection_lost()  # noqa: SLF001

    def __init__(self, buspro, gateway_address_send_receive, callback) -> None:
        self.buspro = buspro
        self._gateway_address_send, self._gateway_address_receive = (
            gateway_address_send_receive
        )
        self.callback = callback
        self.transport: asyncio.DatagramTransport | None = None
        self.closing = False

    def _data_received_callback(self, data, address) -> None:
        self.callback(data, address)

    def _create_multicast_sock(self) -> socket.socket | None:
        """Create the receive socket.

        We try, in order:

        1. Bind to the user's preferred (host, port) with SO_REUSEADDR /
           SO_REUSEPORT so multiple Buspro listeners can coexist (Linux/macOS).
        2. If that fails (Windows, port held without REUSE support, locked
           interface, etc.), bind to ``(host, 0)`` and let the OS pick an
           ephemeral receive port. We can still SEND telegrams to the gateway,
           and most HDL gateways will direct-reply to whatever port we sent
           from, so command/response still works -- only passive broadcasts
           sent to UDP/6000 from other devices on the bus get missed.
        """
        host, port = self._gateway_address_receive
        bind_host = host or ""

        for bind_port in (port, 0):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                if hasattr(socket, "SO_REUSEPORT"):
                    try:
                        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                    except OSError:
                        pass
                sock.setblocking(False)
                sock.bind((bind_host, bind_port))
                try:
                    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 0)
                except OSError:
                    # Not multicast-capable; non-fatal.
                    pass
                actual = sock.getsockname()
                if bind_port == 0:
                    self.buspro.logger.warning(
                        "UDP port %s on %s was unavailable; using ephemeral "
                        "port %s instead. Outbound commands will work, but "
                        "broadcasts sent to UDP/%s by other devices will be "
                        "missed.",
                        port,
                        bind_host or "*",
                        actual[1],
                        port,
                    )
                else:
                    self.buspro.logger.debug(
                        "UDP socket bound to %s", actual
                    )
                return sock
            except OSError as ex:
                self.buspro.logger.debug(
                    "UDP bind to (%s, %s) failed: %s",
                    bind_host or "*",
                    bind_port,
                    ex,
                )
                try:
                    sock.close()
                except Exception:  # noqa: BLE001
                    pass

        self.buspro.logger.warning(
            "Could not bind any UDP socket for %s",
            self._gateway_address_receive,
        )
        return None

    async def _connect(self) -> None:
        try:
            factory = UDPClient.UDPClientFactory(
                self.buspro, data_received_callback=self._data_received_callback
            )
            factory.owner = self
            sock = self._create_multicast_sock()
            if sock is None:
                raise OSError("Failed to create receive socket")
            transport, _ = await self.buspro.loop.create_datagram_endpoint(
                lambda: factory, sock=sock
            )
            self.transport = transport
        except Exception as ex:  # noqa: BLE001
            self.buspro.logger.warning(
                "Could not create UDP endpoint: %s", ex
            )
            raise

    async def start(self) -> None:
        """Open the UDP socket."""
        self.closing = False
        await self._connect()

    async def stop(self) -> None:
        """Close the UDP socket."""
        self.closing = True
        if self.transport is not None:
            self.transport.close()
            self.transport = None

    async def send_message(self, message) -> None:
        """Send a UDP datagram to the gateway."""
        if self.transport is not None:
            try:
                self.transport.sendto(message, self._gateway_address_send)
            except OSError as err:
                self.buspro.logger.warning("UDP send failed: %s", err)
        else:
            self.buspro.logger.info(
                "Could not send message. Transport is None."
            )
