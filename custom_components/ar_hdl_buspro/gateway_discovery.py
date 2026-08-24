"""Auto-detection of HDL Buspro IP gateways on the local network.

HDL IP interfaces (MBUS01IP.431 and friends) listen on UDP/6000 and relay
every Buspro telegram between the wire and Ethernet. There is no mDNS/SSDP on
these modules, but two facts make detection easy:

1. They answer a broadcast "read device" poke (operate code 0x000E) sent to
   bus address 255.255 -- and even devices *behind* the gateway answer, with
   every reply arriving from the *gateway's* IP address.
2. Because they broadcast to 255.255.255.255:6000, they are reachable across
   IP subnets on the same L2 segment -- exactly the situation where typing the
   right IP by hand is painful.

So: open a socket on UDP/6000, blast a couple of harmless read requests to the
broadcast addresses of every local interface, and every distinct source IP
that sends back a CRC-valid HDL frame is a gateway.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
from dataclasses import dataclass, field

from .pybuspro.core.telegram import Telegram
from .pybuspro.helpers.enums import OperateCode
from .pybuspro.helpers.telegram_helper import TelegramHelper

_LOGGER = logging.getLogger(__name__)

HDL_PORT = 6000

# Magic headers seen on HDL UDP frames. Classic Buspro uses "HDLMIRACLE";
# newer cloud-capable firmware also emits "SMARTCLOUD" frames on the same port.
_MAGIC_OFFSET = 4
_MAGICS = (b"HDLMIRACLE", b"SMARTCLOUD")

# Probes: the canonical 0x000E device-info poke plus a channel-status read, so
# both the gateway itself and anything behind it has a reason to answer.
_PROBE_OPS: tuple[object, ...] = (b"\x00\x0e", OperateCode.ReadStatusOfChannels)
# Send the probe burst this many times across the listen window.
_PROBE_ROUNDS = 3
DEFAULT_DISCOVERY_TIMEOUT = 3.0


@dataclass
class DiscoveredGateway:
    """One HDL IP gateway heard during discovery."""

    ip: str
    port: int = HDL_PORT
    frames: int = 0
    bus_addresses: set[str] = field(default_factory=set)

    @property
    def label(self) -> str:
        """Human-readable label for a config-flow dropdown."""
        extra = f" · {len(self.bus_addresses)} bus device(s) heard" if self.bus_addresses else ""
        return f"{self.ip}:{self.port} — HDL Buspro gateway{extra}"


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    """Collect HDL frames and remember who sent them."""

    def __init__(self, local_ips: set[str]) -> None:
        self._helper = TelegramHelper()
        self._local_ips = local_ips
        self.found: dict[str, DiscoveredGateway] = {}
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport) -> None:  # noqa: D102
        self.transport = transport

    def datagram_received(self, data, addr) -> None:  # noqa: D102
        try:
            ip = addr[0]
            # Ignore our own probes looping back on the broadcast.
            if ip in self._local_ips:
                return
            if len(data) < _MAGIC_OFFSET + 10:
                return
            magic = bytes(data[_MAGIC_OFFSET : _MAGIC_OFFSET + 10])
            if magic not in _MAGICS:
                return

            gw = self.found.get(ip)
            if gw is None:
                gw = DiscoveredGateway(ip=ip)
                self.found[ip] = gw
                _LOGGER.info("HDL gateway discovered at %s:%s", ip, HDL_PORT)
            gw.frames += 1

            # Classic frames also parse into telegrams; keep a rough count of
            # distinct bus addresses heard through this gateway (nice signal
            # for which gateway is the "real" one when several exist).
            if magic == _MAGICS[0]:
                telegram = self._helper.build_telegram_from_udp_data(data, addr)
                if telegram is not None and telegram.source_address:
                    src = telegram.source_address
                    if (src[0], src[1]) not in ((200, 200), (0, 0), (255, 255)):
                        gw.bus_addresses.add(f"{src[0]}.{src[1]}")
        except Exception as err:  # noqa: BLE001 - discovery must never blow up
            _LOGGER.debug("Discovery datagram parse error: %s", err)

    def error_received(self, exc) -> None:  # noqa: D102
        _LOGGER.debug("Discovery socket error: %s", exc)


def _build_probes() -> list[bytes]:
    """Build the raw probe frames sent to the broadcast addresses."""
    helper = TelegramHelper()
    probes: list[bytes] = []
    for op in _PROBE_OPS:
        telegram = Telegram()
        telegram.target_address = (255, 255)
        telegram.operate_code = op
        telegram.payload = []
        buf = helper.build_send_buffer(telegram)
        if buf is not None:
            probes.append(bytes(buf))
    return probes


def _collect_local_ips(hass) -> set[str]:
    """Best-effort set of this host's own IPv4 addresses."""
    ips: set[str] = {"127.0.0.1"}
    # Default-route trick: no packets are sent for a UDP connect().
    with contextlib.suppress(OSError):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ips.add(s.getsockname()[0])
        finally:
            s.close()
    with contextlib.suppress(OSError):
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    return ips


async def _collect_broadcast_targets(hass) -> set[str]:
    """Directed broadcast addresses for every enabled interface, plus global."""
    targets: set[str] = {"255.255.255.255"}
    if hass is not None:
        with contextlib.suppress(Exception):
            # Provided by the always-loaded core "network" integration.
            from homeassistant.components import network

            for addr in await network.async_get_ipv4_broadcast_addresses(hass):
                targets.add(str(addr))
    return targets


def _make_probe_socket() -> socket.socket:
    """Create the probe socket on an *ephemeral* port.

    Deliberately not port 6000: gateways unicast their reply to whatever
    address:port the probe came from, and an ephemeral port guarantees the
    reply lands on this socket -- even when a running ar_hdl_buspro entry already
    holds 0.0.0.0:6000 (with SO_REUSEPORT, unicast to a shared port is
    delivered to only ONE of the sockets, so probing from 6000 could have the
    replies swallowed by the live instance).
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setblocking(False)
    sock.bind(("", 0))
    return sock


def _make_listen_socket() -> socket.socket | None:
    """Optionally also listen on UDP/6000 to overhear gateway broadcasts.

    Broadcast datagrams are delivered to *every* socket bound to the port, so
    this catches gateways that ignore our probe but chatter periodically.
    Returns None if the port cannot be bound at all (non-fatal).
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        with contextlib.suppress(OSError):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.setblocking(False)
    try:
        sock.bind(("", HDL_PORT))
    except OSError:
        with contextlib.suppress(Exception):
            sock.close()
        return None
    return sock


async def async_discover_gateways(
    hass=None, timeout: float = DEFAULT_DISCOVERY_TIMEOUT
) -> list[DiscoveredGateway]:
    """Probe the LAN for HDL gateways and return every responder.

    Safe to call with no gateway configured (used by the config flow before an
    entry exists). Returns gateways sorted by how much traffic was heard from
    them, busiest first.
    """
    loop = asyncio.get_running_loop()
    local_ips = await loop.run_in_executor(None, _collect_local_ips, hass)
    targets = await _collect_broadcast_targets(hass)
    probes = _build_probes()

    try:
        probe_sock = _make_probe_socket()
    except OSError as err:
        _LOGGER.warning("Gateway discovery could not open a UDP socket: %s", err)
        return []

    # Both protocols share one result map.
    shared_found: dict[str, DiscoveredGateway] = {}
    probe_proto = _DiscoveryProtocol(local_ips)
    probe_proto.found = shared_found
    probe_transport, _ = await loop.create_datagram_endpoint(
        lambda: probe_proto, sock=probe_sock
    )

    listen_transport = None
    listen_sock = _make_listen_socket()
    if listen_sock is not None:
        listen_proto = _DiscoveryProtocol(local_ips)
        listen_proto.found = shared_found
        listen_transport, _ = await loop.create_datagram_endpoint(
            lambda: listen_proto, sock=listen_sock
        )

    try:
        interval = max(timeout / _PROBE_ROUNDS, 0.2)
        for _ in range(_PROBE_ROUNDS):
            for target in targets:
                for probe in probes:
                    with contextlib.suppress(OSError):
                        probe_transport.sendto(probe, (target, HDL_PORT))
            await asyncio.sleep(interval)
    finally:
        probe_transport.close()
        if listen_transport is not None:
            listen_transport.close()

    found = sorted(
        shared_found.values(),
        key=lambda g: (-g.frames, g.ip),
    )
    _LOGGER.info(
        "HDL gateway discovery finished: %d gateway(s) found (%s)",
        len(found),
        ", ".join(g.ip for g in found) or "none",
    )
    return found
