"""Constants for the AR HDL BUSPRO integration."""
from __future__ import annotations

from typing import Final

DOMAIN: Final = "ar_hdl_buspro"
INTEGRATION_NAME: Final = "AR HDL BUSPRO"
MANUFACTURER: Final = "AR SMART HOME AUTOMATION"

# Legacy domain (used for migration from the old `buspro` integration)
LEGACY_DOMAIN: Final = "buspro"

# Platforms supported by this integration
PLATFORMS: Final = [
    "binary_sensor",
    "climate",
    "cover",
    "light",
    "sensor",
    "switch",
]

# Configuration keys (gateway / entry data)
CONF_GATEWAY_HOST: Final = "gateway_host"
CONF_GATEWAY_PORT: Final = "gateway_port"
CONF_LOCAL_IP: Final = "local_ip"

# Devices stored inside the entry's options
CONF_DEVICES: Final = "devices"
CONF_DEVICE_TYPE: Final = "device_type"
CONF_SUBNET_ID: Final = "subnet_id"
CONF_DEVICE_ID: Final = "device_id"
CONF_CHANNEL: Final = "channel"
CONF_NAME: Final = "name"
CONF_RUNNING_TIME: Final = "running_time"
CONF_DIMMABLE: Final = "dimmable"
CONF_SENSOR_KIND: Final = "sensor_kind"
CONF_BINARY_KIND: Final = "binary_kind"
CONF_SUB_NUMBER: Final = "sub_number"          # universal switch / single-channel / dry-contact number
CONF_TEMP_OFFSET: Final = "temperature_offset"
# The sensor hardware reports Fahrenheit on the bus; convert to Celsius.
CONF_TEMP_FAHRENHEIT: Final = "temperature_fahrenheit"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_DEVICE_HW_KIND: Final = "device_hw_kind"  # e.g. "dlp", "12in1", "sensors_in_one"
CONF_PRESET_MODES: Final = "preset_modes"
CONF_COVER_MODE: Final = "cover_mode"          # "curtain_module" or "relay_pair"
CONF_CURTAIN_NUMBER: Final = "curtain_number"  # curtain no. on an HDL curtain module
CONF_OPEN_CHANNEL: Final = "open_channel"      # relay channel driving "open"
CONF_CLOSE_CHANNEL: Final = "close_channel"    # relay channel driving "close"
CONF_TRAVEL_TIME: Final = "travel_time"        # full open<->close travel, seconds
CONF_RELAY_SUBNET: Final = "relay_subnet"
CONF_RELAY_DEVICE: Final = "relay_device"
CONF_RELAY_CHANNEL: Final = "relay_channel"

# Device types (used to discriminate entries in the device list)
DEVICE_TYPE_LIGHT: Final = "light"
DEVICE_TYPE_SWITCH: Final = "switch"
DEVICE_TYPE_SENSOR: Final = "sensor"
DEVICE_TYPE_BINARY_SENSOR: Final = "binary_sensor"
DEVICE_TYPE_CLIMATE: Final = "climate"
DEVICE_TYPE_COVER: Final = "cover"
# A controllable HDL "universal switch" - a virtual on/off flag used to
# trigger scenes/logic on the bus (as opposed to DEVICE_TYPE_SWITCH, which is
# a physical relay channel). The pybuspro library has always supported this
# (see pybuspro/devices/universal_switch.py and the set_universal_switch
# service); this device type is what surfaces it as a normal toggleable
# switch entity, addable from the UI like any other device.
DEVICE_TYPE_UNIVERSAL_SWITCH: Final = "universal_switch"

DEVICE_TYPES: Final = [
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_SWITCH,
    DEVICE_TYPE_UNIVERSAL_SWITCH,
    DEVICE_TYPE_COVER,
    DEVICE_TYPE_SENSOR,
    DEVICE_TYPE_BINARY_SENSOR,
    DEVICE_TYPE_CLIMATE,
]

# Cover modes
COVER_MODE_CURTAIN_MODULE: Final = "curtain_module"
COVER_MODE_RELAY_PAIR: Final = "relay_pair"
COVER_MODES: Final = [COVER_MODE_CURTAIN_MODULE, COVER_MODE_RELAY_PAIR]
DEFAULT_TRAVEL_TIME: Final = 30

# Sensor kinds (sensor platform)
SENSOR_KIND_TEMPERATURE: Final = "temperature"
SENSOR_KIND_ILLUMINANCE: Final = "illuminance"

SENSOR_KINDS: Final = [
    SENSOR_KIND_TEMPERATURE,
    SENSOR_KIND_ILLUMINANCE,
]

# Binary sensor kinds
BINARY_KIND_MOTION: Final = "motion"
BINARY_KIND_DRY_CONTACT_1: Final = "dry_contact_1"
BINARY_KIND_DRY_CONTACT_2: Final = "dry_contact_2"
BINARY_KIND_UNIVERSAL_SWITCH: Final = "universal_switch"
BINARY_KIND_SINGLE_CHANNEL: Final = "single_channel"
BINARY_KIND_DRY_CONTACT: Final = "dry_contact"

BINARY_KINDS: Final = [
    BINARY_KIND_MOTION,
    BINARY_KIND_DRY_CONTACT_1,
    BINARY_KIND_DRY_CONTACT_2,
    BINARY_KIND_UNIVERSAL_SWITCH,
    BINARY_KIND_SINGLE_CHANNEL,
    BINARY_KIND_DRY_CONTACT,
]

# Hardware variants relevant for sensor decoding
DEVICE_HW_DLP: Final = "dlp"
DEVICE_HW_12IN1: Final = "12in1"
DEVICE_HW_SENSORS_IN_ONE: Final = "sensors_in_one"
DEVICE_HW_DRY_CONTACT: Final = "dry_contact"
DEVICE_HW_GENERIC: Final = "generic"

DEVICE_HW_KINDS: Final = [
    DEVICE_HW_GENERIC,
    DEVICE_HW_DLP,
    DEVICE_HW_12IN1,
    DEVICE_HW_SENSORS_IN_ONE,
    DEVICE_HW_DRY_CONTACT,
]

# Climate presets
PRESET_NONE: Final = "none"
PRESET_AWAY: Final = "away"
PRESET_HOME: Final = "home"
PRESET_SLEEP: Final = "sleep"

CLIMATE_PRESETS: Final = [PRESET_NONE, PRESET_AWAY, PRESET_HOME, PRESET_SLEEP]

# Defaults
DEFAULT_PORT: Final = 6000
DEFAULT_RUNNING_TIME: Final = 0
DEFAULT_TEMP_OFFSET: Final = 0
DEFAULT_SCAN_INTERVAL: Final = 0

# Bus discovery
CONF_SCAN_DURATION: Final = "scan_duration"
CONF_DISCOVERED: Final = "discovered"
CONF_SPLIT_CHANNELS: Final = "split_channels"
# User-pinned dimmer type codes, persisted in the entry options. Codes entered
# on the scan-results screen are remembered and always classify as dimmable
# lights on future scans - no code change needed for new dimmer hardware.
CONF_DIMMER_CODES: Final = "dimmer_type_codes"
DEFAULT_SCAN_DURATION: Final = 15  # seconds to listen on the bus
MIN_SCAN_DURATION: Final = 3
MAX_SCAN_DURATION: Final = 60

# Maps a raw HDL device-type code (hex string, as surfaced by the scanner) to
# the ar_hdl_buspro device_type used when importing a discovered device. A code
# listed here is a confirmed identification and always wins over the discovery
# heuristics that guess a type from live bus chatter (see _infer_device_type in
# config_flow.py) - those heuristics can be fooled by multi-function hardware
# that also answers an unrelated probe (e.g. a relay/dimmer combo unit that
# also replies to a curtain-status read). Anything not listed here, and not
# resolved by a heuristic either, falls back to a switch on channel 1, which
# the user can edit. Codes mirror the DeviceType enum in
# pybuspro/helpers/enums.py; extend this table as new modules are identified
# on real installations.
HDL_TYPE_TO_DEVICE_TYPE: Final = {
    "0x0011": DEVICE_TYPE_CLIMATE,        # SB_DN_6B0_10v heating relay
    "0x0086": DEVICE_TYPE_CLIMATE,        # SB_DLP2 panel
    "0x0095": DEVICE_TYPE_CLIMATE,        # SB_DLP panel
    "0x009C": DEVICE_TYPE_CLIMATE,        # SB_DLP v2 panel
    "0x0260": DEVICE_TYPE_LIGHT,          # SB_DN_DT0601 6ch dimmer
    "0x026D": DEVICE_TYPE_LIGHT,          # HDL_MDT0601 6ch dimmer (newer)
    "0x01AC": DEVICE_TYPE_SWITCH,         # SB_DN_R0816 relay
    "0x0077": DEVICE_TYPE_BINARY_SENSOR,  # SB_DRY_4Z dry contact
    "0x0134": DEVICE_TYPE_SENSOR,         # SB_CMS_12in1 sensor
    "0x0135": DEVICE_TYPE_SENSOR,         # SB_CMS_8in1 sensor
    "0x0150": DEVICE_TYPE_SENSOR,         # HDL_MSP07M sensors-in-one
    # ARSmartHome site relay modules (identified on a live bus).
    "0x120B": DEVICE_TYPE_SWITCH,         # relay module
    "0x0141": DEVICE_TYPE_SWITCH,         # relay module
    "0x0457": DEVICE_TYPE_SWITCH,         # relay module
    "0x01C1": DEVICE_TYPE_SWITCH,         # relay module
    "0x084D": DEVICE_TYPE_SWITCH,         # relay module
    "0x239C": DEVICE_TYPE_SWITCH,         # relay module
    "0x01C2": DEVICE_TYPE_SWITCH,         # 16ch relay module
    "0x01BD": DEVICE_TYPE_SWITCH,         # 8ch relay module
    "0x01BF": DEVICE_TYPE_SWITCH,         # 4ch relay module
    "0x1209": DEVICE_TYPE_SWITCH,         # relay/mix module
    "0x238C": DEVICE_TYPE_SWITCH,         # relay/mix module
    "0x0269": DEVICE_TYPE_LIGHT,          # 6ch dimmer module
    "0x25E5": DEVICE_TYPE_COVER,          # curtain module (ARSmartHome site)
    "0x25E8": DEVICE_TYPE_COVER,          # curtain module (ARSmartHome site)
    "0x02C9": DEVICE_TYPE_COVER,             # HDL-MW02.431 2ch curtain controller
    "0x0516": DEVICE_TYPE_UNIVERSAL_SWITCH,  # HDL-MIRC04.40 IR module, other half of the pair (issue #9)
    "0x0517": DEVICE_TYPE_UNIVERSAL_SWITCH,  # HDL-MIRC04.40 IR module (4x universal switch)
    "0x0166": DEVICE_TYPE_BINARY_SENSOR,     # HDL-MS24.232 (SB-DN-DRY-24Z) 24-zone dry contact module (issue #11)
    "0x0DCE": DEVICE_TYPE_SWITCH,            # HDL-MRCU home control unit, 18 relay + 4 dimmer ch (issue #10) -
                                              # imports as switch channels; re-tag the 4 dimmer channels to
                                              # "light" afterwards via the edit-device screen.
    "0x0890": DEVICE_TYPE_CLIMATE,            # HDL-MPTL4C.48 Granite Display touch panel (issue #13) - has a
                                               # built-in temperature/humidity sensor and drives HVAC/floor
                                               # heating, like the DLP panels above; was falling through to
                                               # switch since it had no entry here.
}

# Friendly names for type codes that aren't in the vendored DeviceType enum, so
# discovered devices read sensibly in the UI instead of "Unknown".
HDL_TYPE_NAMES: Final = {
    "0x120B": "Relay module",
    "0x0141": "Relay module",
    "0x0457": "Relay module",
    "0x01C1": "Relay module",
    "0x084D": "Relay module",
    "0x239C": "Relay module",
    "0x01C2": "Relay module (16ch)",
    "0x01BD": "Relay module (8ch)",
    "0x01BF": "Relay module (4ch)",
    "0x1209": "Relay module",
    "0x238C": "Relay module",
    "0x0269": "Dimmer module (6ch)",
    "0x25E5": "Curtain module",
    "0x25E8": "Curtain module",
    "0x00AF": "Wall keypad",
    "0x08DB": "Wall keypad",
    "0x080D": "Wall keypad",
    "0x02C9": "Curtain module (2ch, HDL-MW02.431)",
    "0x0516": "IR emitter/receiver module (HDL-MIRC04.40)",
    "0x0517": "IR emitter/receiver module (HDL-MIRC04.40)",
    "0x0166": "Dry contact module (24 zone, HDL-MS24.232)",
    "0x0DCE": "Home control unit (HDL-MRCU, 18 relay + 4 dimmer ch)",
    "0x0890": "Granite Display panel (HDL-MPTL4C.48)",
}

# Pseudo device type used only by the discovery flow: keypads/wall panels are
# identified so they can be labelled and excluded from import (they create no
# entities - their button presses are ordinary bus telegrams).
ROLE_KEYPAD: Final = "keypad"

# Type codes known to be keypads / wall panels (button senders, no channels).
# Extend with codes from your own bus - the scan log prints each device's
# type code, and anything flagged [keypad] by the traffic heuristic can be
# pinned here permanently.
HDL_KEYPAD_TYPE_CODES: Final = {
    "0x012B",  # SB_WS8M 8-key panel
    # ARSmartHome site wall panels (identified on a live bus).
    "0x00AF",  # wall keypad
    "0x08DB",  # wall keypad
    "0x080D",  # wall keypad
}

# Type codes known to be dimmer modules. The scanner also detects dimmers from
# live traffic (any channel reporting an intermediate 1-99 level), but codes
# listed here classify correctly even when every light is fully off or on
# during the scan. Add your site's dimmer codes from the scan log.
HDL_DIMMER_TYPE_CODES: Final = {
    "0x0260",  # SB_DN_DT0601 6ch dimmer
    "0x026D",  # HDL_MDT0601 6ch dimmer (newer)
    "0x0269",  # 6ch dimmer module (ARSmartHome site, MDT0601 family)
}

# Dispatcher signals
SIGNAL_DEVICE_ADDED: Final = "ar_hdl_buspro_device_added_{entry_id}_{platform}"
SIGNAL_GATEWAY_AVAILABILITY: Final = "ar_hdl_buspro_gateway_availability_{entry_id}"

# Services
SERVICE_SEND_MESSAGE: Final = "send_message"
SERVICE_ACTIVATE_SCENE: Final = "activate_scene"
SERVICE_SET_UNIVERSAL_SWITCH: Final = "set_universal_switch"

ATTR_OPERATE_CODE: Final = "operate_code"
ATTR_ADDRESS: Final = "address"
ATTR_PAYLOAD: Final = "payload"
ATTR_SCENE_ADDRESS: Final = "scene_address"
ATTR_SWITCH_NUMBER: Final = "switch_number"
ATTR_STATUS: Final = "status"
