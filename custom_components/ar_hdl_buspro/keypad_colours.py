"""Keypad button LED colours (on colour / off colour per button).

Which panels have a colour setting at all, and which command each one takes,
is decided purely from the panel's device type - nothing is probed. A panel
type with no colour setting is never sent a colour command.

Three command families:

  "button"   read 0xE14C [button]        -> 0xE14D [button, on RGB, off RGB(, type)]
             write 0xE14E [button, on RGB, off RGB(, type)]
  "rgbmode"  read 0x354B [button]        -> 0x354C [button, mode, on RGB, off RGB]
             write 0x3549 [button, mode, on RGB, off RGB]
  "dlp"      read 0x198C [page]          -> 0x198D [page, 9 x (on RGB, off RGB)]
             write 0x198E [page, 9 x (on RGB, off RGB)]
             slot 0 of a page is its shortcut key, slots 1-8 its buttons.

Bytes this module does not edit (the rgb mode, the light type, a DLP page's
other slots) are always written back exactly as they were read.
"""

# Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).
# All rights reserved. Proprietary and confidential.
# Licensed software - see LICENSE in the repository root.
from __future__ import annotations

import asyncio
import logging
from typing import Callable

from .pybuspro.core.telegram import Telegram

_LOGGER = logging.getLogger(__name__)

SCHEME_BUTTON = "button"
SCHEME_RGBMODE = "rgbmode"
SCHEME_DLP = "dlp"

OP_BUTTON_READ = 0xE14C
OP_BUTTON_WRITE = 0xE14E
OP_RGBMODE_READ = 0x354B
OP_RGBMODE_WRITE = 0x3549
OP_DLP_READ = 0x198C
OP_DLP_WRITE = 0x198E

DLP_PAGE_BYTES = 54  # 9 slots x (on RGB + off RGB)

# Reply wait per try / number of tries. Wireless panels answer slowly and
# drop frames, so they get many short tries; wired panels a few longer ones.
TIMING_WIRELESS = (0.5, 12)
TIMING_WIRED = (1.5, 4)
WRITE_GAP = 0.05

# ----- device types (decimal HDL type codes) --------------------------------
TOUCH_PANELS = frozenset({
    223, 224, 225, 226, 2022, 2023, 2024, 2029, 2030, 2031, 2032, 2061,
    2062, 2063, 2064, 2065, 2066, 2067, 2068, 2069, 2070, 2071, 2072,
    2073, 2074, 2075, 2085, 2086, 2087, 2088, 2089, 2090, 2091, 2102,
    2104, 2105, 2106, 2107, 2114, 2115, 2116, 2117, 2118, 2119, 2120,
    2121, 2122, 2123, 2124, 2125, 2126, 2127, 2128, 2129, 2130, 2131,
    2132, 2133, 2153, 2154, 2155, 2267, 2268, 2269, 2282, 5038, 5039,
    5040, 5041, 5050, 5051, 5052, 5053, 5055, 5056, 5057, 5058, 5059,
    5060, 5061, 5075, 5076, 5077, 5078, 5079, 12128, 12130, 12131, 12132,
    12133,
})
ROUND_PANELS = frozenset({
    2019, 2020, 2021, 2040, 2041, 2042, 2043, 2096, 2097, 2098, 2099,
    2100, 2101, 2141, 2142, 2143, 2144, 2145, 2146, 2147, 2148, 2149,
    2150, 2151, 2152, 2163, 2164, 2165, 2166, 2167, 2177, 2178, 2179,
    2180, 2183, 2184, 2185, 2186, 2187, 2188, 2218, 2251, 2252, 2253,
    2254, 2255, 2256, 2257, 2258, 2259, 2260, 2261, 2262, 2303, 2304,
    2305, 2351, 2352, 2353, 2354, 2355, 2356, 5034, 5035, 5036, 5037,
    5046, 5047, 5048, 5049, 5054, 12100, 12101,
})
HOTEL_AC_WITH_COLOUR = frozenset({
    169, 178, 184, 189, 197, 10197,
})
DOORBELLS = frozenset({
    239, 241, 3054, 3056, 3059, 3067, 3070, 3071, 3072, 3073, 3074, 3075,
    3077, 3081, 3082, 3083, 3084, 3089, 3091, 3092, 3094,
})
DOORBELLS_NO_COLOUR = frozenset({
    239, 3073, 3074, 3089, 3091, 3094,
})
COLOUR_AND_RGB = frozenset({
    3076, 3078, 3080, 3085,
})
FANGYUE21_PANELS = frozenset({
    2366, 2367, 2368, 2369, 2370, 2371, 2372, 2373, 2374, 2375, 2376,
    2377, 2378, 2384, 2385, 2386, 2387, 2388,
})
TOUCH_PANELS_EXCLUDED = frozenset({
    2073, 2074, 2075,
})
ROUND_PANELS_NO_COLOUR = frozenset({
    2147, 2148, 2149, 2150, 2151, 2152, 2218,
})
RGB_MODE_PANELS = frozenset({
    2141, 2142, 2143, 2144, 2145, 2146, 2163, 2164, 2165, 2166, 2167,
    2183, 2184, 2185, 2186, 2187, 2188, 2251, 2252, 2253, 2254, 2255,
    2256, 2257, 2258, 2259, 2260, 2261, 2262, 2303, 2304, 2305, 2351,
    2352, 2353, 2354, 2355, 2356, 2366, 2367, 2368, 2369, 2370, 2371,
    2372, 2373, 2374, 2375, 2376, 2377, 2378, 2384, 2385, 2386, 2387, 2388,
})
LIGHT_TYPE_PANELS = frozenset({
    2177, 2178, 2179, 2180,
})
COLOUR_DLPS = frozenset({
    165, 168, 170, 172, 175, 188, 195, 9100, 9101, 9105, 9110, 9111, 9131,
    9132, 9133, 9146, 19100,
})
DLPS_EXCLUDED = frozenset({
    2158, 2176, 2181, 2191, 2243, 2271,
})
FZ_DLPS = frozenset({
    195, 9105,
})
KLUGER_DLPS = frozenset({
    9101, 9110,
})
WIRELESS_OLED = frozenset({
    5064, 5071, 5072, 7002, 7003, 7004, 7007, 7008,
})
KLUGER_TOUCH_PANELS = frozenset({
    2153, 2154, 2155,
})
ONE_BACKLIGHT_PANELS = frozenset({
    2147, 2148, 2149, 2150, 2151, 2152, 2342, 2343, 2344, 2345, 2346,
    2347, 2348, 2349, 2360, 2362, 2389,
})
THREE_BUTTON_PANELS = frozenset({
    2102,
})
WIRELESS_TYPES = frozenset({
    1500, 1501, 1502, 1503, 1504, 1505, 5000, 5001, 5002, 5003, 5004,
    5005, 5006, 5007, 5030, 5031, 5032, 5033, 5034, 5035, 5036, 5037,
    5038, 5039, 5040, 5041, 5042, 5043, 5044, 5045, 5046, 5047, 5048,
    5049, 5050, 5051, 5052, 5053, 5054, 5055, 5056, 5057, 5058, 5059,
    5060, 5061, 5062, 5063, 5064, 5065, 5066, 5067, 5068, 5069, 5070,
    5071, 5072, 5073, 5074, 5075, 5076, 5077, 5078, 5079, 5300, 5301,
    5302, 5303, 5304, 5305, 5306, 5307, 5500, 5501, 5502, 5503, 5504,
    5505, 5506, 5507, 5508, 5509, 5510, 5511, 5512, 5513, 5514, 5515,
    5700, 5701, 5702, 5703, 5704, 5705, 5706, 5707, 5708, 5800, 5900,
    5901, 5902, 6100, 6101, 6102, 6103, 6104, 6105, 6107, 6300, 6400,
})


def code_to_int(type_code) -> int | None:
    """'0x13C3' / 5059 -> 5059; None if it can't be read."""
    if isinstance(type_code, int):
        return type_code
    try:
        return int(str(type_code), 16)
    except (TypeError, ValueError):
        return None


def led_scheme_for(type_code) -> str | None:
    """Which colour command a panel type takes; None = no colour setting."""
    t = code_to_int(type_code)
    if t is None:
        return None
    per_button = (
        t in TOUCH_PANELS
        or t in ROUND_PANELS
        or t in HOTEL_AC_WITH_COLOUR
        or (t in DOORBELLS and t not in DOORBELLS_NO_COLOUR)
        or t in COLOUR_AND_RGB
        or t in FANGYUE21_PANELS
    ) and t not in TOUCH_PANELS_EXCLUDED and t not in ROUND_PANELS_NO_COLOUR
    if per_button:
        return SCHEME_RGBMODE if t in RGB_MODE_PANELS else SCHEME_BUTTON
    if t in COLOUR_DLPS and t not in DLPS_EXCLUDED:
        return SCHEME_DLP
    return None


def led_button_count(type_code, buttons: int) -> int:
    """Colour slots a per-button panel has (some types override the count)."""
    t = code_to_int(type_code)
    if t in WIRELESS_OLED:
        return 4
    if t in KLUGER_TOUCH_PANELS:
        return 6
    if t in ONE_BACKLIGHT_PANELS:
        return 1
    if t in THREE_BUTTON_PANELS:
        return 3
    return buttons


def dlp_led_pages(type_code) -> tuple[int, int]:
    """(pages, last button slot) of a colour DLP."""
    t = code_to_int(type_code)
    if t in FZ_DLPS:
        return 9, 6
    if t in KLUGER_DLPS:
        return 1, 4
    return 7, 8


def is_wireless(type_code) -> bool:
    return code_to_int(type_code) in WIRELESS_TYPES


def rgb_to_hex(data) -> str:
    return "#" + "".join(f"{int(x) & 0xFF:02x}" for x in list(data)[:3])


def hex_to_rgb(colour) -> list[int]:
    text = str(colour or "").strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    text = (text + "000000")[:6]
    try:
        return [int(text[i : i + 2], 16) for i in (0, 2, 4)]
    except ValueError:
        return [0, 0, 0]


class ColourError(Exception):
    """A panel did not answer / confirm; the message is shown to the user."""


def _telegram_opcode(telegram) -> int | None:
    """The raw 16-bit operate code of a received telegram.

    The parser maps operate codes through an enum and leaves the ones it
    doesn't know as None, which covers every colour reply - so read it from
    the datagram itself.
    """
    data = getattr(telegram, "udp_data", None)
    if data is not None and len(data) >= 23:
        return (data[21] << 8) | data[22]
    op = getattr(telegram, "operate_code", None)
    value = getattr(op, "value", op)
    if isinstance(value, (bytes, bytearray, list, tuple)) and len(value) >= 2:
        return (int(value[0]) << 8) | int(value[1])
    return None


class KeypadColours:
    """Read and write keypad LED colours over one bus client."""

    def __init__(self, hdl) -> None:
        self._hdl = hdl
        # One colour job on the bus at a time.
        self._lock = asyncio.Lock()
        # Raw DLP pages as last read: {(subnet, device): {page: bytes}}.
        self._dlp_raw: dict[tuple[int, int], dict[int, bytes]] = {}

    async def _request(
        self,
        address: tuple[int, int],
        opcode: int,
        payload: list[int],
        *,
        match: Callable[[list[int]], bool] | None = None,
        timeout: float,
        tries: int,
    ) -> list[int] | None:
        """Send and wait for opcode + 1 from `address`; its payload, or None."""
        ni = getattr(self._hdl, "network_interface", None)
        if ni is None:
            raise ColourError("The gateway is not connected.")
        reply_op = (opcode + 1) & 0xFFFF
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()

        def _handler(telegram) -> None:
            if fut.done():
                return
            src = getattr(telegram, "source_address", None)
            if not src or (int(src[0]), int(src[1])) != tuple(address):
                return
            if _telegram_opcode(telegram) != reply_op:
                return
            data = [int(x) for x in (getattr(telegram, "payload", None) or [])]
            if match is not None and not match(data):
                return
            fut.set_result(data)

        handlers = self._hdl.virtual_handlers
        handlers.append(_handler)
        try:
            for _ in range(max(1, tries)):
                telegram = Telegram()
                telegram.target_address = tuple(address)
                telegram.operate_code = bytes([opcode >> 8, opcode & 0xFF])
                telegram.payload = list(payload)
                await ni.send_telegram(telegram)
                try:
                    return await asyncio.wait_for(asyncio.shield(fut), timeout)
                except asyncio.TimeoutError:
                    continue
            return None
        finally:
            if _handler in handlers:
                handlers.remove(_handler)
            if not fut.done():
                fut.cancel()

    @staticmethod
    def _timing(type_code) -> dict:
        timeout, tries = TIMING_WIRELESS if is_wireless(type_code) else TIMING_WIRED
        return {"timeout": timeout, "tries": tries}

    async def read(self, address, type_code, buttons: int) -> dict:
        """Read every button's colours. Returns {scheme, colors:[...]}."""
        scheme = led_scheme_for(type_code)
        if scheme is None:
            return {"scheme": None, "colors": []}
        address = (int(address[0]), int(address[1]))
        tm = self._timing(type_code)
        colours: list[dict] = []
        async with self._lock:
            if scheme in (SCHEME_BUTTON, SCHEME_RGBMODE):
                op = OP_BUTTON_READ if scheme == SCHEME_BUTTON else OP_RGBMODE_READ
                first = 2 if scheme == SCHEME_RGBMODE else 1
                count = led_button_count(type_code, buttons)
                for button in range(1, count + 1):
                    data = await self._request(
                        address, op, [button],
                        match=lambda p, b=button: p[:1] == [b], **tm,
                    )
                    if data is None:
                        if button == 1:
                            raise ColourError(
                                "The keypad did not answer. Check that it is "
                                "powered and online, then try again."
                            )
                        break  # fewer colour slots than buttons
                    if len(data) < first + 6:
                        continue
                    entry = {
                        "button": button,
                        "on": rgb_to_hex(data[first : first + 3]),
                        "off": rgb_to_hex(data[first + 3 : first + 6]),
                    }
                    if scheme == SCHEME_RGBMODE:
                        entry["rgbmode"] = data[1]
                    elif code_to_int(type_code) in LIGHT_TYPE_PANELS and len(data) > 7:
                        entry["colortype"] = 1 if data[7] == 1 else 0
                    colours.append(entry)
            else:
                pages, last = dlp_led_pages(type_code)
                pages = min(pages, max(1, (int(buttons) + 7) // 8))
                raw: dict[int, bytes] = {}
                for page in range(1, pages + 1):
                    data = await self._request(
                        address, OP_DLP_READ, [page],
                        match=lambda p, pg=page: p[:1] == [pg], **tm,
                    )
                    if data is None:
                        if page == 1:
                            raise ColourError(
                                "The panel did not answer. Check that it is "
                                "powered and online, then try again."
                            )
                        break
                    raw[page] = bytes(x & 0xFF for x in data[1:])
                self._dlp_raw[address] = raw
                for page, page_data in raw.items():
                    for slot in range(1, min(8, last) + 1):
                        o = slot * 6
                        if len(page_data) >= o + 6:
                            colours.append({
                                "button": (page - 1) * 8 + slot,
                                "on": rgb_to_hex(page_data[o : o + 3]),
                                "off": rgb_to_hex(page_data[o + 3 : o + 6]),
                            })
        return {"scheme": scheme, "colors": colours}

    async def save(self, address, type_code, colours: list[dict]) -> int:
        """Write the given buttons' colours; returns how many were written."""
        scheme = led_scheme_for(type_code)
        if scheme is None:
            raise ColourError(
                "This panel type has no LED colour setting, so nothing was sent."
            )
        address = (int(address[0]), int(address[1]))
        tm = self._timing(type_code)
        written = 0
        if scheme == SCHEME_DLP and not self._dlp_raw.get(address):
            raise ColourError("Read the colours from the panel first.")
        async with self._lock:
            if scheme in (SCHEME_BUTTON, SCHEME_RGBMODE):
                for c in colours:
                    button = int(c["button"])
                    rgb = hex_to_rgb(c.get("on")) + hex_to_rgb(c.get("off"))
                    if scheme == SCHEME_BUTTON:
                        op, payload = OP_BUTTON_WRITE, [button] + rgb
                        if code_to_int(type_code) in LIGHT_TYPE_PANELS:
                            payload.append(int(c.get("colortype", 0)) & 0xFF)
                    else:
                        op = OP_RGBMODE_WRITE
                        payload = [button, int(c.get("rgbmode", 0)) & 0xFF] + rgb
                    if await self._request(address, op, payload, **tm) is None:
                        raise ColourError(
                            f"The keypad did not confirm button {button}. "
                            f"{written} button(s) were saved before it."
                        )
                    written += 1
                    await asyncio.sleep(WRITE_GAP)
                return written
            raw = self._dlp_raw[address]
            pages = {p: bytearray(v) for p, v in raw.items()}
            touched: dict[int, int] = {}
            for c in colours:
                button = int(c["button"])
                page, slot = (button - 1) // 8 + 1, (button - 1) % 8 + 1
                if page not in pages:
                    continue
                buf = pages[page]
                if len(buf) < DLP_PAGE_BYTES:
                    buf.extend(b"\x00" * (DLP_PAGE_BYTES - len(buf)))
                o = slot * 6
                buf[o : o + 6] = bytes(
                    hex_to_rgb(c.get("on")) + hex_to_rgb(c.get("off"))
                )
                touched[page] = touched.get(page, 0) + 1
            for page in sorted(touched):
                payload = [page] + list(pages[page][:DLP_PAGE_BYTES])
                # Any confirmation from the panel counts: its reply does not
                # reliably lead with the page number.
                data = await self._request(address, OP_DLP_WRITE, payload, **tm)
                if data is None:
                    raise ColourError(f"The panel did not confirm page {page}.")
                raw[page] = bytes(pages[page])
                written += touched[page]
                await asyncio.sleep(WRITE_GAP)
            return written
