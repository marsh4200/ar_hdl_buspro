"""Small generic helper utilities."""
from __future__ import annotations

from .enums import DeviceType, OperateCode


class Generics:
    """Misc helper functions used throughout the library."""

    @staticmethod
    def calculate_minutes_seconds(seconds: int) -> tuple[int, int]:
        """Split a duration in seconds into (minutes, seconds)."""
        return divmod(seconds, 60)

    @staticmethod
    def integer_list_to_hex(list_):
        """Convert a list of integers to a bytearray."""
        return bytearray(list_)

    @staticmethod
    def hex_to_integer_list(hex_value):
        """Convert a bytes/bytearray sequence to a list of integers."""
        return [byte for byte in hex_value]

    @staticmethod
    def enum_has_value(enum, value) -> bool:
        """Return True if `value` is a valid member of `enum`."""
        return any(value == item.value for item in enum)

    def get_enum_value(self, enum, value):
        """Return enum member matching `value`, or None if unknown.

        Works for any Enum class (previously only DeviceType/OperateCode were
        handled and everything else silently returned None).
        """
        return enum(value) if self.enum_has_value(enum, value) else None
