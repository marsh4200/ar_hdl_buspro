"""Config flow for the AR HDL BUSPRO integration."""

# Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).
# All rights reserved. Proprietary and confidential.
# Licensed software - see LICENSE in the repository root. Unauthorised
# copying, redistribution, modification, or circumvention of the licence
# check in licensing.py is prohibited.
from __future__ import annotations

import re
import asyncio
import logging
import uuid
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import (
    AC_HVAC_MODES,
    BINARY_KIND_DRY_CONTACT,
    BINARY_KIND_MOTION,
    BINARY_KIND_SINGLE_CHANNEL,
    BINARY_KIND_UNIVERSAL_SWITCH,
    BINARY_KINDS,
    CLIMATE_KIND_AC_IR,
    CLIMATE_KIND_AC_PANEL,
    CLIMATE_KIND_DLP,
    CLIMATE_KINDS,
    CLIMATE_PRESETS,
    CONF_AC_HVAC_MODES,
    CONF_BINARY_KIND,
    CONF_CHANNEL,
    CONF_CLIMATE_KIND,
    CONF_CLOSE_CHANNEL,
    CONF_COVER_MODE,
    CONF_CURTAIN_NUMBER,
    CONF_DEVICE_HW_KIND,
    CONF_DEVICE_ID,
    CONF_DEVICE_TYPE,
    CONF_DEVICES,
    CONF_DIMMABLE,
    CONF_DIMMER_CODES,
    CONF_DISCOVERED,
    CONF_GATEWAY_HOST,
    CONF_GATEWAY_PORT,
    CONF_HVAC_NUMBER,
    CONF_KEYPAD_LEDS,
    CONF_CONTACT_EMAIL,
    CONF_CONTACT_NAME,
    CONF_LICENSE_KEY,
    CONF_LICENSE_URL,
    LICENSE_PORTAL_HOST,
    LICENSE_PORTAL_URL,
    LICENSE_SERVER_URL,
    CONF_LOCAL_IP,
    CONF_MOTION_BYTE_INDEX,
    CONF_MOTION_UV_SWITCH,
    CONF_NAME,
    CONF_OPEN_CHANNEL,
    CONF_PRESET_MODES,
    CONF_RECALIBRATE_BEFORE_REPOSITION,
    CONF_RELAY_CHANNEL,
    CONF_RELAY_DEVICE,
    CONF_RELAY_SUBNET,
    CONF_RUNNING_TIME,
    CONF_SCAN_DURATION,
    CONF_SCAN_INTERVAL,
    CONF_SENSOR_KIND,
    CONF_SPLIT_CHANNELS,
    CONF_SUB_NUMBER,
    CONF_SUBNET_ID,
    CONF_TEMP_CHANNEL,
    CONF_TEMP_FAHRENHEIT,
    CONF_TEMP_OFFSET,
    CONF_TRAVEL_TIME,
    COVER_MODE_CURTAIN_MODULE,
    COVER_MODES,
    DEFAULT_TRAVEL_TIME,
    DEFAULT_PORT,
    DEFAULT_RUNNING_TIME,
    DEFAULT_MOTION_BYTE_INDEX,
    DEFAULT_MOTION_UV_SWITCH,
    DEFAULT_SCAN_DURATION,
    DEFAULT_MOTION_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TEMP_CHANNEL,
    DEFAULT_TEMP_OFFSET,
    DEVICE_HW_DLP,
    DEVICE_HW_GENERIC,
    DEVICE_HW_12IN1,
    DEVICE_HW_8IN1,
    DEVICE_HW_KINDS,
    DEVICE_HW_PANEL,
    DEVICE_HW_PIR,
    DEVICE_HW_SENSORS_IN_ONE,
    DEVICE_TYPE_BINARY_SENSOR,
    DEVICE_TYPE_CLIMATE,
    DEVICE_TYPE_COVER,
    DEVICE_TYPE_KEYPAD_BUTTON,
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_SENSOR,
    DEVICE_TYPE_SWITCH,
    DEVICE_TYPE_UNIVERSAL_SWITCH,
    DEVICE_TYPES,
    DOMAIN,
    HA_VIRTUAL_ADDRESS,
    HDL_DIMMER_TYPE_CODES,
    HDL_DRY_CONTACT_ZONES,
    HDL_AMBIGUOUS_COUNT_CODES,
    HDL_KEYPAD_TYPE_CODES,
    HDL_NO_ENTITY_ROLES,
    HDL_TYPE_CHANNEL_COUNT,
    HDL_TYPE_NAMES,
    HDL_TYPE_TO_DEVICE_TYPE,
    MAX_SCAN_DURATION,
    WIRELESS_RELAY_PANEL_CODES,
    MIN_SCAN_DURATION,
    PRESET_NONE,
    ROLE_KEYPAD,
    SENSOR_HAS_HUMIDITY,
    SENSOR_KIND_HUMIDITY,
    SENSOR_KIND_ILLUMINANCE,
    SENSOR_KIND_TEMPERATURE,
    SENSOR_KINDS,
    TARGET_TYPE_UNIVERSAL_SWITCH,
)
from .classify import (
    FAMILY_HW_KIND,
    FAMILY_ROLE,
    PANEL_FAMILIES,
    PANEL_FEATURE_AC,
    PANEL_FEATURE_FLOOR_HEATING,
    PANEL_FEATURE_TEMPERATURE,
    catalog_entry,
    catalog_family,
    catalog_name,
    channel_plan_from_functions,
    dry_contact_zones,
    function_summary,
    panel_features,
    panel_has_humidity,
    rcu_plan,
    role_from_functions,
)
from .keypad_led import (
    TARGET_TYPE_SINGLE_CHANNEL,
    format_keypad_leds,
    parse_keypad_leds,
)
from .licensing import (
    STATUS_LICENSED,
    STATUS_PENDING,
    STATUS_TRIAL,
    TRIAL_DAYS,
    async_get_manager,
)

_LOGGER = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _contact_schema(name: str = "", email: str = "") -> vol.Schema:
    """Name + email form shown before an online licence request."""
    return vol.Schema(
        {
            vol.Required(CONF_CONTACT_NAME, default=name): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            ),
            vol.Required(CONF_CONTACT_EMAIL, default=email): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.EMAIL)
            ),
        }
    )


def _validate_contact(user_input: dict[str, Any]) -> tuple[str, str, dict[str, str]]:
    """Return (name, email, errors) from the contact form."""
    name = (user_input.get(CONF_CONTACT_NAME) or "").strip()
    email = (user_input.get(CONF_CONTACT_EMAIL) or "").strip()
    errors: dict[str, str] = {}
    if not name:
        errors[CONF_CONTACT_NAME] = "contact_name_required"
    if not _EMAIL_RE.match(email):
        errors[CONF_CONTACT_EMAIL] = "invalid_email"
    return name, email, errors


# ---------------------------------------------------------------------------
# Schemas (built dynamically since they depend on selectors/defaults)
# ---------------------------------------------------------------------------
def _gateway_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Schema for the gateway-setup step."""
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_GATEWAY_HOST,
                default=defaults.get(CONF_GATEWAY_HOST, vol.UNDEFINED),
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            ),
            vol.Required(
                CONF_GATEWAY_PORT,
                default=defaults.get(CONF_GATEWAY_PORT, DEFAULT_PORT),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=65535, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_LOCAL_IP, default=defaults.get(CONF_LOCAL_IP, "")
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            ),
        }
    )


def _common_address_fields(defaults: dict[str, Any]) -> dict:
    """Subnet/device/name fields used by most device-add forms."""
    return {
        vol.Required(
            CONF_NAME, default=defaults.get(CONF_NAME, vol.UNDEFINED)
        ): str,
        vol.Required(
            CONF_SUBNET_ID, default=defaults.get(CONF_SUBNET_ID, 1)
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, max=255, mode=selector.NumberSelectorMode.BOX
            )
        ),
        vol.Required(
            CONF_DEVICE_ID, default=defaults.get(CONF_DEVICE_ID, 1)
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, max=255, mode=selector.NumberSelectorMode.BOX
            )
        ),
    }


def _keypad_led_field(defaults: dict[str, Any]) -> dict:
    """Keypad buttons whose LED should follow this relay/light."""
    return {
        vol.Optional(
            CONF_KEYPAD_LEDS, default=defaults.get(CONF_KEYPAD_LEDS, "") or ""
        ): selector.TextSelector(),
    }


def _light_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            **_common_address_fields(defaults),
            vol.Required(
                CONF_CHANNEL, default=defaults.get(CONF_CHANNEL, 1)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=255, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_DIMMABLE, default=defaults.get(CONF_DIMMABLE, True)
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_RUNNING_TIME,
                default=defaults.get(CONF_RUNNING_TIME, DEFAULT_RUNNING_TIME),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=3600, mode=selector.NumberSelectorMode.BOX
                )
            ),
            **_keypad_led_field(defaults),
        }
    )


def _switch_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            **_common_address_fields(defaults),
            vol.Required(
                CONF_CHANNEL, default=defaults.get(CONF_CHANNEL, 1)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=255, mode=selector.NumberSelectorMode.BOX
                )
            ),
            **_keypad_led_field(defaults),
        }
    )


def _universal_switch_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            **_common_address_fields(defaults),
            vol.Required(
                CONF_SUB_NUMBER, default=defaults.get(CONF_SUB_NUMBER, 1)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=255, mode=selector.NumberSelectorMode.BOX
                )
            ),
        }
    )


def _keypad_button_schema(defaults: dict[str, Any]) -> vol.Schema:
    """A spare keypad button sending a universal switch to Home Assistant."""
    return vol.Schema(
        {
            vol.Required(
                CONF_NAME, default=defaults.get(CONF_NAME, vol.UNDEFINED)
            ): str,
            vol.Required(
                CONF_SUB_NUMBER, default=defaults.get(CONF_SUB_NUMBER, 200)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=255, mode=selector.NumberSelectorMode.BOX
                )
            ),
            **_keypad_led_field(defaults),
        }
    )


def _sensor_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            **_common_address_fields(defaults),
            vol.Required(
                CONF_SENSOR_KIND,
                default=defaults.get(CONF_SENSOR_KIND, SENSOR_KINDS[0]),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=SENSOR_KINDS,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key=CONF_SENSOR_KIND,
                )
            ),
            vol.Optional(
                CONF_DEVICE_HW_KIND,
                default=defaults.get(CONF_DEVICE_HW_KIND, DEVICE_HW_GENERIC),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=DEVICE_HW_KINDS,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key=CONF_DEVICE_HW_KIND,
                )
            ),
            # Only used by the "panel" hw kind (MPTL/Granite Display), whose
            # temperature read is channel-addressed (0xE3E7). The onboard
            # sensor answers on channel 1 on every panel seen so far, and the
            # panel replies on all of its channels with that one reading, so
            # this exists mainly to pin which reply the entity accepts.
            vol.Optional(
                CONF_TEMP_CHANNEL,
                default=defaults.get(CONF_TEMP_CHANNEL, DEFAULT_TEMP_CHANNEL),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=32, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_TEMP_OFFSET,
                default=defaults.get(CONF_TEMP_OFFSET, DEFAULT_TEMP_OFFSET),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=-50, max=50, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_TEMP_FAHRENHEIT,
                default=defaults.get(CONF_TEMP_FAHRENHEIT, False),
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=defaults.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=3600, mode=selector.NumberSelectorMode.BOX
                )
            ),
        }
    )


def _binary_sensor_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            **_common_address_fields(defaults),
            vol.Required(
                CONF_BINARY_KIND,
                default=defaults.get(CONF_BINARY_KIND, BINARY_KINDS[0]),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=BINARY_KINDS,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key=CONF_BINARY_KIND,
                )
            ),
            vol.Optional(
                CONF_SUB_NUMBER, default=defaults.get(CONF_SUB_NUMBER, 0)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=255, mode=selector.NumberSelectorMode.BOX
                )
            ),
            # This field was missing entirely, which made binary_sensor.py's
            # hw-kind handling unreachable: every manually added motion sensor
            # was stuck on "generic" and therefore polled with
            # ReadSensorStatus (0x1645) no matter what the hardware was. A
            # CMS-PIR module ("pir") or a sensors-in-one module never answers
            # that opcode, so the entity could never leave its default state.
            vol.Optional(
                CONF_DEVICE_HW_KIND,
                default=defaults.get(CONF_DEVICE_HW_KIND, DEVICE_HW_GENERIC),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=DEVICE_HW_KINDS,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key=CONF_DEVICE_HW_KIND,
                )
            ),
            # Motion kind only. 201 (0xC9) is what HDL CMS multisensors use to
            # push a PIR trip in real time; 0 disables. Harmless on hardware
            # that doesn't push -- it simply never arrives.
            vol.Optional(
                CONF_MOTION_UV_SWITCH,
                default=defaults.get(
                    CONF_MOTION_UV_SWITCH, DEFAULT_MOTION_UV_SWITCH
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=255, mode=selector.NumberSelectorMode.BOX
                )
            ),
            # Motion kind only. Which byte of the 0x1630 broadcast is the
            # motion flag; -1 decodes none. See CONF_MOTION_BYTE_INDEX.
            vol.Optional(
                CONF_MOTION_BYTE_INDEX,
                default=defaults.get(
                    CONF_MOTION_BYTE_INDEX, DEFAULT_MOTION_BYTE_INDEX
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=-1, max=15, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=defaults.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=3600, mode=selector.NumberSelectorMode.BOX
                )
            ),
        }
    )



def _cover_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            **_common_address_fields(defaults),
            vol.Required(
                CONF_COVER_MODE,
                default=defaults.get(CONF_COVER_MODE, COVER_MODE_CURTAIN_MODULE),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=COVER_MODES,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key=CONF_COVER_MODE,
                )
            ),
            vol.Optional(
                CONF_CURTAIN_NUMBER,
                default=defaults.get(CONF_CURTAIN_NUMBER, 1),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=32, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_OPEN_CHANNEL,
                default=defaults.get(CONF_OPEN_CHANNEL, 1),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=64, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_CLOSE_CHANNEL,
                default=defaults.get(CONF_CLOSE_CHANNEL, 2),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=64, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_TRAVEL_TIME,
                default=defaults.get(CONF_TRAVEL_TIME, DEFAULT_TRAVEL_TIME),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=600, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_RECALIBRATE_BEFORE_REPOSITION,
                default=defaults.get(CONF_RECALIBRATE_BEFORE_REPOSITION, False),
            ): selector.BooleanSelector(),
        }
    )

def _climate_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            **_common_address_fields(defaults),
            vol.Required(
                CONF_CLIMATE_KIND,
                default=defaults.get(CONF_CLIMATE_KIND, CLIMATE_KIND_DLP),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=CLIMATE_KINDS,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key=CONF_CLIMATE_KIND,
                )
            ),
            # AC via IR module only (climate_kind = ac_ir): which of the IR
            # module's 4 live AC channels this entity targets. Subnet/Device
            # above are the IR module's own address, not the AC unit's.
            # Ignored for a DLP panel.
            # Touch-panel AC (climate_kind = ac_panel) uses the same field for
            # the panel's AC slot; Subnet/Device are then the panel's address.
            vol.Optional(
                CONF_HVAC_NUMBER, default=defaults.get(CONF_HVAC_NUMBER, 1)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=8, mode=selector.NumberSelectorMode.BOX
                )
            ),
            # Touch-panel AC only: the panel's own temperature-sensor channel
            # (0xE3E7), used for the room temperature. 0 = don't read one.
            vol.Optional(
                CONF_TEMP_CHANNEL, default=defaults.get(CONF_TEMP_CHANNEL, 1)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=8, mode=selector.NumberSelectorMode.BOX
                )
            ),
            # AC via IR module only. Which HVAC modes this specific unit
            # actually has -- e.g. no Heat on a cooling-only unit -- so the
            # entity doesn't offer modes that don't exist on the hardware.
            # Defaults to all of them (the original, unconfigurable
            # behaviour) when left as-is. Ignored for a DLP panel.
            vol.Optional(
                CONF_AC_HVAC_MODES,
                default=defaults.get(CONF_AC_HVAC_MODES, AC_HVAC_MODES),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=AC_HVAC_MODES,
                    mode=selector.SelectSelectorMode.LIST,
                    multiple=True,
                    translation_key="ac_hvac_mode",
                )
            ),
            # DLP panel only (climate_kind = dlp). Ignored for an AC via an
            # IR module -- preset selection isn't meaningful there (see
            # AirConditioner's docstring in pybuspro/devices/climate.py).
            vol.Optional(
                CONF_PRESET_MODES,
                default=defaults.get(CONF_PRESET_MODES, [PRESET_NONE]),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=CLIMATE_PRESETS,
                    mode=selector.SelectSelectorMode.LIST,
                    multiple=True,
                    translation_key="climate_preset",
                )
            ),
            vol.Optional(
                CONF_RELAY_SUBNET, default=defaults.get(CONF_RELAY_SUBNET, 0)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=255, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_RELAY_DEVICE, default=defaults.get(CONF_RELAY_DEVICE, 0)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=255, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_RELAY_CHANNEL, default=defaults.get(CONF_RELAY_CHANNEL, 0)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=255, mode=selector.NumberSelectorMode.BOX
                )
            ),
        }
    )


DEVICE_SCHEMA_BUILDERS = {
    DEVICE_TYPE_LIGHT: _light_schema,
    DEVICE_TYPE_SWITCH: _switch_schema,
    DEVICE_TYPE_UNIVERSAL_SWITCH: _universal_switch_schema,
    DEVICE_TYPE_SENSOR: _sensor_schema,
    DEVICE_TYPE_BINARY_SENSOR: _binary_sensor_schema,
    DEVICE_TYPE_COVER: _cover_schema,
    DEVICE_TYPE_CLIMATE: _climate_schema,
    DEVICE_TYPE_KEYPAD_BUTTON: _keypad_button_schema,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _normalize_device_input(
    device_type: str, user_input: dict[str, Any]
) -> dict[str, Any]:
    """Coerce selector values (which may be floats) into the right types."""
    out = dict(user_input)
    out[CONF_DEVICE_TYPE] = device_type
    int_fields = (
        CONF_SUBNET_ID,
        CONF_DEVICE_ID,
        CONF_CHANNEL,
        CONF_RUNNING_TIME,
        CONF_SCAN_INTERVAL,
        CONF_TEMP_OFFSET,
        CONF_TEMP_CHANNEL,
        CONF_SUB_NUMBER,
        CONF_CURTAIN_NUMBER,
        CONF_OPEN_CHANNEL,
        CONF_CLOSE_CHANNEL,
        CONF_TRAVEL_TIME,
        CONF_RELAY_SUBNET,
        CONF_RELAY_DEVICE,
        CONF_RELAY_CHANNEL,
        CONF_HVAC_NUMBER,
    )
    for field in int_fields:
        if field in out and out[field] is not None and out[field] != "":
            try:
                out[field] = int(out[field])
            except (TypeError, ValueError):
                pass
    if device_type == DEVICE_TYPE_KEYPAD_BUTTON:
        # Keypad buttons live at Home Assistant's own bus address.
        out[CONF_SUBNET_ID], out[CONF_DEVICE_ID] = HA_VIRTUAL_ADDRESS
    if CONF_KEYPAD_LEDS in out:
        # Store a clean "s.d:b, ..." string; anything unparseable is dropped.
        out[CONF_KEYPAD_LEDS] = format_keypad_leds(
            parse_keypad_leds(out[CONF_KEYPAD_LEDS])
        )
    return out


def _device_summary(device: dict[str, Any]) -> str:
    """Build a short human-readable label for the device list."""
    dtype = device.get(CONF_DEVICE_TYPE, "?")
    name = device.get(CONF_NAME, "?")
    sub = device.get(CONF_SUBNET_ID, "?")
    dev = device.get(CONF_DEVICE_ID, "?")
    ch = device.get(CONF_CHANNEL)
    if ch is None and dtype in (DEVICE_TYPE_UNIVERSAL_SWITCH, DEVICE_TYPE_KEYPAD_BUTTON):
        ch = device.get(CONF_SUB_NUMBER)
    if (
        ch is None
        and dtype == DEVICE_TYPE_CLIMATE
        and device.get(CONF_CLIMATE_KIND)
        in (CLIMATE_KIND_AC_IR, CLIMATE_KIND_AC_PANEL)
    ):
        # Several AC entities can share one IR module's address, only
        # distinguished by HVAC No. -- show it so the device list (and
        # edit/remove pickers) don't display identical-looking rows.
        ch = device.get(CONF_HVAC_NUMBER)
    addr = f"{sub}.{dev}" + (f".{ch}" if ch is not None else "")
    return f"{name} [{dtype} @ {addr}]"


def _validate_gateway_inputs(host: str, port: int) -> str | None:
    """Validate gateway inputs without touching the network.

    Returns an error key (matching strings.json) or None when inputs are OK.
    Note: we deliberately do NOT try to "ping" the gateway here -- HDL Buspro
    is connectionless UDP, so there is no handshake we could test, and a local
    socket bind only proves the local port is free (which is unrelated to
    whether the gateway is reachable).
    """
    import socket

    if not host:
        return "invalid_host"
    # Accept both hostnames and IPs.
    try:
        socket.getaddrinfo(host, None)
    except socket.gaierror:
        return "invalid_host"
    if not (1 <= port <= 65535):
        return "invalid_port"
    return None


# ---------------------------------------------------------------------------
# ConfigFlow
# ---------------------------------------------------------------------------
CHOICE_MANUAL = "manual"
CHOICE_RESCAN = "rescan"
CONF_GATEWAY_CHOICE = "gateway_choice"
GATEWAY_DISCOVERY_TIMEOUT = 3.0



def _disc_name(disc) -> str:
    """Entity name for a discovered device: its HDL remark, else its address.

    The remark is the name the installer typed into the HDL setup tool
    (read with 0x000E during the scan), so imported entities come out as
    "Lounge 7in1 Temperature" instead of "HDL 1.83 Temperature".
    """
    remark = getattr(disc, "remark", None)
    return remark if remark else f"HDL {disc.address}"


def _disc_channel_plan(disc, dtype: str) -> list[tuple[int, str]] | None:
    """Channel -> entity type list for a light/switch/cover import.

    Highest first:
      1. HDL_TYPE_CHANNEL_COUNT (field-confirmed), except for codes in
         HDL_AMBIGUOUS_COUNT_CODES, where one code covers several relay
         counts and the device's own report is better when it gives one;
      2. the device's 0xE549 self-description;
      3. HDL's fixed 22-channel RCU layout (relays + dimmers 18-21);
      4. the channel count the scan learned from a status reply;
      5. the HDL catalogue's function list for the type code.
    A plan whose channels are all one kind takes the import type (so a pin
    or a user dimmer code still decides light vs switch); a genuinely mixed
    module keeps its per-channel types.
    """
    if dtype not in (DEVICE_TYPE_LIGHT, DEVICE_TYPE_SWITCH, DEVICE_TYPE_COVER):
        return None
    code = disc.type_code
    want = "cover" if dtype == DEVICE_TYPE_COVER else "switch"

    def uniform(count: int) -> list[tuple[int, str]]:
        return [(ch, dtype) for ch in range(1, count + 1)]

    def normalise(plan):
        if not plan:
            return None
        if len({kind for _ch, kind in plan}) == 1:
            return [(ch, dtype) for ch, _kind in plan]
        return plan

    pinned = HDL_TYPE_CHANNEL_COUNT.get(code)
    if pinned and code not in HDL_AMBIGUOUS_COUNT_CODES:
        return uniform(pinned)
    own = normalise(
        channel_plan_from_functions(getattr(disc, "self_functions", None), want)
    )
    if own:
        return own
    if pinned:
        return uniform(pinned)
    if dtype != DEVICE_TYPE_COVER:
        fixed = normalise(rcu_plan(code))
        if fixed:
            return fixed
    if disc.channel_count:
        return uniform(disc.channel_count)
    entry = catalog_entry(code)
    if entry:
        return normalise(channel_plan_from_functions(entry[3], want))
    return None


def _disc_channel_count(disc, dtype: str = DEVICE_TYPE_SWITCH) -> int | None:
    """Number of channels that would be imported (labels, compatibility)."""
    plan = _disc_channel_plan(disc, dtype)
    return len(plan) if plan else None


def _keypad_links(results) -> tuple[dict, set]:
    """Relay channel -> keypad buttons, from the keypads' own programming.

    Returns ({(subnet, device, channel): {(kp_subnet, kp_device, button)}},
    {addresses of keypads whose buttons were read}).
    """
    links: dict[tuple[int, int, int], set[tuple[int, int, int]]] = {}
    read: set[tuple[int, int]] = set()
    for disc in results:
        targets = getattr(disc, "button_targets", None) or {}
        if targets:
            read.add((disc.subnet_id, disc.device_id))
        for button, by_no in targets.items():
            channels = {
                (subnet, device, channel)
                for ttype, subnet, device, channel, _level in by_no.values()
                if ttype == TARGET_TYPE_SINGLE_CHANNEL and 1 <= channel <= 255
            }
            # Only a button that drives exactly ONE channel owns that
            # channel's LED. A button driving several (an "all lights" /
            # combination button) would otherwise light up whenever any one
            # of its channels came on - seen on a 0x13C3 whose button 4 was
            # programmed for relays 1 and 2.
            if len(channels) != 1:
                continue
            links.setdefault(next(iter(channels)), set()).add(
                (disc.subnet_id, disc.device_id, int(button))
            )
    return links, read


def _apply_keypad_links(devices: list, results) -> int:
    """Write keypad LED links onto relay/light entries. Returns how many changed.

    Links found by reading a keypad replace earlier links to that same
    keypad (its programming is the truth); links to keypads not read in
    this scan, including ones typed by hand, are kept. A relay on a
    wireless panel whose buttons couldn't be read falls back to the
    same-numbered button. Entries are replaced, never mutated in place.
    """
    links, read = _keypad_links(results)
    wireless_unread = {
        (d.subnet_id, d.device_id)
        for d in results
        if d.type_code in WIRELESS_RELAY_PANEL_CODES
        and (d.subnet_id, d.device_id) not in read
    }
    changed = 0
    for index, dev in enumerate(devices):
        if dev.get(CONF_DEVICE_TYPE) not in (DEVICE_TYPE_SWITCH, DEVICE_TYPE_LIGHT):
            continue
        try:
            subnet = int(dev.get(CONF_SUBNET_ID))
            device = int(dev.get(CONF_DEVICE_ID))
            channel = int(dev.get(CONF_CHANNEL, 0))
        except (TypeError, ValueError):
            continue
        old_text = dev.get(CONF_KEYPAD_LEDS, "") or ""
        kept = {
            link
            for link in parse_keypad_leds(old_text)
            if (link[0], link[1]) not in read
        }
        new = kept | links.get((subnet, device, channel), set())
        if not new and (subnet, device) in wireless_unread and channel >= 1:
            new = {(subnet, device, channel)}
        new_text = format_keypad_leds(new)
        if new_text != old_text:
            devices[index] = {**dev, CONF_KEYPAD_LEDS: new_text}
            changed += 1
    return changed


_PANEL_FEATURE_LABELS = {
    PANEL_FEATURE_TEMPERATURE: "temperature sensor",
    PANEL_FEATURE_FLOOR_HEATING: "floor heating",
    PANEL_FEATURE_AC: "air conditioner",
}
_FEATURE_SEP = "|"


def _disc_panel_features(disc, role: str) -> list[str]:
    """Panel features offered as their own scan-list lines.

    Whatever the main line already imports is left out: a panel imported
    as climate (floor heating) doesn't repeat floor heating, and a panel
    imported as a temperature sensor doesn't repeat that.
    """
    family = catalog_family(disc.type_code)
    features = panel_features(
        disc.type_code,
        family,
        getattr(disc, "op_codes", set()),
        getattr(disc, "self_functions", None),
    )
    if role == DEVICE_TYPE_CLIMATE:
        features = [f for f in features if f != PANEL_FEATURE_FLOOR_HEATING]
        if family == "touch_panel":
            # Climate import of a touch panel already adds its sensors.
            features = [f for f in features if f != PANEL_FEATURE_TEMPERATURE]
    elif role == DEVICE_TYPE_SENSOR:
        features = [f for f in features if f != PANEL_FEATURE_TEMPERATURE]
    return features


def _panel_feature_configured(devices, disc, feature: str) -> bool:
    """Is this panel feature already in the config?"""
    for d in devices:
        if (d.get(CONF_SUBNET_ID), d.get(CONF_DEVICE_ID)) != (
            disc.subnet_id,
            disc.device_id,
        ):
            continue
        dtype, kind = d.get(CONF_DEVICE_TYPE), d.get(CONF_CLIMATE_KIND)
        if feature == PANEL_FEATURE_TEMPERATURE and dtype == DEVICE_TYPE_SENSOR and (
            d.get(CONF_SENSOR_KIND) == SENSOR_KIND_TEMPERATURE
        ):
            return True
        if feature == PANEL_FEATURE_FLOOR_HEATING and dtype == DEVICE_TYPE_CLIMATE and (
            kind in (None, CLIMATE_KIND_DLP)
        ):
            return True
        if feature == PANEL_FEATURE_AC and dtype == DEVICE_TYPE_CLIMATE and (
            kind == CLIMATE_KIND_AC_PANEL
        ):
            return True
    return False


def _import_keypad_buttons(devices: list, results) -> int:
    """Create keypad-button entries for buttons programmed to target HA.

    Any keypad button whose programming sends a universal switch to
    HA_VIRTUAL_ADDRESS gets a keypad-button entity (one per universal
    switch number), linked to that button for LED sync. Existing entries
    are only given the extra LED link. Returns how many entries changed.
    """
    found: dict[int, tuple[str, set]] = {}
    for disc in results:
        for button, by_no in (getattr(disc, "button_targets", None) or {}).items():
            for ttype, subnet, device, number, _status in by_no.values():
                if ttype != TARGET_TYPE_UNIVERSAL_SWITCH:
                    continue
                if (subnet, device) != tuple(HA_VIRTUAL_ADDRESS) or not 1 <= number <= 255:
                    continue
                name, links = found.setdefault(
                    number, (f"{_disc_name(disc)} button {button}", set())
                )
                links.add((disc.subnet_id, disc.device_id, int(button)))
    changed = 0
    for number, (name, links) in found.items():
        index = next(
            (
                i
                for i, d in enumerate(devices)
                if d.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_KEYPAD_BUTTON
                and int(d.get(CONF_SUB_NUMBER, 0) or 0) == number
            ),
            None,
        )
        if index is None:
            devices.append(
                {
                    "id": uuid.uuid4().hex,
                    CONF_DEVICE_TYPE: DEVICE_TYPE_KEYPAD_BUTTON,
                    CONF_NAME: name,
                    CONF_SUBNET_ID: HA_VIRTUAL_ADDRESS[0],
                    CONF_DEVICE_ID: HA_VIRTUAL_ADDRESS[1],
                    CONF_SUB_NUMBER: number,
                    CONF_KEYPAD_LEDS: format_keypad_leds(links),
                }
            )
            changed += 1
            continue
        old = devices[index]
        merged = format_keypad_leds(
            set(parse_keypad_leds(old.get(CONF_KEYPAD_LEDS))) | links
        )
        if merged != (old.get(CONF_KEYPAD_LEDS) or ""):
            devices[index] = {**old, CONF_KEYPAD_LEDS: merged}
            changed += 1
    return changed


def _disc_zones(disc) -> int | None:
    """Dry-contact zone count: pin > self-report > scan > catalogue."""
    entry = catalog_entry(disc.type_code)
    return (
        HDL_DRY_CONTACT_ZONES.get(disc.type_code)
        or dry_contact_zones(getattr(disc, "self_functions", None))
        or getattr(disc, "dry_contact_zones", None)
        or (dry_contact_zones(entry[3]) if entry else None)
    )


def _hw_kind_from_replies(disc) -> str:
    """Pick a sensor profile from the protocols the device answered.

    Used when the type code isn't in the built-in table -- the device told
    us during the scan which read it answers, and that IS the profile.
    """
    ops = getattr(disc, "op_codes", set()) or set()
    if "ReadSensorsInOneStatusResponse" in ops or "BroadcastSensorsInOneStatusResponse" in ops:
        return DEVICE_HW_SENSORS_IN_ONE
    if "ReadSensorStatusResponse" in ops:
        return DEVICE_HW_GENERIC
    if "ReadMotionSensorStatusResponse" in ops:
        return DEVICE_HW_PIR
    if "ReadTemperatureResponse" in ops:
        return DEVICE_HW_PANEL
    return DEVICE_HW_GENERIC


def _has_humidity(disc) -> bool:
    """Whether to create a humidity entity for a discovered multisensor.

    The sensors-in-one reply carries humidity at payload[4], with 0xFF
    meaning "no humidity element fitted". When the scan captured that reply
    it is the answer; otherwise fall back to the per-code table.
    """
    payload = (getattr(disc, "payloads", {}) or {}).get(
        "ReadSensorsInOneStatusResponse"
    )
    if payload and len(payload) > 4:
        return payload[4] != 0xFF
    return bool(SENSOR_HAS_HUMIDITY.get(disc.type_code, False))

class ARHDLConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for AR HDL BUSPRO."""

    VERSION = 2

    def __init__(self) -> None:
        """Initialize the flow."""
        self._discovered_gateways: list[Any] = []
        self._prefill: dict[str, Any] = {}
        self._license_seen = False
        self._pending_license_url = ""
        self._license_errors: dict[str, str] = {}

    async def _async_run_gateway_discovery(self) -> None:
        """Probe UDP/6000 for HDL gateways on the local network."""
        from .gateway_discovery import async_discover_gateways

        try:
            self._discovered_gateways = await async_discover_gateways(
                self.hass, timeout=GATEWAY_DISCOVERY_TIMEOUT
            )
        except Exception as err:  # noqa: BLE001 - discovery must never block setup
            _LOGGER.warning("HDL gateway auto-detection failed: %s", err)
            self._discovered_gateways = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """First step: auto-detect gateways on UDP/6000 and offer a pick list."""
        # Licensing comes before anything else on a fresh install, so the
        # installer sees the Server ID up front. Re-entry for "Scan again"
        # skips it -- the flag is only cleared on a brand new flow.
        if not self._license_seen:
            return await self.async_step_license()

        if user_input is not None:
            choice = user_input[CONF_GATEWAY_CHOICE]
            if choice == CHOICE_RESCAN:
                # Re-entering the step with no input triggers a fresh scan.
                self._discovered_gateways = []
                return await self.async_step_user()
            if choice == CHOICE_MANUAL:
                self._prefill = {}
                return await self.async_step_manual()
            # choice is a discovered gateway IP -> prefill the details form.
            self._prefill = {
                CONF_GATEWAY_HOST: choice,
                CONF_GATEWAY_PORT: DEFAULT_PORT,
            }
            return await self.async_step_manual()

        await self._async_run_gateway_discovery()
        if not self._discovered_gateways:
            # Nothing answered the broadcast probe (different L2 segment,
            # blocked broadcasts, ...) -- fall back to manual entry.
            return await self.async_step_manual()

        options = [
            selector.SelectOptionDict(value=gw.ip, label=gw.label)
            for gw in self._discovered_gateways
        ]
        options.append(
            selector.SelectOptionDict(value=CHOICE_RESCAN, label="Scan again")
        )
        options.append(
            selector.SelectOptionDict(
                value=CHOICE_MANUAL, label="Enter address manually"
            )
        )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_GATEWAY_CHOICE,
                        default=self._discovered_gateways[0].ip,
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
            description_placeholders={
                "count": str(len(self._discovered_gateways))
            },
        )

    async def async_step_license(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show this install's Server ID and accept a licence key.

        Leaving the key blank is allowed: the integration then runs in the
        %s-day demo window and the key can be entered later from the
        integration's Configure menu, without redoing any of the setup.
        """ % TRIAL_DAYS
        manager = await async_get_manager(self.hass)
        errors: dict[str, str] = self._license_errors
        self._license_errors = {}

        if user_input is not None:
            key = (user_input.get(CONF_LICENSE_KEY) or "").strip()
            url = (user_input.get(CONF_LICENSE_URL) or "").strip()

            # A pasted key always wins - it is the offline path, and an
            # installer who went to the trouble of fetching one by hand
            # should not have it overridden by a server round trip.
            if key:
                _state, reason = await manager.async_set_key(key)
                if reason is None:
                    if url:
                        await manager.async_set_activation_url(url)
                    self._license_seen = True
                    return await self.async_step_user()
                errors["base"] = reason
            elif url:
                await manager.async_set_activation_url(url)
                # A brand-new request: ask for a name and email FIRST. No
                # licence request reaches the server until they are given.
                if not manager.has_stored_key and not manager.has_contact:
                    self._pending_license_url = url
                    return await self.async_step_license_contact()
                # Always ask the server, even when a key is already stored
                # (e.g. the integration was removed and re-added): the server
                # decides whether that licence still exists, and a deleted or
                # revoked one clears the stored key. Only ask for a name and
                # email when it turns out to be a new request.
                _state, reason = await manager.async_activate(url)
                if reason is None:
                    self._license_seen = True
                    return await self.async_step_user()
                # Server unreachable or throttled, not a "no": an install
                # that still holds a valid key carries on offline.
                if manager.state.licensed and reason in (
                    "cannot_reach_server",
                    "checked_recently",
                    "no_activation_endpoint",
                ):
                    self._license_seen = True
                    return await self.async_step_user()
                if reason == "contact_required" or (
                    reason == "pending_approval" and not manager.has_contact
                ):
                    self._pending_license_url = url
                    return await self.async_step_license_contact()
                errors["base"] = reason
            else:
                # Neither supplied: carry on into the demo window.
                self._license_seen = True
                return await self.async_step_user()

        state = manager.state
        return self.async_show_form(
            step_id="license",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_LICENSE_URL,
                        default=manager.activation_url or LICENSE_SERVER_URL,
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.URL
                        )
                    ),
                    vol.Optional(CONF_LICENSE_KEY, default=""): selector.TextSelector(
                        selector.TextSelectorConfig(
                            multiline=True, type=selector.TextSelectorType.TEXT
                        )
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                "server_id": manager.server_id,
                "status": state.status,
                "trial_days": str(TRIAL_DAYS),
                "portal_url": LICENSE_PORTAL_URL,
                "portal_host": LICENSE_PORTAL_HOST,
                "server_url": LICENSE_SERVER_URL,
            },
        )

    async def async_step_license_contact(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect a name and email, then send the licence request."""
        manager = await async_get_manager(self.hass)
        errors: dict[str, str] = {}

        if user_input is not None:
            name, email, errors = _validate_contact(user_input)
            if not errors:
                await manager.async_set_contact(name, email)
                _state, reason = await manager.async_activate(
                    self._pending_license_url or None
                )
                if reason is None:
                    self._license_seen = True
                    return await self.async_step_user()
                # Back to the licence screen with the result shown there
                # (e.g. "sent for approval").
                self._license_errors = {"base": reason}
                return await self.async_step_license()

        return self.async_show_form(
            step_id="license_contact",
            data_schema=_contact_schema(
                (user_input or {}).get(CONF_CONTACT_NAME, manager.contact_name),
                (user_input or {}).get(CONF_CONTACT_EMAIL, manager.contact_email),
            ),
            errors=errors,
            description_placeholders={"server_id": manager.server_id},
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Enter/confirm the gateway details."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_GATEWAY_HOST].strip()
            port = int(user_input[CONF_GATEWAY_PORT])
            local_ip = user_input.get(CONF_LOCAL_IP, "").strip()

            # Validate the inputs only. HDL Buspro is connectionless UDP --
            # there is no handshake we can use to "ping" a gateway here, and a
            # local socket bind test only proves the local port is free (which
            # is unrelated to whether the gateway is reachable). The actual
            # bind happens in async_setup_entry, where ConfigEntryNotReady
            # gives us proper retry semantics.
            validation_error = _validate_gateway_inputs(host, port)
            if validation_error is not None:
                errors["base"] = validation_error
            else:
                # Prevent duplicates on the same gateway host:port pair.
                await self.async_set_unique_id(f"{host}:{port}")
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"AR HDL BUSPRO ({host})",
                    data={
                        CONF_GATEWAY_HOST: host,
                        CONF_GATEWAY_PORT: port,
                        CONF_LOCAL_IP: local_ip,
                    },
                    options={CONF_DEVICES: []},
                )

        return self.async_show_form(
            step_id="manual",
            data_schema=_gateway_schema(user_input or self._prefill),
            errors=errors,
        )

    async def async_step_import(
        self, import_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Import a legacy YAML/buspro config entry into ar_hdl_buspro."""
        return await self.async_step_manual(import_data)

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow handler."""
        return ARHDLOptionsFlow(entry)


# ---------------------------------------------------------------------------
# OptionsFlow - the heart of the no-YAML experience
# ---------------------------------------------------------------------------
class ARHDLOptionsFlow(OptionsFlow):
    """Options flow allowing users to manage everything from the UI."""

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize options flow."""
        # NOTE: do not assign self.config_entry directly; recent HA versions
        # provide it automatically via the OptionsFlow base class and warn on
        # explicit assignment. We keep a private reference instead.
        self._entry = entry
        self._editing_device_id: str | None = None
        self._adding_device_type: str | None = None
        self._scan_results: list[Any] = []
        self._scan_task: asyncio.Task | None = None
        self._scan_started: float = 0.0
        self._scan_duration: int = DEFAULT_SCAN_DURATION
        self._scan_error: str | None = None

    # ----- helpers ---------------------------------------------------------
    @property
    def devices(self) -> list[dict[str, Any]]:
        """Return the current list of configured devices."""
        return list(self._entry.options.get(CONF_DEVICES, []))

    def _save_devices(self, devices: list[dict[str, Any]]):
        """Persist a new device list as the options payload."""
        return self.async_create_entry(
            title="", data={**self._entry.options, CONF_DEVICES: devices}
        )

    def _purge_registry_for_device(self, device_cfg: dict[str, Any]) -> None:
        """Delete the registry rows belonging to a removed device entry.

        Removing a device from the options list stops the integration from
        creating its entities, but it does NOT remove their entity-registry
        rows: those belong to the config entry, which still exists. Home
        Assistant keeps them, shows them as unavailable, and -- critically --
        keeps their entity_ids reserved.

        So re-adding the same physical channel produced a duplicate. The
        unique_id is derived from the device entry's random UUID (see
        build_unique_id in entity.py), a re-scan mints a fresh UUID, and HA
        correctly treats that as a new entity -- which then has to take
        `switch.x_2` because the dead row still holds `switch.x`. The only
        way out was deleting and re-adding the whole config entry, which is
        what finally purged the rows.

        Removing them here frees the entity_id, so re-adding the channel gets
        its original name back and there is nothing stale to clean up.

        The device-registry entry is removed too, but only once no entities
        are left on it -- a module's other channels share that device.
        """
        entry_id = self._entry.entry_id
        prefix = f"{entry_id}_{device_cfg.get('id')}"

        ent_reg = er.async_get(self.hass)
        removed = 0
        for entity in list(
            er.async_entries_for_config_entry(ent_reg, entry_id)
        ):
            # Suffixed ids (sensor kinds, binary kinds, cover channels) all
            # share the "<entry>_<device uuid>" stem.
            if entity.unique_id == prefix or entity.unique_id.startswith(
                f"{prefix}_"
            ):
                ent_reg.async_remove(entity.entity_id)
                removed += 1

        if not removed:
            return

        subnet = device_cfg.get(CONF_SUBNET_ID, 0)
        device = device_cfg.get(CONF_DEVICE_ID, 0)
        dev_reg = dr.async_get(self.hass)
        hdl_device = dev_reg.async_get_device(
            identifiers={(DOMAIN, f"{entry_id}_{subnet}_{device}")}
        )
        if hdl_device is not None and not er.async_entries_for_device(
            ent_reg, hdl_device.id, include_disabled_entities=True
        ):
            dev_reg.async_update_device(
                hdl_device.id, remove_config_entry_id=entry_id
            )

        _LOGGER.debug(
            "Removed %s stale registry entrie(s) for device %s.%s",
            removed,
            subnet,
            device,
        )

    # ----- main menu -------------------------------------------------------
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the main options menu."""
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "license",
                "gateway",
                "detect_gateway",
                "scan_bus",
                "add_device",
                "edit_device",
                "remove_device",
            ],
        )

    # ----- licence ----------------------------------------------------------
    async def async_step_license(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """View licence status / Server ID and enter or replace a key."""
        manager = await async_get_manager(self.hass)
        errors: dict[str, str] = getattr(self, "_license_errors", {})
        self._license_errors: dict[str, str] = {}

        if user_input is not None:
            key = (user_input.get(CONF_LICENSE_KEY) or "").strip()
            url = (user_input.get(CONF_LICENSE_URL) or "").strip()

            if url != manager.activation_url:
                await manager.async_set_activation_url(url)

            if key:
                _state, reason = await manager.async_set_key(key)
                if reason is None:
                    # Saving options reloads the entry, which re-evaluates
                    # the licence and clears the repair issue.
                    return self.async_create_entry(
                        title="", data=dict(self._entry.options)
                    )
                errors["base"] = reason
            elif url:
                # No key typed but a URL present: treat Submit as
                # "activate / renew now". A brand-new request asks for a
                # name and email FIRST - nothing reaches the server until
                # they are given. An install that already holds a key is
                # checked with the server straight away.
                if not manager.has_stored_key and not manager.has_contact:
                    self._pending_license_url = url
                    return await self.async_step_license_contact()
                _state, reason = await manager.async_activate(url)
                if reason is None:
                    return self.async_create_entry(
                        title="", data=dict(self._entry.options)
                    )
                if reason == "contact_required" or (
                    reason == "pending_approval" and not manager.has_contact
                ):
                    self._pending_license_url = url
                    return await self.async_step_license_contact()
                errors["base"] = reason
            else:
                return await self.async_step_init()

        state = manager.state
        if state.status == STATUS_LICENSED:
            status_text = f"Licensed to {state.client or 'unknown'}" + (
                f", expires {state.expires_at[:10]}" if state.expires_at else ", perpetual"
            )
        elif state.status == STATUS_TRIAL:
            status_text = f"Demo - {state.trial_days_left} day(s) left"
        elif state.status == STATUS_PENDING:
            status_text = "Awaiting approval on the licence server"
        else:
            status_text = "Not licensed - entities are unavailable"

        if manager.last_contact:
            status_text += f" (last checked {manager.last_contact[:16].replace('T', ' ')})"

        return self.async_show_form(
            step_id="license",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_LICENSE_URL,
                        default=manager.activation_url or LICENSE_SERVER_URL,
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.URL
                        )
                    ),
                    vol.Optional(CONF_LICENSE_KEY, default=""): selector.TextSelector(
                        selector.TextSelectorConfig(
                            multiline=True, type=selector.TextSelectorType.TEXT
                        )
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                "server_id": manager.server_id,
                "status": status_text,
                "portal_url": LICENSE_PORTAL_URL,
                "portal_host": LICENSE_PORTAL_HOST,
                "server_url": LICENSE_SERVER_URL,
            },
        )

    async def async_step_license_contact(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect a name and email, then send the licence request."""
        manager = await async_get_manager(self.hass)
        errors: dict[str, str] = {}

        if user_input is not None:
            name, email, errors = _validate_contact(user_input)
            if not errors:
                await manager.async_set_contact(name, email)
                _state, reason = await manager.async_activate(
                    getattr(self, "_pending_license_url", "") or None
                )
                if reason is None:
                    return self.async_create_entry(
                        title="", data=dict(self._entry.options)
                    )
                self._license_errors = {"base": reason}
                return await self.async_step_license()

        return self.async_show_form(
            step_id="license_contact",
            data_schema=_contact_schema(
                (user_input or {}).get(CONF_CONTACT_NAME, manager.contact_name),
                (user_input or {}).get(CONF_CONTACT_EMAIL, manager.contact_email),
            ),
            errors=errors,
            description_placeholders={"server_id": manager.server_id},
        )

    # ----- gateway settings -------------------------------------------------
    async def async_step_gateway(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit gateway connection settings."""
        errors: dict[str, str] = {}

        if user_input is not None:
            new_data = {
                CONF_GATEWAY_HOST: user_input[CONF_GATEWAY_HOST].strip(),
                CONF_GATEWAY_PORT: int(user_input[CONF_GATEWAY_PORT]),
                CONF_LOCAL_IP: user_input.get(CONF_LOCAL_IP, "").strip(),
            }
            validation_error = _validate_gateway_inputs(
                new_data[CONF_GATEWAY_HOST], new_data[CONF_GATEWAY_PORT]
            )
            if validation_error is not None:
                errors["base"] = validation_error
                return self.async_show_form(
                    step_id="gateway",
                    data_schema=_gateway_schema({**self._entry.data, **user_input}),
                    errors=errors,
                )
            self.hass.config_entries.async_update_entry(
                self._entry,
                data={**self._entry.data, **new_data},
                title=f"AR HDL BUSPRO ({new_data[CONF_GATEWAY_HOST]})",
            )
            # Saving options (even empty) triggers the update listener -> reload.
            return self.async_create_entry(title="", data=dict(self._entry.options))

        return self.async_show_form(
            step_id="gateway",
            data_schema=_gateway_schema(self._entry.data),
            errors=errors,
        )

    # ----- gateway auto-detection ------------------------------------------
    async def async_step_detect_gateway(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Probe the network for HDL gateways and let the user pick one."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_GATEWAY_CHOICE]
            self.hass.config_entries.async_update_entry(
                self._entry,
                data={
                    **self._entry.data,
                    CONF_GATEWAY_HOST: host,
                    CONF_GATEWAY_PORT: DEFAULT_PORT,
                },
                title=f"AR HDL BUSPRO ({host})",
            )
            # Saving options triggers the update listener -> reload with the
            # new gateway (and a freshly resolved source-IP filter).
            return self.async_create_entry(title="", data=dict(self._entry.options))

        from .gateway_discovery import async_discover_gateways

        try:
            gateways = await async_discover_gateways(
                self.hass, timeout=GATEWAY_DISCOVERY_TIMEOUT
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("HDL gateway auto-detection failed: %s", err)
            gateways = []
        if not gateways:
            return self.async_abort(reason="no_gateways_found")

        current = self._entry.data.get(CONF_GATEWAY_HOST)
        options = [
            selector.SelectOptionDict(
                value=gw.ip,
                label=gw.label + ("  ✓ current" if gw.ip == current else ""),
            )
            for gw in gateways
        ]
        return self.async_show_form(
            step_id="detect_gateway",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_GATEWAY_CHOICE, default=gateways[0].ip
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
            errors=errors,
            description_placeholders={"count": str(len(gateways))},
        )

    # ----- bus scan: discover devices automatically ------------------------
    def _live_buspro(self):
        """Return the connected Buspro client for this entry, or None."""
        data = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)
        gateway = getattr(data, "gateway", None)
        if gateway is None or not gateway.available:
            return None
        return gateway.hdl

    async def async_step_scan_bus(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Scan the bus for devices, with a live countdown while it runs."""
        errors: dict[str, str] = {}
        if self._scan_error:
            errors["base"] = self._scan_error
            self._scan_error = None

        if self._scan_task is None and user_input is not None:
            buspro = self._live_buspro()
            if buspro is None:
                return self.async_abort(reason="gateway_unavailable")

            self._scan_duration = int(
                user_input.get(CONF_SCAN_DURATION, DEFAULT_SCAN_DURATION)
            )

            # Import lazily so a missing/edited library can't break the import
            # of the whole config flow module.
            from .discovery import SCAN_TIMEOUT_MARGIN, BusScanner

            scanner = BusScanner(buspro)
            self._scan_started = self.hass.loop.time()
            # The real scan runs independently in the background; the flow
            # only needs to know when it's done to move on.
            self._scan_task = self.hass.async_create_task(
                asyncio.wait_for(
                    scanner.scan(self._scan_duration),
                    timeout=self._scan_duration + SCAN_TIMEOUT_MARGIN,
                ),
                name=f"{DOMAIN} bus scan",
            )

        if self._scan_task is not None:
            if not self._scan_task.done():
                elapsed = self.hass.loop.time() - self._scan_started
                # A throwaway 1s task just paces the tick - it's not the scan
                # itself, so a slow render doesn't delay the scan and the scan
                # finishing early doesn't wait on this tick.
                tick = self.hass.async_create_task(asyncio.sleep(1))
                if elapsed < self._scan_duration:
                    # Phase 1: broadcasting and listening for the duration the
                    # user picked - this is what the countdown counts down.
                    seconds_left = max(0, round(self._scan_duration - elapsed))
                    return self.async_show_progress(
                        step_id="scan_bus",
                        progress_action="bus_scan",
                        description_placeholders={"seconds_left": str(seconds_left)},
                        progress_task=tick,
                    )
                # Phase 2: the listen window is over, but the scanner still
                # follows up directly with every device it found so far to
                # confirm channel counts (most relay/dimmer modules ignore a
                # broadcast channel-status read and only answer one addressed
                # to them). This isn't covered by "listen duration" - it's
                # normally a few seconds, hard-capped well beyond that on a
                # very large bus - so show a distinct message instead of
                # freezing the countdown at 0.
                return self.async_show_progress(
                    step_id="scan_bus",
                    progress_action="bus_scan_confirming",
                    progress_task=tick,
                )

            task, self._scan_task = self._scan_task, None
            try:
                self._scan_results = task.result()
            except (asyncio.TimeoutError, RuntimeError, OSError) as err:
                _LOGGER.warning("AR HDL BUSPRO bus scan failed: %s", err)
                self._scan_error = "scan_failed"
                return self.async_show_progress_done(next_step_id="scan_bus_retry")

            if not self._scan_results:
                return self.async_show_progress_done(
                    next_step_id="scan_bus_no_devices"
                )
            return self.async_show_progress_done(next_step_id="scan_results")

        return self.async_show_form(
            step_id="scan_bus",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_DURATION, default=DEFAULT_SCAN_DURATION
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=MIN_SCAN_DURATION,
                            max=MAX_SCAN_DURATION,
                            step=1,
                            unit_of_measurement="s",
                            mode=selector.NumberSelectorMode.SLIDER,
                        )
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_scan_bus_retry(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-show the scan form after a failed scan attempt."""
        return await self.async_step_scan_bus(user_input)

    async def async_step_scan_bus_no_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Abort after a scan that found nothing."""
        return self.async_abort(reason="no_devices_found")

    @staticmethod
    def _parse_dimmer_codes(text: str | None) -> set[str]:
        """Parse user-entered dimmer type codes into normalized '0xABCD' form.

        Accepts comma/space/semicolon separated tokens with or without the 0x
        prefix (e.g. '0x0602, 26D 0x120b'). Invalid tokens are ignored.
        """
        codes: set[str] = set()
        if not text:
            return codes
        for token in re.split(r"[\s,;]+", text.strip()):
            if not token:
                continue
            raw = token.lower().removeprefix("0x")
            if not raw or len(raw) > 4:
                continue
            try:
                value = int(raw, 16)
            except ValueError:
                continue
            codes.add(f"0x{value:04X}")
        return codes

    def _saved_dimmer_codes(self) -> set[str]:
        """Return dimmer type codes previously saved into the entry options."""
        saved = self._entry.options.get(CONF_DIMMER_CODES, [])
        if isinstance(saved, str):
            return self._parse_dimmer_codes(saved)
        return {str(c) for c in saved}

    def _infer_device_type(
        self, disc, dimmer_codes: set[str] | None = None
    ) -> str:
        """Decide an ar_hdl_buspro device_type from what the device replied with.

        A confirmed identification - a user-pinned dimmer code or a code in
        our built-in table - always wins first. Those heuristics further down
        infer the type from live bus chatter, which is usually right but can
        be fooled by multi-function hardware that also answers an unrelated
        probe: e.g. a relay+dimmer combo unit (HDL-MRCU) that also replies to
        a curtain-status read used to get imported entirely as a cover,
        discarding all of its relay/dimmer channels (see
        https://github.com/marsh4200/ar_hdl_buspro/issues/9, /10 and /11).
        Only once both of those come up empty do we fall back to the reply
        operate codes, and finally to a plain switch.
        """
        if dimmer_codes is None:
            dimmer_codes = self._saved_dimmer_codes()
        if (
            disc.type_code in HDL_DIMMER_TYPE_CODES
            or disc.type_code in dimmer_codes
        ):
            return DEVICE_TYPE_LIGHT
        # Gateways / logic modules: nothing to control, never imported.
        no_entity_role = HDL_NO_ENTITY_ROLES.get(disc.type_code)
        if no_entity_role is not None:
            return no_entity_role
        known = HDL_TYPE_TO_DEVICE_TYPE.get(disc.type_code)
        if known is not None:
            return known

        ops = disc.op_codes
        # Hard evidence the device has load channels: it answered a channel
        # status read with a count. Beats the catalogue, which only knows
        # what HDL sells under that code (and Smart-Bus reuses some codes).
        has_channels = bool(disc.channel_count) and (
            "ReadStatusOfChannelsResponse" in ops
        )

        # The device's own description of itself (0xE549), then HDL's
        # catalogue for the type code. Panels still defer to what the panel
        # answered: floor heating -> climate, onboard temperature -> sensor.
        family = catalog_family(disc.type_code)
        own_role = role_from_functions(getattr(disc, "self_functions", None))
        cat_role = FAMILY_ROLE.get(family)
        for role in (own_role, cat_role):
            if role is None:
                continue
            if role == ROLE_KEYPAD:
                if "ReadFloorHeatingStatusResponse" in ops:
                    return DEVICE_TYPE_CLIMATE
                if "ReadTemperatureResponse" in ops:
                    return DEVICE_TYPE_SENSOR
                if has_channels:
                    break
                return ROLE_KEYPAD
            if role in ("gateway", "logic", DEVICE_TYPE_SENSOR):
                if has_channels:
                    break
                if role == DEVICE_TYPE_SENSOR and family in PANEL_FAMILIES:
                    break
                if role == DEVICE_TYPE_SENSOR and "ReadFloorHeatingStatusResponse" in ops:
                    return DEVICE_TYPE_CLIMATE
                return role
            return role
        if (
            "ReadSensorStatusResponse" in ops
            or "ReadSensorsInOneStatusResponse" in ops
            or "ReadMotionSensorStatusResponse" in ops
        ):
            return DEVICE_TYPE_SENSOR
        if "ReadFloorHeatingStatusResponse" in ops:
            return DEVICE_TYPE_CLIMATE
        # A panel that answers the channel-addressed temperature read but
        # not floor heating: import its onboard temperature (the reference
        # integration's "panel" profile: temperature only).
        if "ReadTemperatureResponse" in ops:
            return DEVICE_TYPE_SENSOR
        if "ReadDryContactStatusResponse" in ops:
            return DEVICE_TYPE_BINARY_SENSOR
        # Curtain modules answer the curtain-status probe (0xE3E3) or echo
        # keypad open/close commands (0xE3E1). Either is definitive - check
        # before the generic channel branch, since some curtain modules also
        # answer ReadStatusOfChannels and would otherwise land on switch.
        if (
            "ReadStatusOfCurtainSwitchResponse" in ops
            or "CurtainSwitchControlResponse" in ops
        ):
            return DEVICE_TYPE_COVER
        # Keypads/panels: known type codes, or the traffic pattern (sends
        # control telegrams, never answers a channel read). Checked after the
        # sensor/climate ops above so DLPs still land on climate.
        if disc.type_code in HDL_KEYPAD_TYPE_CODES or getattr(
            disc, "looks_like_keypad", False
        ):
            return ROLE_KEYPAD
        if (
            "ReadStatusOfChannelsResponse" in ops
            or "SingleChannelControlResponse" in ops
        ):
            # Channel device with an unknown type code. Dimmer if the scan saw
            # an intermediate brightness level; otherwise a switch (the user
            # can flip it later).
            if getattr(disc, "dimmer_evidence", False):
                return DEVICE_TYPE_LIGHT
            return DEVICE_TYPE_SWITCH
        return DEVICE_TYPE_SWITCH

    def _discovery_label(self, disc, role: str, already: bool) -> str:
        """Build the checklist label for a discovered device."""
        parts = [disc.address, role]
        if getattr(disc, "remark", None):
            parts.append(f'"{disc.remark}"')
        if disc.type_code and disc.type_code != "0x0000":
            parts.append(disc.type_code)
        plan = _disc_channel_plan(disc, role)
        if plan:
            kinds = {kind for _ch, kind in plan}
            if len(kinds) > 1:
                relays = sum(1 for _ch, k in plan if k == DEVICE_TYPE_SWITCH)
                dimmers = sum(1 for _ch, k in plan if k == DEVICE_TYPE_LIGHT)
                parts.append(f"{relays} relay + {dimmers} dimmer")
            else:
                parts.append(f"{len(plan)}ch")
        elif role == DEVICE_TYPE_BINARY_SENSOR and _disc_zones(disc):
            parts.append(f"{_disc_zones(disc)} zones")
        # Our own table first (field-confirmed names), then HDL's catalogue,
        # then the vendored enum name.
        friendly = (
            HDL_TYPE_NAMES.get(disc.type_code)
            or catalog_name(disc.type_code)
            or (
                disc.type_name
                if disc.type_name and disc.type_name != "Unknown"
                else None
            )
        )
        own = function_summary(getattr(disc, "self_functions", None))
        if own:
            friendly = f"{friendly} [{own}]" if friendly else f"[{own}]"
        if friendly:
            parts.append(friendly)
        label = "  ".join(parts[:2]) + "  ·  " + " · ".join(parts[2:])
        if role == ROLE_KEYPAD:
            label += "  ·  buttons only, no entities"
        elif role in HDL_NO_ENTITY_ROLES.values():
            label += "  ·  no entities"
        if already:
            label += "  \u2713 in config"
        return label

    async def async_step_scan_results(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick which discovered devices to import."""
        results = self._scan_results
        if not results:
            return self.async_abort(reason="no_devices_found")

        # Mark addresses that already exist in the config so the user can tell
        # what is new (we still allow re-importing -- it never deletes anything).
        existing_addrs = {
            (d.get(CONF_SUBNET_ID), d.get(CONF_DEVICE_ID)) for d in self.devices
        }

        options: list[selector.SelectOptionDict] = []
        for disc in results:
            role = self._infer_device_type(disc)
            already = (disc.subnet_id, disc.device_id) in existing_addrs
            features = _disc_panel_features(disc, role)
            label = self._discovery_label(disc, role, already)
            if features and (role == ROLE_KEYPAD or role in HDL_NO_ENTITY_ROLES.values()):
                # The panel line itself imports nothing: point at its
                # feature lines instead of looking like a dead option.
                label = label.replace(
                    "buttons only, no entities", "buttons — tick its features below"
                ).replace("  \u2713 in config", "")
            options.append(
                selector.SelectOptionDict(value=disc.key, label=label)
            )
            name = _disc_name(disc)
            for feature in features:
                feature_label = (
                    f"{disc.address}  {_PANEL_FEATURE_LABELS[feature]}"
                    f"  ·  {name}"
                )
                if feature == PANEL_FEATURE_TEMPERATURE and panel_has_humidity(
                    disc.type_code
                ):
                    feature_label = feature_label.replace(
                        "temperature sensor", "temperature + humidity", 1
                    )
                if _panel_feature_configured(self.devices, disc, feature):
                    feature_label += "  \u2713 in config"
                options.append(
                    selector.SelectOptionDict(
                        value=f"{disc.key}{_FEATURE_SEP}{feature}",
                        label=feature_label,
                    )
                )

        if user_input is not None:
            chosen_keys = set(user_input.get(CONF_DISCOVERED, []))
            split = bool(user_input.get(CONF_SPLIT_CHANNELS, True))
            # Dimmer codes: whatever the user typed wins for this import and is
            # persisted for every future scan/import.
            dimmer_codes = self._parse_dimmer_codes(
                user_input.get(CONF_DIMMER_CODES, "")
            )
            by_key = {disc.key: disc for disc in results}
            devices = self.devices
            # Existing (subnet, device, channel) tuples, so a re-scan only fills
            # gaps instead of creating duplicates of what is already configured.
            existing_triples = {
                (
                    d.get(CONF_SUBNET_ID),
                    d.get(CONF_DEVICE_ID),
                    # Covers key on curtain number instead of channel; fall
                    # through so a re-scan recognises them and fills gaps
                    # instead of duplicating.
                    d.get(CONF_CHANNEL, d.get(CONF_CURTAIN_NUMBER)),
                )
                for d in devices
            }
            added = 0
            skipped_keypads = 0
            for key in sorted(chosen_keys):
                if _FEATURE_SEP in key:
                    disc_key, feature = key.split(_FEATURE_SEP, 1)
                    disc = by_key.get(disc_key)
                    if disc is not None:
                        added += self._import_panel_feature(disc, feature, devices)
                    continue
                disc = by_key.get(key)
                if disc is None:
                    continue
                dtype = self._infer_device_type(disc, dimmer_codes)
                if dtype == ROLE_KEYPAD or dtype in HDL_NO_ENTITY_ROLES.values():
                    # Keypads, gateways and logic modules have no controllable
                    # channels; importing one as a switch just creates a dead
                    # entity. Skip and count.
                    skipped_keypads += 1
                    continue
                if dtype == DEVICE_TYPE_SENSOR:
                    # A multi-sensor is one physical device but several HA
                    # entities. Import the full bundle (temperature + lux +
                    # motion) so the user isn't left hand-adding the rest.
                    added += self._import_sensor_bundle(disc, devices)
                    continue
                if dtype == DEVICE_TYPE_BINARY_SENSOR and _disc_zones(disc):
                    # Dry-contact input module: one entity per zone.
                    added += self._import_dry_contact_zones(disc, devices)
                    continue
                channel_device = dtype in (
                    DEVICE_TYPE_LIGHT,
                    DEVICE_TYPE_SWITCH,
                    DEVICE_TYPE_COVER,
                )
                plan = (
                    _disc_channel_plan(disc, dtype)
                    if split and channel_device
                    else None
                )
                entries = plan or [(None, dtype)]
                for channel, ctype in entries:
                    eff_channel = channel if channel is not None else (
                        1 if channel_device else None
                    )
                    if (
                        disc.subnet_id,
                        disc.device_id,
                        eff_channel,
                    ) in existing_triples:
                        continue
                    devices.append(
                        self._build_imported_device(disc, ctype, channel)
                    )
                    existing_triples.add(
                        (disc.subnet_id, disc.device_id, eff_channel)
                    )
                    added += 1
                if dtype == DEVICE_TYPE_CLIMATE and catalog_family(
                    disc.type_code
                ) == "touch_panel":
                    # Granite / 4" touch panels also carry onboard
                    # temperature (and humidity on most): import those too.
                    added += self._import_sensor_bundle(disc, devices)
            linked = _apply_keypad_links(devices, results)
            buttons = _import_keypad_buttons(devices, results)
            if buttons:
                _LOGGER.info(
                    "AR HDL BUSPRO import: %d keypad button entr(y/ies) added "
                    "or updated",
                    buttons,
                )
            if linked:
                _LOGGER.info(
                    "AR HDL BUSPRO import: keypad LED links updated on %d "
                    "relay/light entr(y/ies)",
                    linked,
                )
            if skipped_keypads:
                _LOGGER.info(
                    "AR HDL BUSPRO import: skipped %d keypad/gateway/logic "
                    "device(s) (no entities to create)",
                    skipped_keypads,
                )
            _LOGGER.info("AR HDL BUSPRO import: added %d new device entr(y/ies)", added)
            return self.async_create_entry(
                title="",
                data={
                    **self._entry.options,
                    CONF_DEVICES: devices,
                    CONF_DIMMER_CODES: sorted(dimmer_codes),
                },
            )

        new_count = sum(
            1
            for disc in results
            if (disc.subnet_id, disc.device_id) not in existing_addrs
        )
        return self.async_show_form(
            step_id="scan_results",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_DISCOVERED, default=[]
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            mode=selector.SelectSelectorMode.LIST,
                            multiple=True,
                        )
                    ),
                    vol.Optional(
                        CONF_SPLIT_CHANNELS, default=True
                    ): selector.BooleanSelector(),
                    vol.Optional(
                        CONF_DIMMER_CODES,
                        default=", ".join(sorted(self._saved_dimmer_codes())),
                    ): selector.TextSelector(),
                }
            ),
            description_placeholders={
                "found": str(len(results)),
                "new": str(new_count),
            },
        )

    def _import_panel_feature(
        self, disc, feature: str, devices: list[dict[str, Any]]
    ) -> int:
        """Import one feature of a wall panel. Returns entries added."""
        if _panel_feature_configured(devices, disc, feature):
            return 0
        name = _disc_name(disc)
        if feature == PANEL_FEATURE_TEMPERATURE:
            # DLP panels report their temperature with the floor-heating
            # status; the others answer the channel temperature read.
            entry = catalog_entry(disc.type_code)
            is_dlp = bool(entry) and "dlp" in f"{entry[0]} {entry[1]}".lower()
            hw = (
                DEVICE_HW_DLP
                if is_dlp and "ReadTemperatureResponse" not in disc.op_codes
                else DEVICE_HW_PANEL
            )
            return self._import_sensor_bundle(disc, devices, hw_override=hw)
        base = {
            "id": uuid.uuid4().hex,
            CONF_DEVICE_TYPE: DEVICE_TYPE_CLIMATE,
            CONF_SUBNET_ID: disc.subnet_id,
            CONF_DEVICE_ID: disc.device_id,
            CONF_PRESET_MODES: [PRESET_NONE],
            CONF_RELAY_SUBNET: 0,
            CONF_RELAY_DEVICE: 0,
            CONF_RELAY_CHANNEL: 0,
        }
        if feature == PANEL_FEATURE_FLOOR_HEATING:
            devices.append(
                {**base, CONF_NAME: f"{name} floor heating", CONF_CLIMATE_KIND: CLIMATE_KIND_DLP}
            )
            return 1
        if feature == PANEL_FEATURE_AC:
            devices.append(
                {
                    **base,
                    CONF_NAME: f"{name} AC",
                    CONF_CLIMATE_KIND: CLIMATE_KIND_AC_PANEL,
                    CONF_HVAC_NUMBER: 1,
                    CONF_TEMP_CHANNEL: 1,
                }
            )
            return 1
        return 0

    def _import_sensor_bundle(
        self, disc, devices: list[dict[str, Any]], hw_override: str | None = None
    ) -> int:
        """Append a full entity bundle for a discovered multi-sensor.

        One physical HDL sensor (12in1 / 8in1 / sensors-in-one) surfaces as
        several HA entities: temperature sensor, illuminance sensor, motion
        binary sensor and (sensors-in-one only) a humidity sensor. They
        share the (subnet, device) address so HA groups them under one
        device. Existing entries of the same kind are left alone so a
        re-scan never duplicates. Returns how many were added.
        """
        subnet, device = disc.subnet_id, disc.device_id
        name = _disc_name(disc)
        # Hardware kind from the type code; this drives payload decoding
        # (e.g. the 12in1's -20 temperature offset on auto broadcasts).
        hw_kind = {
            "0x0134": DEVICE_HW_12IN1,        # SB_CMS_12in1
            "0x0135": DEVICE_HW_8IN1,         # SB_CMS_8in1 (same +20 temp bias)
            "0x0150": DEVICE_HW_SENSORS_IN_ONE,  # HDL_MSP07M
            "0x0148": DEVICE_HW_SENSORS_IN_ONE,  # HDL-MSP07M.4C (confirmed on real hardware)
            # 0x0138 answers the sensors-in-one protocol (it is the family
            # that pushes the 0x1630 broadcast), so it polls 0x1604 rather
            # than the 8-in-1's 0x1645. Changeable per entity in the UI if a
            # given unit turns out to answer something else.
            "0x0138": DEVICE_HW_SENSORS_IN_ONE,
            "0x0890": DEVICE_HW_PANEL,        # HDL-MPTL4C.48 Granite Display
            "0x0141": DEVICE_HW_12IN1,        # HDL-MS12.2C 12-in-1 (was mis-mapped as a relay)
        }.get(disc.type_code)
        if hw_override:
            hw_kind = hw_override
        family = catalog_family(disc.type_code)
        if not hw_kind:
            replied = _hw_kind_from_replies(disc)
            hw_kind = (
                replied
                if replied != DEVICE_HW_GENERIC
                else FAMILY_HW_KIND.get(family)
                or (DEVICE_HW_PANEL if family in PANEL_FAMILIES else None)
                or replied
            )
        # Temperature-only hardware (HDL-MTS04 style): no lux, no motion.
        # DLP panels too: their temperature rides the floor-heating status.
        temp_only = family == "sensor_temp" or hw_kind == DEVICE_HW_DLP
        # Poll every 60s so readings arrive even when the sensor doesn't
        # broadcast on its own; broadcasts still update instantly.
        scan = 60

        def have(dev_type: str, kind_key: str, kind_val: str) -> bool:
            return any(
                d.get(CONF_SUBNET_ID) == subnet
                and d.get(CONF_DEVICE_ID) == device
                and d.get(CONF_DEVICE_TYPE) == dev_type
                and d.get(kind_key) == kind_val
                for d in devices
            )

        added = 0
        if hw_kind == DEVICE_HW_PANEL or temp_only:
            # Wall panel: onboard temperature only -- no lux, no PIR (the
            # reference integration's panel profile). Lux/motion entities
            # created here would sit unavailable forever.
            sensor_kinds = [SENSOR_KIND_TEMPERATURE]
            # Granite / 4" touch panels HDL reads humidity from (0xE440).
            if hw_kind == DEVICE_HW_PANEL and panel_has_humidity(disc.type_code):
                sensor_kinds.append(SENSOR_KIND_HUMIDITY)
        elif hw_kind == DEVICE_HW_PIR:
            # Motion-only module.
            sensor_kinds = []
        else:
            sensor_kinds = [SENSOR_KIND_TEMPERATURE, SENSOR_KIND_ILLUMINANCE]
        # Humidity comes from the type code, NOT from the hw kind. 0x0138
        # speaks the same sensors-in-one protocol as the MSP07M but has no
        # humidity element, so keying off the protocol created a permanently
        # unavailable humidity entity for it.
        if hw_kind not in (DEVICE_HW_PANEL, DEVICE_HW_PIR) and _has_humidity(disc):
            # Humidity is only ever carried by ReadSensorsInOneStatusResponse
            # (see SENSOR_KIND_HUMIDITY in const.py) -- the 12in1 hw kind
            # relies on BroadcastSensorStatusAutoResponse instead, which
            # doesn't include a humidity byte, so don't offer it there.
            sensor_kinds.append(SENSOR_KIND_HUMIDITY)
        for kind in sensor_kinds:
            if have(DEVICE_TYPE_SENSOR, CONF_SENSOR_KIND, kind):
                continue
            devices.append(
                {
                    "id": uuid.uuid4().hex,
                    CONF_DEVICE_TYPE: DEVICE_TYPE_SENSOR,
                    CONF_NAME: name,
                    CONF_SUBNET_ID: subnet,
                    CONF_DEVICE_ID: device,
                    CONF_SENSOR_KIND: kind,
                    CONF_DEVICE_HW_KIND: hw_kind,
                    CONF_TEMP_OFFSET: DEFAULT_TEMP_OFFSET,
                    CONF_SCAN_INTERVAL: scan,
                }
            )
            added += 1
        if hw_kind != DEVICE_HW_PANEL and not temp_only and not have(
            DEVICE_TYPE_BINARY_SENSOR, CONF_BINARY_KIND, BINARY_KIND_MOTION
        ):
            devices.append(
                {
                    "id": uuid.uuid4().hex,
                    CONF_DEVICE_TYPE: DEVICE_TYPE_BINARY_SENSOR,
                    CONF_NAME: name,
                    CONF_SUBNET_ID: subnet,
                    CONF_DEVICE_ID: device,
                    CONF_BINARY_KIND: BINARY_KIND_MOTION,
                    CONF_SUB_NUMBER: 0,
                    # Same protocol profile as the sibling sensor entities.
                    # Without it the motion entity fell back to "generic"
                    # and polled 0x1645, which a 7-in-1 (sensors-in-one)
                    # never answers -- motion then only moved when a sibling
                    # entity's 60 s poll happened to come back.
                    CONF_DEVICE_HW_KIND: hw_kind,
                    CONF_SCAN_INTERVAL: DEFAULT_MOTION_SCAN_INTERVAL,
                }
            )
            added += 1
        return added

    @staticmethod
    def _import_dry_contact_zones(
        disc, devices: list[dict[str, Any]]
    ) -> int:
        """Append one dry_contact binary sensor per zone of a dry-contact module.

        Zones already configured for this (subnet, device) are skipped so a
        re-scan only fills gaps. Returns how many were added.
        """
        subnet, device = disc.subnet_id, disc.device_id
        existing = {
            int(d.get(CONF_SUB_NUMBER, 0))
            for d in devices
            if d.get(CONF_SUBNET_ID) == subnet
            and d.get(CONF_DEVICE_ID) == device
            and d.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_BINARY_SENSOR
            and d.get(CONF_BINARY_KIND) == BINARY_KIND_DRY_CONTACT
        }
        added = 0
        zones = int(_disc_zones(disc) or 1)
        for zone in range(1, zones + 1):
            if zone in existing:
                continue
            devices.append(
                {
                    "id": uuid.uuid4().hex,
                    CONF_DEVICE_TYPE: DEVICE_TYPE_BINARY_SENSOR,
                    CONF_NAME: f"{_disc_name(disc)} zone {zone}",
                    CONF_SUBNET_ID: subnet,
                    CONF_DEVICE_ID: device,
                    CONF_BINARY_KIND: BINARY_KIND_DRY_CONTACT,
                    CONF_SUB_NUMBER: zone,
                    CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                }
            )
            added += 1
        return added

    @staticmethod
    def _build_imported_device(
        disc, dtype: str, channel: int | None = None
    ) -> dict[str, Any]:
        """Build a minimal valid device dict for an imported discovery.

        Required addressing is filled from the scan; type-specific details get
        safe defaults the user can refine in the edit form afterwards. When a
        channel is supplied, the name is suffixed so per-channel entries are
        distinguishable.
        """
        name = _disc_name(disc)
        if channel is not None:
            name = f"{name} ch{channel}"
        base: dict[str, Any] = {
            "id": uuid.uuid4().hex,
            CONF_DEVICE_TYPE: dtype,
            CONF_NAME: name,
            CONF_SUBNET_ID: disc.subnet_id,
            CONF_DEVICE_ID: disc.device_id,
        }
        if dtype == DEVICE_TYPE_LIGHT:
            base.update(
                {
                    CONF_CHANNEL: channel or 1,
                    CONF_DIMMABLE: True,
                    CONF_RUNNING_TIME: DEFAULT_RUNNING_TIME,
                }
            )
        elif dtype == DEVICE_TYPE_SWITCH:
            base[CONF_CHANNEL] = channel or 1
        elif dtype == DEVICE_TYPE_UNIVERSAL_SWITCH:
            # Universal-switch numbers aren't split per-channel on import (the
            # broadcast probe only ever reads switch number 1, so the scan has
            # no way to learn how many the module actually exposes) - import
            # one entity pointed at switch 1 and let the user add the rest by
            # hand with the correct sub_number, same as sensor/binary_sensor.
            base[CONF_SUB_NUMBER] = channel or 1
        elif dtype == DEVICE_TYPE_COVER:
            # Discovered curtain hardware is always a real curtain module
            # (CurtainSwitchControl open/close/stop), never a relay pair -
            # relay-pair curtains are a wiring convention the user sets up by
            # hand. The per-channel split maps onto curtain numbers.
            base.update(
                {
                    CONF_COVER_MODE: COVER_MODE_CURTAIN_MODULE,
                    CONF_CURTAIN_NUMBER: channel or 1,
                }
            )
        elif dtype == DEVICE_TYPE_SENSOR:
            base.update(
                {
                    CONF_SENSOR_KIND: SENSOR_KINDS[0],
                    CONF_DEVICE_HW_KIND: DEVICE_HW_GENERIC,
                    CONF_TEMP_OFFSET: DEFAULT_TEMP_OFFSET,
                    CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                }
            )
        elif dtype == DEVICE_TYPE_BINARY_SENSOR:
            base.update(
                {
                    CONF_BINARY_KIND: BINARY_KINDS[0],
                    CONF_SUB_NUMBER: 0,
                    CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                }
            )
        elif dtype == DEVICE_TYPE_CLIMATE:
            # Discovery only ever identifies DLP panels (the IR-module AC
            # path has no bus-scan signature of its own -- add it by hand
            # via "Add a device" and pick climate_kind = ac_ir).
            base.update(
                {
                    CONF_CLIMATE_KIND: CLIMATE_KIND_DLP,
                    CONF_PRESET_MODES: [PRESET_NONE],
                    CONF_RELAY_SUBNET: 0,
                    CONF_RELAY_DEVICE: 0,
                    CONF_RELAY_CHANNEL: 0,
                }
            )
        return base

    # ----- add device: pick a type, then show the matching form ------------
    async def async_step_add_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick which kind of device to add."""
        if user_input is not None:
            self._adding_device_type = user_input[CONF_DEVICE_TYPE]
            return await self.async_step_add_device_form()

        return self.async_show_form(
            step_id="add_device",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_DEVICE_TYPE, default=DEVICE_TYPE_LIGHT
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=DEVICE_TYPES,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                            translation_key=CONF_DEVICE_TYPE,
                        )
                    )
                }
            ),
        )

    async def async_step_add_device_form(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the type-specific add form."""
        dtype = self._adding_device_type or DEVICE_TYPE_LIGHT
        schema_builder = DEVICE_SCHEMA_BUILDERS[dtype]

        if user_input is not None:
            device = _normalize_device_input(dtype, user_input)
            device["id"] = uuid.uuid4().hex
            devices = self.devices
            devices.append(device)
            return self._save_devices(devices)

        return self.async_show_form(
            step_id="add_device_form",
            data_schema=schema_builder({}),
            description_placeholders={"device_type": dtype},
        )

    # ----- edit device: pick one, then show the matching form --------------
    async def async_step_edit_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick an existing device to edit."""
        devices = self.devices
        if not devices:
            return self.async_abort(reason="no_devices")

        options = [
            selector.SelectOptionDict(value=d["id"], label=_device_summary(d))
            for d in devices
        ]

        if user_input is not None:
            self._editing_device_id = user_input["device_id_choice"]
            return await self.async_step_edit_device_form()

        return self.async_show_form(
            step_id="edit_device",
            data_schema=vol.Schema(
                {
                    vol.Required("device_id_choice"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_edit_device_form(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the type-specific edit form."""
        devices = self.devices
        target = next(
            (d for d in devices if d["id"] == self._editing_device_id), None
        )
        if target is None:
            return self.async_abort(reason="device_not_found")

        dtype = target[CONF_DEVICE_TYPE]
        schema_builder = DEVICE_SCHEMA_BUILDERS[dtype]

        if user_input is not None:
            updated = _normalize_device_input(dtype, user_input)
            updated["id"] = target["id"]
            new_devices = [
                updated if d["id"] == target["id"] else d for d in devices
            ]
            return self._save_devices(new_devices)

        return self.async_show_form(
            step_id="edit_device_form",
            data_schema=schema_builder(target),
            description_placeholders={
                "device_type": dtype,
                "device_name": target.get(CONF_NAME, ""),
            },
        )

    # ----- remove devices (tick any number, remove in one go) --------------
    async def async_step_remove_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove one or more configured devices."""
        devices = self.devices
        if not devices:
            return self.async_abort(reason="no_devices")

        options = sorted(
            (
                selector.SelectOptionDict(value=d["id"], label=_device_summary(d))
                for d in devices
            ),
            key=lambda o: o["label"].lower(),
        )

        errors: dict[str, str] = {}
        if user_input is not None:
            chosen = set(user_input.get("device_ids") or [])
            if not chosen:
                errors["base"] = "no_devices_selected"
            else:
                for target in devices:
                    if target["id"] in chosen:
                        # Free the entity_ids before the entry reloads, so
                        # re-adding a channel later reuses them instead of
                        # colliding.
                        self._purge_registry_for_device(target)
                # One save = one reload, however many were ticked.
                return self._save_devices(
                    [d for d in devices if d["id"] not in chosen]
                )

        return self.async_show_form(
            step_id="remove_device",
            data_schema=vol.Schema(
                {
                    vol.Required("device_ids", default=[]): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            multiple=True,
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
            errors=errors,
            description_placeholders={"count": str(len(devices))},
        )
