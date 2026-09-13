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

    # Channel-addressed temperature read used by the MPTL/panel family
    # (HDL-MPTL4C.48 "Granite Display", HDL-MPTLC43.46-A "Enviro", ...).
    # Request payload is [channel]; the reply is
    #   [channel, signed_whole_degrees, <float32 little-endian degrees>]
    # where the trailing 4 bytes are optional but present on every frame
    # captured so far.
    #
    # CONFIRMED from a live capture on a Granite Display at 1.60 (device
    # type 0x0890): 456 frames over two days, e.g.
    #   [1, 27, 216, 163, 216, 65] -> struct.unpack("<f", bytes([216,163,216,65]))
    #                              -> 27.080 degC
    #   [2, 26, 134, 235, 215, 65] -> 26.990 degC
    #   [1, 26,  12, 215, 215, 65] -> 26.980 degC
    # The whole-degree byte is a truncation of the float (26.99 -> 26), so
    # the float wins when it is present. Note the byte is PLAIN Celsius --
    # there is no -20 bias on this telegram (see Sensor.temperature).
    #
    # Without these two members defined, Generics.get_enum_value() returns
    # None for 0xE3E8 frames and telegram_helper hands every Sensor a
    # telegram whose operate_code is None -- so the panel's only working
    # temperature source was being dropped before any decode ran. Both
    # reference implementations (Frequencies/home_assistant_buspro and
    # the home-assistant-buspro dashboard fork) define this pair.
    ReadTemperature = b"\xE3\xE7"
    ReadTemperatureResponse = b"\xE3\xE8"

    # Motion-only read for CMS-PIR style modules. These answer 0xDB00 with
    # 0xDB01 and do NOT answer ReadSensorStatus (0x1645) at all, so without
    # this pair a PIR-only presence sensor is polled with an opcode it will
    # never reply to and its entity stays "clear" forever. Reply payload is
    # [.., .., .., motion] -- motion at index 3, same as both reference
    # implementations.
    ReadMotionSensorStatus = b"\xDB\x00"
    ReadMotionSensorStatusResponse = b"\xDB\x01"

    ReadFloorHeatingStatus = b"\x19\x44"
    ReadFloorHeatingStatusResponse = b"\x19\x45"
    ControlFloorHeatingStatus = b"\x19\x46"
    ControlFloorHeatingStatusResponse = b"\x19\x47"

    # Air conditioner control via an IR emitter module's live AC panel
    # channels (e.g. HDL-MIRC04.40, GitHub issue #17). ControlACStatus and
    # ControlACStatusResponse were confirmed from a real bus capture on that
    # issue. ReadACStatus/ReadACStatusResponse were never observed directly
    # (nothing in the capture issued a read) -- they're inferred from the
    # same read=control-2/control-1 numbering this file already uses for
    # floor heating (0x1944-0x1947 above), and are parsed defensively: a
    # wrong guess here just means no response arrives, not a bad command
    # sent to the bus.
    ReadACStatus = b"\x19\x38"
    ReadACStatusResponse = b"\x19\x39"
    ControlACStatus = b"\x19\x3A"
    ControlACStatusResponse = b"\x19\x3B"

    ReadDryContactStatus = b"\x15\xCE"
    ReadDryContactStatusResponse = b"\x15\xCF"

    ReadSensorsInOneStatus = b"\x16\x04"
    ReadSensorsInOneStatusResponse = b"\x16\x05"
    # Unsolicited push from a "sensors_in_one" module (PIR/motion fires,
    # periodic temp/humidity update, etc). Same payload layout as
    # ReadSensorsInOneStatusResponse above -- confirmed against a second,
    # independent HDL Buspro implementation (Frequencies/home_assistant_
    # buspro) which handles both ops with one identical parser. Without
    # this opcode defined, telegram_helper's enum lookup returns None for
    # 0x1630 frames and they get silently dropped by every Sensor's
    # _telegram_received_cb -- which starves a "sensors_in_one" motion
    # sensor of any update that isn't its own poll response.
    BroadcastSensorsInOneStatusResponse = b"\x16\x30"

    TIME_IF_FROM_LOGIC_OR_SECURITY = b"\xDA\x44"
    INFO_IF_FROM_RELE_10V = b"\xEF\xFF"
