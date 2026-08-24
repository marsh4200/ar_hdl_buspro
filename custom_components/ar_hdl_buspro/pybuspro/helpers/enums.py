"""Enumerations used throughout the pybuspro library."""
from __future__ import annotations

from enum import Enum


class SuccessOrFailure(Enum):
    """Telegram success/failure indicator."""

    Success = b"\xF8"
    Failure = b"\xF5"


class DeviceType(Enum):
    """HDL Buspro device types."""

    NotSet = b"\x00\x00"
    SB_DN_6B0_10v = b"\x00\x11"       # Heating relay
    SB_DN_SEC250K = b"\x0B\xE9"       # Security module
    SB_CMS_12in1 = b"\x01\x34"        # 12-in-1 sensor
    SB_DN_Logic960 = b"\x04\x53"      # Logic module
    SB_DLP2 = b"\x00\x86"             # DLP
    SB_DLP = b"\x00\x95"              # DLP
    SB_DLP_v2 = b"\x00\x9C"           # DLPv2
    PyBusPro = b"\xFF\xFC"
    SmartHDLTest = b"\xFF\xFD"
    SetupTool = b"\xFF\xFE"
    SB_WS8M = b"\x01\x2B"             # 8 keys panel
    SB_CMS_8in1 = b"\x01\x35"         # 8-in-1 sensor
    SB_DN_DT0601 = b"\x02\x60"        # 6ch dimmer
    HDL_MDT0601 = b"\x02\x6D"         # 6ch dimmer (newer)
    SB_DN_R0816 = b"\x01\xAC"         # Relay
    SB_DRY_4Z = b"\x00\x77"           # Dry contact
    HDL_MSP07M = b"\x01\x50"          # Sensors-in-one


class OnOff(Enum):
    """Generic On/Off enumeration (raw byte values)."""

    OFF = 0
    ON = 255


class SwitchStatusOnOff(Enum):
    """Switch status flag."""

    OFF = 0
    ON = 1


class OnOffStatus(Enum):
    """Generic on/off status."""

    OFF = 0
    ON = 1


class TemperatureType(Enum):
    """Temperature unit type."""

    Celsius = 0
    Fahrenheit = 1


class TemperatureMode(Enum):
    """Floor heating temperature/preset mode."""

    Normal = 1
    Day = 2
    Night = 3
    Away = 4
    Timer = 5


class OperateCode(Enum):
    """HDL Buspro operate codes used by this library."""

    NotSet = b"\x00"

    SingleChannelControl = b"\x00\x31"
    SingleChannelControlResponse = b"\x00\x32"
    ReadStatusOfChannels = b"\x00\x33"
    ReadStatusOfChannelsResponse = b"\x00\x34"
    SceneControl = b"\x00\x02"
    SceneControlResponse = b"\x00\x03"
    UniversalSwitchControl = b"\xE0\x1C"
    UniversalSwitchControlResponse = b"\xE0\x1D"

    ReadStatusOfUniversalSwitch = b"\xE0\x18"
    ReadStatusOfUniversalSwitchResponse = b"\xE0\x19"
    BroadcastStatusOfUniversalSwitch = b"\xE0\x17"

    BroadcastSensorStatusResponse = b"\x16\x44"
    ReadSensorStatus = b"\x16\x45"
    ReadSensorStatusResponse = b"\x16\x46"
    BroadcastSensorStatusAutoResponse = b"\x16\x47"

    CurtainSwitchControl = b"\xE3\xE0"
    CurtainSwitchControlResponse = b"\xE3\xE1"
    ReadStatusOfCurtainSwitch = b"\xE3\xE2"
    ReadStatusOfCurtainSwitchResponse = b"\xE3\xE3"

    BroadcastTemperatureResponse = b"\xE3\xE5"

    ReadFloorHeatingStatus = b"\x19\x44"
    ReadFloorHeatingStatusResponse = b"\x19\x45"
    ControlFloorHeatingStatus = b"\x19\x46"
    ControlFloorHeatingStatusResponse = b"\x19\x47"

    ReadDryContactStatus = b"\x15\xCE"
    ReadDryContactStatusResponse = b"\x15\xCF"

    ReadSensorsInOneStatus = b"\x16\x04"
    ReadSensorsInOneStatusResponse = b"\x16\x05"

    TIME_IF_FROM_LOGIC_OR_SECURITY = b"\xDA\x44"
    INFO_IF_FROM_RELE_10V = b"\xEF\xFF"
