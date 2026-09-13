"""Build and parse HDL Buspro telegrams over UDP."""
from __future__ import annotations

import logging
import socket
import traceback
from struct import pack

from ..core.telegram import Telegram
from ..devices.control import *  # noqa: F401,F403 - re-export for legacy callers
from .enums import DeviceType, OperateCode
from .generics import Generics

_LOGGER = logging.getLogger(__name__)


# Fallback for the outbound header's source-IP field, used only when the real
# local IP toward the gateway could not be determined. This exact constant was
# the unconditional, hardcoded value in every release up to 4.4.5 (and in two
# of the three upstream pybuspro forks) -- keeping it as the fallback means
# detection failing can never be worse than the old behaviour.
_LEGACY_ADVERTISED_IP = (192, 168, 1, 15)


class TelegramHelper:
    """Encode/decode HDL Buspro telegrams to/from UDP byte buffers."""

    def __init__(self, buspro=None) -> None:
        """Hold an optional Buspro client, read for its advertised IP.

        The client is stored rather than the IP itself so a value resolved
        after this helper is constructed (the route to the gateway is only
        known once the socket exists) is still picked up.
        """
        self._buspro = buspro

    def _advertised_ip_bytes(self) -> bytes:
        """Return the 4 header bytes identifying this client to the gateway.

        HDL IP gateways use this field, not just the UDP source address. A
        hardcoded 192.168.1.15 happens to be plausible on a 192.168.1.0/24
        network and is unroutable anywhere else -- which is consistent with
        polled reads working (the gateway unicasts its reply straight back to
        the UDP sender) while relayed bus broadcasts never arrive.
        """
        ip = getattr(self._buspro, "advertised_ip", None)
        if ip:
            try:
                return socket.inet_aton(ip)
            except OSError:
                _LOGGER.warning(
                    "Invalid advertised IP %r; falling back to %s",
                    ip,
                    ".".join(str(b) for b in _LEGACY_ADVERTISED_IP),
                )
        return bytes(_LEGACY_ADVERTISED_IP)

    def build_telegram_from_udp_data(self, data, address):
        """Parse a raw UDP datagram into a Telegram. Returns None on failure."""
        if not data:
            _LOGGER.debug("build_telegram_from_udp_data: no data")
            return None

        try:
            index_length_of_data_package = 16
            index_original_subnet_id = 17
            index_original_device_id = 18
            index_original_device_type = 19
            index_operate_code = 21
            index_target_subnet_id = 23
            index_target_device_id = 24
            index_content = 25
            length_of_data_package = data[index_length_of_data_package]

            content_length = (
                length_of_data_package - 1 - 1 - 1 - 2 - 2 - 1 - 1 - 1 - 1
            )
            source_subnet_id = data[index_original_subnet_id]
            source_device_id = data[index_original_device_id]
            source_device_type_hex = data[
                index_original_device_type : index_original_device_type + 2
            ]
            operate_code_hex = data[index_operate_code : index_operate_code + 2]
            target_subnet_id = data[index_target_subnet_id]
            target_device_id = data[index_target_device_id]
            content = data[index_content : index_content + content_length]
            crc = data[-2:]

            generics = Generics()

            telegram = Telegram()
            telegram.source_device_type = generics.get_enum_value(
                DeviceType, source_device_type_hex
            )
            telegram.udp_data = data
            telegram.source_address = (source_subnet_id, source_device_id)
            telegram.operate_code = generics.get_enum_value(
                OperateCode, operate_code_hex
            )
            telegram.target_address = (target_subnet_id, target_device_id)
            telegram.udp_address = address
            telegram.payload = generics.hex_to_integer_list(content)
            telegram.crc = crc

            if not self._check_crc(telegram):
                _LOGGER.debug("Telegram CRC check failed")
                return None
            return telegram
        except Exception:  # noqa: BLE001
            _LOGGER.debug(
                "Error building telegram: %s", traceback.format_exc()
            )
            return None

    @staticmethod
    def replace_none_values(telegram: Telegram) -> Telegram | None:
        """Fill required fields if missing."""
        if telegram is None:
            return None
        if telegram.payload is None:
            telegram.payload = []
        if telegram.source_address is None:
            telegram.source_address = [200, 200]
        if telegram.source_device_type is None:
            telegram.source_device_type = DeviceType.PyBusPro
        return telegram

    def build_send_buffer(self, telegram: Telegram):
        """Build the raw UDP send buffer for a telegram."""
        send_buf = bytearray(self._advertised_ip_bytes())
        send_buf.extend(b"HDLMIRACLE")
        send_buf.append(0xAA)
        send_buf.append(0xAA)

        if telegram is None:
            return None
        if telegram.payload is None:
            telegram.payload = []

        length_of_data_package = 11 + len(telegram.payload)
        send_buf.append(length_of_data_package)

        if telegram.source_address is not None:
            sender_subnet_id, sender_device_id = telegram.source_address
        else:
            sender_subnet_id = 200
            sender_device_id = 200

        send_buf.append(sender_subnet_id)
        send_buf.append(sender_device_id)

        if telegram.source_device_type is not None:
            source_device_type_hex = telegram.source_device_type.value
            send_buf.append(source_device_type_hex[0])
            send_buf.append(source_device_type_hex[1])
        else:
            send_buf.append(0)
            send_buf.append(0)

        if telegram.operate_code is None:
            _LOGGER.warning("Cannot build buffer: telegram has no operate_code")
            return None
        # operate_code may be an enum or a raw 2-byte sequence.
        operate_code_hex = (
            telegram.operate_code.value
            if hasattr(telegram.operate_code, "value")
            else telegram.operate_code
        )
        send_buf.append(operate_code_hex[0])
        send_buf.append(operate_code_hex[1])

        target_subnet_id, target_device_id = telegram.target_address
        send_buf.append(target_subnet_id)
        send_buf.append(target_device_id)

        for byte in telegram.payload:
            send_buf.append(byte)

        crc_0, crc_1 = self._calculate_crc(length_of_data_package, send_buf)
        send_buf.append(crc_0)
        send_buf.append(crc_1)

        return send_buf

    def _calculate_crc(self, length_of_data_package, send_buf):
        crc_buf_length = length_of_data_package - 2
        crc_buf = send_buf[-crc_buf_length:]
        crc = self._crc16(bytes(crc_buf))
        return pack(">H", crc)

    def _calculate_crc_from_telegram(self, telegram):
        length_of_data_package = 11 + len(telegram.payload)
        crc_buf_length = length_of_data_package - 2
        send_buf = telegram.udp_data[:-2]
        crc_buf = send_buf[-crc_buf_length:]
        crc = self._crc16(bytes(crc_buf))
        return pack(">H", crc)

    def _check_crc(self, telegram) -> bool:
        return self._calculate_crc_from_telegram(telegram) == telegram.crc

    @staticmethod
    def _crc16(data: bytes) -> int:
        """CRC16 variant used by HDL Buspro."""
        xor_in = 0x0000
        xor_out = 0x0000
        poly = 0x1021

        reg = xor_in
        for octet in data:
            for i in range(8):
                topbit = reg & 0x8000
                if octet & (0x80 >> i):
                    topbit ^= 0x8000
                reg <<= 1
                if topbit:
                    reg ^= poly
            reg &= 0xFFFF
        return reg ^ xor_out
