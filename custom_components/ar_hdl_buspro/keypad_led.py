"""Keep HDL keypad button LEDs in step with relays switched from Home Assistant.

An HDL keypad's button LED shows the BUTTON's own on/off state, not the
load's. When a button is pressed the keypad flips its state and switches the
relay, so they agree. When anything else switches that relay - Home
Assistant, another keypad, logic - the keypad is never told, and its LED
stays where it was.

HDL's fix is the panel-control command "Control button status": operate code
0xE3D8 with payload [17, button, 1 = on / 0 = off], sent to the keypad. It
sets the button's LED (and state) without running the button's targets.
Confirmed on a Buspro wireless panel (0x13C3 at 2.1).

Links are stored per relay/light entity as text, e.g. "2.1:2, 1.50:4"
(keypad subnet.device:button), filled in automatically by the bus scan from
the keypads' own button programming (see discovery.py) and editable by hand.
"""

# Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).
# All rights reserved. Proprietary and confidential.
# Licensed software - see LICENSE in the repository root.
from __future__ import annotations

import logging
import re

from .pybuspro.devices.control import _GenericControl

_LOGGER = logging.getLogger(__name__)

# Panel control (0xE3D8) and its "Control button status" sub-type.
OP_PANEL_CONTROL = b"\xe3\xd8"
PANEL_CONTROL_BUTTON_STATUS = 17

# Button-target type meaning "single channel control" in a keypad's
# programming (0xE001 reply byte 2), e.g. [2, 1, 89, 2, 1, 2, 100, 0, 0] =
# button 2, target 1: single channel control of 2.1 channel 2 at 100%.
TARGET_TYPE_SINGLE_CHANNEL = 89

_LINK_RE = re.compile(r"(?<!\d)(\d{1,3})\s*[.\-/]\s*(\d{1,3})\s*[:#]\s*(\d{1,3})(?!\d)")


def parse_keypad_leds(value) -> list[tuple[int, int, int]]:
    """'2.1:2, 1.50:4' -> [(2, 1, 2), (1, 50, 4)]; bad tokens are ignored."""
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        value = ", ".join(str(v) for v in value)
    links: list[tuple[int, int, int]] = []
    for match in _LINK_RE.finditer(str(value)):
        subnet, device, button = (int(g) for g in match.groups())
        if subnet > 255 or device > 255 or not 1 <= button <= 255:
            continue
        link = (subnet, device, button)
        if link not in links:
            links.append(link)
    return links


def format_keypad_leds(links) -> str:
    """[(2, 1, 2), (1, 50, 4)] -> '1.50:4, 2.1:2' (sorted, deduplicated)."""
    return ", ".join(f"{s}.{d}:{b}" for s, d, b in sorted(set(links)))


class KeypadLedSync:
    """Send 'Control button status' to every keypad button linked to a load."""

    def __init__(self, hdl, value) -> None:
        self._hdl = hdl
        self._links = parse_keypad_leds(value)
        self._last: bool | None = None

    @property
    def active(self) -> bool:
        return bool(self._links)

    async def push(self, on: bool, force: bool = False) -> None:
        """Set the linked buttons' LEDs to `on`.

        Without `force` nothing is sent if the LEDs were already set to this
        state, so a relay's own status replies don't repeat the message.
        Home Assistant commands force it, which also repairs a keypad that
        drifted out of step for any other reason.
        """
        if not self._links:
            return
        on = bool(on)
        if not force and self._last is on:
            return
        self._last = on
        network = getattr(self._hdl, "network_interface", None)
        if network is None:
            return
        for subnet, device, button in self._links:
            ctrl = _GenericControl(self._hdl)
            ctrl.subnet_id, ctrl.device_id = subnet, device
            ctrl.operate_code = OP_PANEL_CONTROL
            ctrl.payload = [PANEL_CONTROL_BUTTON_STATUS, button, 1 if on else 0]
            try:
                await ctrl.send()
            except Exception as err:  # noqa: BLE001 - LED sync must never break control
                _LOGGER.debug(
                    "Keypad LED sync to %s.%s button %s failed: %s",
                    subnet, device, button, err,
                )
