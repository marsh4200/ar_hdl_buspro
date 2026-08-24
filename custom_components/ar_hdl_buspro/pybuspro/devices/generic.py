"""Generic raw-telegram sender used by the send_message service."""
from __future__ import annotations

from ..helpers.enums import OperateCode
from ..helpers.generics import Generics
from .control import _GenericControl
from .device import Device


class Generic(Device):
    """Send a raw HDL Buspro telegram."""

    def __init__(self, buspro, device_address, payload, operate_code, name: str = "") -> None:
        """Initialize the generic sender."""
        super().__init__(buspro, device_address, name)
        self._buspro = buspro
        self._device_address = device_address
        self._payload = payload
        self._operate_code = operate_code

    async def run(self) -> None:
        """Send the configured raw telegram."""
        ctrl = _GenericControl(self._buspro)
        ctrl.subnet_id, ctrl.device_id = self._device_address
        ctrl.payload = self._payload

        # operate_code can come in as: an OperateCode enum, a 2-byte sequence
        # like [4, 78] from the service call, or a raw bytes object.
        oc = self._operate_code
        if isinstance(oc, OperateCode):
            ctrl.operate_code = oc
        elif isinstance(oc, (list, tuple)) and len(oc) == 2:
            byte_form = bytes(oc)
            resolved = Generics().get_enum_value(OperateCode, byte_form)
            ctrl.operate_code = resolved if resolved is not None else byte_form
        elif isinstance(oc, (bytes, bytearray)):
            resolved = Generics().get_enum_value(OperateCode, bytes(oc))
            ctrl.operate_code = resolved if resolved is not None else bytes(oc)
        else:
            ctrl.operate_code = oc  # fall through; telegram_helper handles it
        await ctrl.send()
