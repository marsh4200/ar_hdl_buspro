"""Gateway/connection management for the AR HDL BUSPRO integration."""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import SIGNAL_GATEWAY_AVAILABILITY
from .pybuspro.buspro import Buspro

if TYPE_CHECKING:
    pass

_LOGGER = logging.getLogger(__name__)

RECONNECT_DELAYS = [5, 10, 20, 30, 60]  # exponential-ish backoff (seconds)


class ARHDLGateway:
    """Wrap the underlying Buspro client and manage availability."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        host: str,
        port: int,
        local_ip: str = "",
    ) -> None:
        """Initialize the gateway."""
        self.hass = hass
        self.entry_id = entry_id
        self.host = host
        self.port = port
        self.local_ip = local_ip
        self._available = False
        self._stop_requested = False
        self._reconnect_task: asyncio.Task | None = None

        # The receive-bind address: use specified local IP if provided
        send_addr = (host, port)
        receive_addr = (local_ip, port)
        self.hdl = Buspro((send_addr, receive_addr), hass.loop)
        # When the UDP transport dies unexpectedly (interface change, docker
        # network flap, etc.) mark unavailable and start reconnecting.
        self.hdl.on_connection_lost = self._handle_connection_lost

    @property
    def available(self) -> bool:
        """Return True if the gateway is currently reachable."""
        return self._available

    async def _async_refresh_source_filter(self) -> None:
        """Resolve the configured host and install the source-IP allowlist.

        Everything the gateway relays arrives from its own IP, so this is a
        precise filter: telegrams broadcast by other HDL gateways or HDL
        software on the same L2 segment (even on other IP subnets) get
        dropped instead of materialising as phantom devices/entities.
        """
        import socket as _socket

        try:
            infos = await self.hass.async_add_executor_job(
                _socket.getaddrinfo, self.host, None, _socket.AF_INET
            )
            ips = {info[4][0] for info in infos}
        except OSError as err:
            _LOGGER.warning(
                "Could not resolve gateway host %s (%s); the source-IP filter "
                "is disabled, so telegrams from other HDL gateways on the "
                "network will NOT be filtered out",
                self.host,
                err,
            )
            self.hdl.allowed_source_ips = set()
            return
        self.hdl.allowed_source_ips = ips
        _LOGGER.debug("Gateway source-IP filter set to %s", ips)

    async def async_connect(self) -> bool:
        """Connect to the gateway. Returns True on success."""
        await self._async_refresh_source_filter()
        try:
            await self.hdl.start(state_updater=False)
        except Exception as err:  # noqa: BLE001 - third-party can raise anything
            _LOGGER.warning(
                "Could not connect to AR HDL BUSPRO gateway %s:%s: %s",
                self.host,
                self.port,
                err,
            )
            self._available = False
            self._notify_availability()
            return False

        self._available = True
        self._notify_availability()
        _LOGGER.info(
            "Connected to AR HDL BUSPRO gateway at %s:%s", self.host, self.port
        )
        return True

    async def async_disconnect(self) -> None:
        """Disconnect cleanly and cancel reconnect."""
        self._stop_requested = True
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
            self._reconnect_task = None

        try:
            await self.hdl.stop()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Error stopping HDL client: %s", err)

        self._available = False
        self._notify_availability()

    def _handle_connection_lost(self) -> None:
        """React to an unexpected transport loss (called from the event loop)."""
        if self._stop_requested:
            return
        if self._available:
            _LOGGER.warning(
                "Lost connection to AR HDL BUSPRO gateway %s:%s; reconnecting",
                self.host,
                self.port,
            )
        self._available = False
        self._notify_availability()
        self.async_schedule_reconnect()

    def async_schedule_reconnect(self) -> None:
        """Schedule a reconnect attempt (called when the link drops)."""
        if self._stop_requested:
            return
        if self._reconnect_task and not self._reconnect_task.done():
            return
        self._reconnect_task = self.hass.async_create_task(
            self._reconnect_loop()
        )

    async def _reconnect_loop(self) -> None:
        """Keep trying to reconnect until success or stop."""
        attempt = 0
        while not self._stop_requested:
            delay = RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)]
            _LOGGER.debug(
                "AR HDL BUSPRO gateway reconnect attempt %s in %ss", attempt + 1, delay
            )
            await asyncio.sleep(delay)
            if self._stop_requested:
                return
            try:
                await self.hdl.stop()
            except Exception:  # noqa: BLE001
                pass
            if await self.async_connect():
                return
            attempt += 1

    def _notify_availability(self) -> None:
        """Fire a dispatcher signal so entities can re-evaluate `available`."""
        async_dispatcher_send(
            self.hass,
            SIGNAL_GATEWAY_AVAILABILITY.format(entry_id=self.entry_id),
            self._available,
        )

    def diagnostics(self) -> dict[str, Any]:
        """Return diagnostic information about the gateway."""
        return {
            "host": self.host,
            "port": self.port,
            "local_ip": self.local_ip or "auto",
            "source_ip_filter": sorted(self.hdl.allowed_source_ips) or "disabled",
            "available": self._available,
            "stop_requested": self._stop_requested,
            "reconnect_active": bool(
                self._reconnect_task and not self._reconnect_task.done()
            ),
        }
