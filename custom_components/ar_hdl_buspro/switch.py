"""Switch platform for the AR HDL BUSPRO integration."""

# Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).
# All rights reserved. Proprietary and confidential.
# Licensed software - see LICENSE in the repository root. Unauthorised
# copying, redistribution, modification, or circumvention of the licence
# check in licensing.py is prohibited.
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import ARHDLData
from .const import (
    CONF_CHANNEL,
    CONF_DEVICE_ID,
    CONF_DEVICE_TYPE,
    CONF_DEVICES,
    CONF_KEYPAD_LEDS,
    CONF_NAME,
    CONF_SUB_NUMBER,
    CONF_SUBNET_ID,
    DEVICE_TYPE_KEYPAD_BUTTON,
    DEVICE_TYPE_SWITCH,
    DEVICE_TYPE_UNIVERSAL_SWITCH,
    DOMAIN,
)
from .entity import ARHDLBaseEntity, build_device_info, build_unique_id
from .gateway import ARHDLGateway
from .keypad_led import KeypadLedSync
from .pybuspro.devices.switch import Switch as PyBusproSwitch
from .pybuspro.devices.universal_switch import UniversalSwitch as PyBusproUniversalSwitch

_LOGGER = logging.getLogger(__name__)

# Seconds after a keypad press at which the button LED is (re)set: after the
# keypad has finished applying its own LED state for the press.
KEYPAD_PRESS_LED_DELAYS = (0.5, 1.0)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up AR HDL BUSPRO switches."""
    data: ARHDLData = hass.data[DOMAIN][entry.entry_id]
    devices = entry.options.get(CONF_DEVICES, [])

    entities: list[SwitchEntity] = []
    for device_cfg in devices:
        dtype = device_cfg.get(CONF_DEVICE_TYPE)
        if dtype == DEVICE_TYPE_SWITCH:
            entities.append(ARHDLSwitch(entry, data.gateway, device_cfg))
        elif dtype == DEVICE_TYPE_UNIVERSAL_SWITCH:
            entities.append(ARHDLUniversalSwitch(entry, data.gateway, device_cfg))
        elif dtype == DEVICE_TYPE_KEYPAD_BUTTON:
            entities.append(ARHDLKeypadButton(entry, data.gateway, device_cfg))

    if entities:
        async_add_entities(entities)


class ARHDLSwitch(ARHDLBaseEntity, SwitchEntity):
    """Representation of an HDL Buspro switch channel."""

    def __init__(
        self,
        entry: ConfigEntry,
        gateway: ARHDLGateway,
        device_cfg: dict[str, Any],
    ) -> None:
        """Initialize the switch."""
        super().__init__(entry, gateway, device_cfg)

        subnet = int(device_cfg[CONF_SUBNET_ID])
        device = int(device_cfg[CONF_DEVICE_ID])
        channel = int(device_cfg[CONF_CHANNEL])

        self._switch = PyBusproSwitch(
            gateway.hdl, (subnet, device), channel, device_cfg.get(CONF_NAME, "")
        )
        # Keypad buttons whose LED should follow this relay (keypad_led.py).
        self._led_sync = KeypadLedSync(gateway.hdl, device_cfg.get(CONF_KEYPAD_LEDS))
        # Lets ARHDLBaseEntity re-read this channel once after a reconnect
        # (see _handle_gateway_availability in entity.py).
        self._resync_device = self._switch

        self._attr_unique_id = build_unique_id(entry.entry_id, device_cfg)
        self._attr_device_info = build_device_info(entry, device_cfg, gateway.device_id)
        # See light.py: several channels can share one HA device, so each one
        # needs its own visible name rather than deferring to the device name.
        self._attr_has_entity_name = False
        self._attr_name = device_cfg.get(CONF_NAME) or f"HDL {subnet}.{device} ch{channel}"

    async def async_added_to_hass(self) -> None:
        """Register update callback."""
        await super().async_added_to_hass()

        async def _after_update(_device) -> None:
            self.async_write_ha_state()
            # The relay changed on the bus (another keypad, logic, a status
            # read): bring linked keypad LEDs along. Only sends on a change.
            if self._led_sync.active:
                await self._led_sync.push(self.is_on)

        self._switch.register_device_updated_cb(_after_update)

    @property
    def is_on(self) -> bool:
        """Return True if switch is on."""
        return bool(self._switch.is_on)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        await self._switch.set_on()
        # Show the new state now (see light.py).
        self.async_write_ha_state()
        self._led_sync.push_soon(lambda: self.is_on)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        await self._switch.set_off()
        # Show the new state now (see light.py).
        self.async_write_ha_state()
        self._led_sync.push_soon(lambda: self.is_on)


class ARHDLUniversalSwitch(ARHDLBaseEntity, SwitchEntity):
    """Representation of an HDL Buspro universal switch.

    A universal switch is a virtual on/off flag (not a physical relay
    channel) commonly used to trigger scenes or logic elsewhere on the bus.
    The underlying control/telegram handling already existed in pybuspro
    (see pybuspro/devices/universal_switch.py) and via the
    set_universal_switch service; this entity is what lets it be added and
    controlled like any other switch, straight from the UI.
    """

    def __init__(
        self,
        entry: ConfigEntry,
        gateway: ARHDLGateway,
        device_cfg: dict[str, Any],
    ) -> None:
        """Initialize the universal switch."""
        super().__init__(entry, gateway, device_cfg)

        subnet = int(device_cfg[CONF_SUBNET_ID])
        device = int(device_cfg[CONF_DEVICE_ID])
        switch_number = int(device_cfg[CONF_SUB_NUMBER])

        self._switch = PyBusproUniversalSwitch(
            gateway.hdl,
            (subnet, device),
            switch_number,
            device_cfg.get(CONF_NAME, ""),
        )
        # Lets ARHDLBaseEntity re-read this switch once after a reconnect
        # (see _handle_gateway_availability in entity.py).
        self._resync_device = self._switch

        self._attr_unique_id = build_unique_id(
            entry.entry_id, device_cfg, suffix="universal_switch"
        )
        self._attr_device_info = build_device_info(entry, device_cfg, gateway.device_id)
        # See light.py/switch.py: several universal switches can share one HA
        # device, so each one needs its own visible name.
        self._attr_has_entity_name = False
        self._attr_name = (
            device_cfg.get(CONF_NAME)
            or f"HDL {subnet}.{device} universal switch {switch_number}"
        )

    async def async_added_to_hass(self) -> None:
        """Register update callback."""
        await super().async_added_to_hass()

        async def _after_update(_device) -> None:
            self.async_write_ha_state()

        self._switch.register_device_updated_cb(_after_update)

    @property
    def is_on(self) -> bool:
        """Return True if the universal switch is on."""
        return bool(self._switch.is_on)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the universal switch on."""
        await self._switch.set_on()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the universal switch off."""
        await self._switch.set_off()
        self.async_write_ha_state()


class ARHDLKeypadButton(ARHDLBaseEntity, SwitchEntity, RestoreEntity):
    """A spare keypad button that talks to Home Assistant.

    The button is programmed in the HDL software as Single ON/OFF with a
    Universal Switch target at Home Assistant's bus address (250.250); the
    integration answers for that address (virtual_device.py). Pressing the
    button flips this switch and fires `ar_hdl_buspro_keypad_button`;
    toggling the switch here sets the button's LED to match. Nothing on the
    bus is switched - it is a trigger for automations.
    """

    def __init__(
        self,
        entry: ConfigEntry,
        gateway: ARHDLGateway,
        device_cfg: dict[str, Any],
    ) -> None:
        """Initialize the keypad button."""
        super().__init__(entry, gateway, device_cfg)
        self._virtual = gateway.virtual
        self._number = int(device_cfg[CONF_SUB_NUMBER])
        self._led_sync = KeypadLedSync(gateway.hdl, device_cfg.get(CONF_KEYPAD_LEDS))
        self._attr_unique_id = build_unique_id(
            entry.entry_id, device_cfg, suffix="keypad_button"
        )
        self._attr_device_info = build_device_info(entry, device_cfg, gateway.device_id)
        self._attr_has_entity_name = False
        self._attr_name = (
            device_cfg.get(CONF_NAME) or f"HDL keypad button {self._number}"
        )
        self._attr_icon = "mdi:gesture-tap-button"

    async def async_added_to_hass(self) -> None:
        """Follow presses of the button, restoring its last state."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in ("on", "off"):
            self._virtual.set_state(self._number, last.state == "on")

        def _changed(on: bool, source) -> None:
            self.async_write_ha_state()
            if source is not None and self._led_sync.active:
                # Pressed on a keypad: set every linked button LED - the
                # pressing one included. A keypad that sends a fixed status
                # sets its own LED from that status while it finishes the
                # press, overwriting anything sent at the same instant, so
                # the LED is set shortly AFTER the press (twice, to be sure).
                self.hass.async_create_task(self._push_led_after_press(on))

        self.async_on_remove(self._virtual.add_listener(self._number, _changed))

    async def _push_led_after_press(self, on: bool) -> None:
        for delay in KEYPAD_PRESS_LED_DELAYS:
            await asyncio.sleep(delay)
            if self._virtual.state(self._number) is not on:
                return  # pressed again meanwhile; that press sets the LED
            await self._led_sync.push(on, force=True)

    @property
    def is_on(self) -> bool:
        """Return True if the button is on."""
        return self._virtual.state(self._number)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Show how the button is addressed, for programming the keypad."""
        subnet, device = self._virtual.address
        return {
            "universal_switch": self._number,
            "target_address": f"{subnet}.{device}",
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on: set the state and the linked button LEDs."""
        self._virtual.set_state(self._number, True)
        await self._led_sync.push(True, force=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off: set the state and the linked button LEDs."""
        self._virtual.set_state(self._number, False)
        await self._led_sync.push(False, force=True)
