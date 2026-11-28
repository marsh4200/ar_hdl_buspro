"""Button platform for the AR HDL BUSPRO integration.

Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).
All rights reserved. Proprietary and confidential.
Licensed software - see LICENSE in the repository root. Unauthorised
copying, redistribution, modification, or circumvention of the licence
check in licensing.py is prohibited.

A universal switch set to "Show as: button" (CONF_UV_SHOW_AS). Pressing it
sends the universal switch once, ON or OFF as configured. This is the right
shape for IR modules (HDL-MIRC04.40): each universal switch fires a stored IR
code, and the module keeps no state an on/off switch could truthfully show.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ARHDLData
from .const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_TYPE,
    CONF_DEVICES,
    CONF_IR_ACTION,
    CONF_NAME,
    CONF_SUB_NUMBER,
    CONF_SUBNET_ID,
    CONF_UV_BUTTON_STATUS,
    CONF_UV_SHOW_AS,
    DEVICE_TYPE_UNIVERSAL_SWITCH,
    DOMAIN,
    UV_BUTTON_OFF,
    UV_BUTTON_ON,
    UV_SHOW_AS_BUTTON,
)
from .entity import ARHDLBaseEntity, build_device_info, build_unique_id
from .gateway import ARHDLGateway
from .pybuspro.devices.control import _UniversalSwitch
from .pybuspro.helpers.enums import SwitchStatusOnOff

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up AR HDL BUSPRO buttons."""
    data: ARHDLData = hass.data[DOMAIN][entry.entry_id]
    devices = entry.options.get(CONF_DEVICES, [])

    entities: list[ARHDLUniversalButton] = [
        ARHDLUniversalButton(entry, data.gateway, device_cfg)
        for device_cfg in devices
        if device_cfg.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_UNIVERSAL_SWITCH
        and device_cfg.get(CONF_UV_SHOW_AS) == UV_SHOW_AS_BUTTON
    ]
    for ent in entities:
        ent.assign_clean_entity_id(hass, "button.{}")
    if entities:
        async_add_entities(entities)


class ARHDLUniversalButton(ARHDLBaseEntity, ButtonEntity):
    """A universal switch sent once per press."""

    def __init__(
        self,
        entry: ConfigEntry,
        gateway: ARHDLGateway,
        device_cfg: dict[str, Any],
    ) -> None:
        """Initialize the button."""
        super().__init__(entry, gateway, device_cfg)
        self._gateway = gateway
        self._address = (
            int(device_cfg[CONF_SUBNET_ID]),
            int(device_cfg[CONF_DEVICE_ID]),
        )
        self._switch_number = int(device_cfg[CONF_SUB_NUMBER])
        self._send_off = device_cfg.get(CONF_UV_BUTTON_STATUS) == UV_BUTTON_OFF
        self._ir_action = device_cfg.get(CONF_IR_ACTION)

        # ON and OFF buttons may share one device entry's switch number but
        # are separate entries, so the entry UUID alone keeps them apart.
        self._attr_unique_id = build_unique_id(
            entry.entry_id, device_cfg, suffix="universal_button"
        )
        self._attr_device_info = build_device_info(
            entry, device_cfg, gateway.device_id
        )
        self._attr_has_entity_name = False
        subnet, device = self._address
        self._attr_name = device_cfg.get(CONF_NAME) or (
            f"HDL {subnet}.{device} universal switch {self._switch_number}"
            + (" off" if self._send_off else "")
        )
        if self._ir_action:
            self._attr_icon = "mdi:remote"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Which universal switch a press sends, and what it does."""
        attrs = dict(super().extra_state_attributes)
        attrs["universal_switch"] = self._switch_number
        attrs["sends"] = UV_BUTTON_OFF if self._send_off else UV_BUTTON_ON
        if self._ir_action:
            attrs["ir_action"] = self._ir_action
        return attrs

    async def async_press(self) -> None:
        """Send the universal switch once."""
        ctrl = _UniversalSwitch(self._gateway.hdl)
        ctrl.subnet_id, ctrl.device_id = self._address
        ctrl.switch_number = self._switch_number
        ctrl.switch_status = (
            SwitchStatusOnOff.OFF if self._send_off else SwitchStatusOnOff.ON
        )
        await ctrl.send()
