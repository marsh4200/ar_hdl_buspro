"""Shared entity base class and helpers for the AR HDL BUSPRO integration."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import (
    CLIMATE_KIND_AC_IR,
    CLIMATE_KIND_DLP,
    CONF_CHANNEL,
    CONF_CLIMATE_KIND,
    CONF_DEVICE_ID,
    CONF_DEVICE_TYPE,
    CONF_NAME,
    CONF_SUBNET_ID,
    CONF_SUB_NUMBER,
    DEVICE_TYPE_BINARY_SENSOR,
    DEVICE_TYPE_CLIMATE,
    DEVICE_TYPE_COVER,
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_SENSOR,
    DEVICE_TYPE_SWITCH,
    DEVICE_TYPE_UNIVERSAL_SWITCH,
    DOMAIN,
    MANUFACTURER,
    SIGNAL_GATEWAY_AVAILABILITY,
)
from .gateway import ARHDLGateway

# Friendly label for the device list's model line (see build_device_info).
# Deliberately the broad HA platform category, not the specific HDL
# hardware model or channel count -- that detail (e.g. "16ch") isn't kept
# around after a bus scan finishes, so it isn't available here. The entity
# count Home Assistant already shows next to this (e.g. "16 entities")
# covers that in practice.
_DEVICE_TYPE_LABELS: dict[str, str] = {
    DEVICE_TYPE_LIGHT: "Light",
    DEVICE_TYPE_SWITCH: "Relay",
    DEVICE_TYPE_UNIVERSAL_SWITCH: "Universal Switch",
    DEVICE_TYPE_COVER: "Curtain",
    DEVICE_TYPE_SENSOR: "Sensor",
    DEVICE_TYPE_BINARY_SENSOR: "Binary Sensor",
}
_CLIMATE_KIND_LABELS: dict[str, str] = {
    CLIMATE_KIND_DLP: "Climate – Floor Heating",
    CLIMATE_KIND_AC_IR: "Climate – AC via IR Module",
}


def _device_type_label(device_cfg: dict[str, Any]) -> str | None:
    """Return a friendly type label for device_cfg, or None if unknown."""
    dtype = device_cfg.get(CONF_DEVICE_TYPE)
    if dtype == DEVICE_TYPE_CLIMATE:
        kind = device_cfg.get(CONF_CLIMATE_KIND, CLIMATE_KIND_DLP)
        return _CLIMATE_KIND_LABELS.get(kind)
    return _DEVICE_TYPE_LABELS.get(dtype)


def build_device_info(
    entry: ConfigEntry, device_cfg: dict[str, Any], gateway_device_id: str | None = None
) -> DeviceInfo:
    """Build a DeviceInfo for an HDL device entry.

    One DeviceInfo per (subnet, device) — channels are entities of the same
    device, linked to the gateway device via `via_device_id` (the gateway's
    actual device-registry id, set on ARHDLGateway.device_id right after
    __init__.py registers it). The older `via_device` identifiers-tuple
    kwarg is deprecated as of HA's device-registry follow-up changes and is
    removed in 2027.8 -- see developers.home-assistant.io/blog/2026/08/24/
    device-registry-follow-up-changes/. gateway_device_id is optional only
    so a caller that somehow runs before the gateway device exists still
    gets a DeviceInfo (just without the via-device link) instead of an
    exception.

    The device name is deliberately *not* taken from device_cfg[CONF_NAME]:
    that field holds the per-channel entity name (e.g. "HDL 1.11 ch3"), and
    every channel on the module calls this function with its own device_cfg.
    Home Assistant's device registry only keeps one name per device, so
    whichever channel happened to be set up last would silently overwrite the
    others — the module's address is the only value guaranteed to be the same
    across all of a module's channels, so it's what we use here.

    The model line similarly gets a friendly type label appended (e.g.
    "HDL Buspro 1.11 · Relay") when every channel on this device agrees on
    one -- see _device_type_label. Same one-name-per-device caveat applies:
    if a module's channels somehow disagree on type, whichever call happens
    last wins, same as the existing name/model behaviour.
    """
    subnet = device_cfg.get(CONF_SUBNET_ID, 0)
    device = device_cfg.get(CONF_DEVICE_ID, 0)
    name = f"HDL {subnet}.{device}"
    model = f"HDL Buspro {subnet}.{device}"
    type_label = _device_type_label(device_cfg)
    if type_label:
        model = f"{model} · {type_label}"

    info = DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_{subnet}_{device}")},
        manufacturer=MANUFACTURER,
        model=model,
        name=name,
    )
    if gateway_device_id is not None:
        info["via_device_id"] = gateway_device_id
    return info


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

    # Subclasses that can be actively re-read (switch/universal-switch/light
    # channels) set this to their pybuspro device object in __init__, giving
    # it an awaitable `read_status()`. Left None for entities that already
    # handle this themselves (sensors poll on a timer; AC climate has its
    # own tuned retry logic -- see AirConditioner in pybuspro/devices/
    # climate.py for why blanket polling was reverted there) or that have
    # no reliable status read at all (curtain/cover modules).
    _resync_device: Any = None

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
        """Update availability from the gateway signal.

        On a reconnect (False -> True; the initial connect never sees this
        transition since `_gateway_available` already starts True) also
        kick off a one-shot resync of `_resync_device`, if set. Without
        this, a switch/light's last-known state -- which is a Python
        object living for the lifetime of the config entry, not re-created
        just because the socket dropped and came back -- goes stale
        forever after any mid-session gateway blip: whatever the physical
        device was doing when the link was lost keeps displaying in HA
        until someone operates it manually. This is a single on-demand
        read per reconnect event, not a timer -- see the _resync_device
        docstring above for why that distinction matters on this bus.
        """
        was_available = self._gateway_available
        self._gateway_available = available
        self.async_write_ha_state()

        if available and not was_available and self._resync_device is not None:
            self.hass.async_create_task(self._async_resync_after_reconnect())

    async def _async_resync_after_reconnect(self) -> None:
        """Re-read `_resync_device`'s status once, after a reconnect."""
        try:
            await self._resync_device.read_status()
        except Exception:  # noqa: BLE001
            self._gateway.hdl.logger.debug(
                "Post-reconnect resync read failed for %s",
                getattr(self._resync_device, "device_identifier", "?"),
            )

    @property
    def available(self) -> bool:
        """Return True if the gateway is connected."""
        return self._gateway_available
