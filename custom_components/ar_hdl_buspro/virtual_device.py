"""Home Assistant's own address on the HDL bus, for keypad buttons.

A spare keypad button (one with no relay behind it) can be programmed in the
HDL software as:

    mode: Single ON/OFF
    target: Universal Switch <N> at 250.250  (HA_VIRTUAL_ADDRESS)

Each press then sends UniversalSwitchControl (0xE01C) [N, 255 = on / 0 = off]
to 250.250. Nothing physical lives there: this module answers on its behalf.
It replies UniversalSwitchControlResponse (0xE01D) [N, status] so the keypad
sees the command succeed (without a reply the keypad flashes its LED three
times and turns it back off), keeps the on/off state per universal switch,
notifies the matching "keypad button" entities and fires an
`ar_hdl_buspro_keypad_button` event for automations.

It also answers ReadStatusOfUniversalSwitch (0xE018) with 0xE019 [N, status],
so a keypad that asks for the state after a restart gets the right answer.
"""

# Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).
# All rights reserved. Proprietary and confidential.
# Licensed software - see LICENSE in the repository root.
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from .pybuspro.core.telegram import Telegram
from .pybuspro.helpers.enums import DeviceType, OperateCode

_LOGGER = logging.getLogger(__name__)

# HDL universal-switch status bytes (the HDL software writes 255 for ON).
UV_ON = 255
UV_OFF = 0


class VirtualKeypadResponder:
    """Answer universal-switch commands addressed to Home Assistant."""

    def __init__(
        self,
        hdl,
        address: tuple[int, int],
        fire_event: Callable[[dict], None] | None = None,
    ) -> None:
        self._hdl = hdl
        self.address = (int(address[0]), int(address[1]))
        self._fire_event = fire_event
        self._states: dict[int, bool] = {}
        self._listeners: dict[int, list[Callable[[bool, tuple | None], None]]] = {}

    # ----- state ----------------------------------------------------------
    def state(self, number: int) -> bool:
        return self._states.get(int(number), False)

    def add_listener(self, number: int, listener) -> Callable[[], None]:
        """Call `listener(on, source)` whenever switch `number` changes."""
        number = int(number)
        self._listeners.setdefault(number, []).append(listener)

        def _remove() -> None:
            listeners = self._listeners.get(number, [])
            if listener in listeners:
                listeners.remove(listener)

        return _remove

    def set_state(self, number: int, on: bool, source=None) -> None:
        """Set a switch from Home Assistant (no bus traffic of its own)."""
        self._update(int(number), bool(on), source)

    def _update(self, number: int, on: bool, source) -> None:
        self._states[number] = on
        for listener in list(self._listeners.get(number, [])):
            try:
                listener(on, source)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Keypad button listener failed")

    # ----- bus ------------------------------------------------------------
    def handle_telegram(self, telegram) -> None:
        """Inspect every received telegram; act on those sent to us."""
        target = getattr(telegram, "target_address", None)
        if target is None or tuple(target) != self.address:
            return
        source = getattr(telegram, "source_address", None)
        source = tuple(source) if source is not None else None
        if source == self.address:
            return
        op = getattr(telegram, "operate_code", None)
        payload = list(getattr(telegram, "payload", None) or [])

        if op == OperateCode.UniversalSwitchControl and len(payload) >= 2:
            number, status = int(payload[0]), int(payload[1])
            on = status != UV_OFF
            # Acknowledge first: the keypad is waiting for it.
            self._send(
                source,
                OperateCode.UniversalSwitchControlResponse,
                [number, status],
            )
            self._update(number, on, source)
            if self._fire_event is not None:
                try:
                    self._fire_event(
                        {
                            "switch_number": number,
                            "state": "on" if on else "off",
                            "keypad": f"{source[0]}.{source[1]}" if source else None,
                        }
                    )
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("Keypad button event failed")

        elif op == OperateCode.ReadStatusOfUniversalSwitch and payload:
            number = int(payload[0])
            self._send(
                source,
                OperateCode.ReadStatusOfUniversalSwitchResponse,
                [number, UV_ON if self.state(number) else UV_OFF],
            )

    def _send(self, target, operate_code, payload) -> None:
        if target is None:
            return
        network = getattr(self._hdl, "network_interface", None)
        if network is None:
            return
        telegram = Telegram()
        telegram.source_address = self.address
        telegram.source_device_type = DeviceType.PyBusPro
        telegram.target_address = target
        telegram.operate_code = operate_code
        telegram.payload = payload

        async def _go() -> None:
            try:
                await network.send_telegram(telegram)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Keypad button reply failed: %s", err)

        loop = getattr(self._hdl, "loop", None)
        try:
            asyncio.ensure_future(_go(), loop=loop) if loop else asyncio.ensure_future(_go())
        except RuntimeError:
            _LOGGER.debug("No running loop for keypad button reply")
