"""Shared entity base class and helpers for the AR HDL BUSPRO integration."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import (
    CONF_CHANNEL,
    CONF_DEVICE_ID,
    CONF_NAME,
    CONF_SUBNET_ID,
    CONF_SUB_NUMBER,
    DOMAIN,
    MANUFACTURER,
    SIGNAL_GATEWAY_AVAILABILITY,
)
from .gateway import ARHDLGateway


def build_device_info(
    entry: ConfigEntry, device_cfg: dict[str, Any]
) -> DeviceInfo:
    """Build a DeviceInfo for an HDL device entry.

    One DeviceInfo per (subnet, device) — channels are entities of the same
    device. The gateway is identified as `via_device`.

    The device name is deliberately *not* taken from device_cfg[CONF_NAME]:
    that field holds the per-channel entity name (e.g. "HDL 1.11 ch3"), and
    every channel on the module calls this function with its own device_cfg.
    Home Assistant's device registry only keeps one name per device, so
    whichever channel happened to be set up last would silently overwrite the
    others — the module's address is the only value guaranteed to be the same
    across all of a module's channels, so it's what we use here.
    """
    subnet = device_cfg.get(CONF_SUBNET_ID, 0)
    device = device_cfg.get(CONF_DEVICE_ID, 0)
    name = f"HDL {subnet}.{device}"

    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_{subnet}_{device}")},
        manufacturer=MANUFACTURER,
        model=f"HDL Buspro {subnet}.{device}",
        name=name,
        via_device=(DOMAIN, f"gateway_{entry.entry_id}"),
    )


def build_unique_id(entry_id: str, device_cfg: dict[str, Any], suffix: str = "") -> str:
    """Build a deterministic unique_id for an entity.

    Uses the device's stored UUID (from the options flow) as the stable
    discriminator so that renaming subnet/device addresses keeps the entity
    identity. Falls back to the address tuple if no id is present (legacy
    migration only).
    """
    base = device_cfg.get("id") or "{}_{}_{}_{}".format(
        device_cfg.get(CONF_SUBNET_ID, 0),
        device_cfg.get(CONF_DEVICE_ID, 0),
        device_cfg.get(CONF_CHANNEL, 0),
        device_cfg.get(CONF_SUB_NUMBER, 0),
    )
    return f"{entry_id}_{base}" + (f"_{suffix}" if suffix else "")


class ARHDLBaseEntity(Entity):
    """Base class for AR HDL BUSPRO entities providing common availability/wiring."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(
        self,
        entry: ConfigEntry,
        gateway: ARHDLGateway,
        device_cfg: dict[str, Any],
    ) -> None:
        """Initialize the base entity."""
        self._entry = entry
        self._gateway = gateway
        self._device_cfg = device_cfg
        self._gateway_available = gateway.available

    async def async_added_to_hass(self) -> None:
        """Wire up availability dispatcher."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_GATEWAY_AVAILABILITY.format(entry_id=self._entry.entry_id),
                self._handle_gateway_availability,
            )
        )

    @callback
    def _handle_gateway_availability(self, available: bool) -> None:
        """Update availability from the gateway signal."""
        self._gateway_available = available
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Return True if the gateway is connected."""
        return self._gateway_available
