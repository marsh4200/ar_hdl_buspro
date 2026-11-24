"""Colour helpers for RGB channel groups and DALI-2 colour channels.

Pure functions (no Home Assistant imports) so they can be tested standalone.

HDL channel levels are 0-100. Home Assistant passes brightness 0-255 and a
colour whose brightest primary is 255; the light's level per primary is the
colour scaled by brightness.
"""

# Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).
# All rights reserved. Proprietary and confidential.
# Licensed software - see LICENSE in the repository root.
from __future__ import annotations

# Kelvin range offered for DALI-2 tunable-white channels.
MIN_KELVIN = 2000
MAX_KELVIN = 6500


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def group_levels(colour, brightness: int) -> list[int]:
    """(r, g, b[, w]) 0-255 + brightness 0-255 -> channel levels 0-100.

    A non-zero component never rounds down to 0 while the light is on, so a
    dim colour keeps its hue instead of losing a primary.
    """
    brightness = _clamp(int(brightness), 0, 255)
    levels = []
    for c in colour:
        c = _clamp(int(c), 0, 255)
        level = round(c / 255 * brightness / 255 * 100)
        if c > 0 and brightness > 0 and level == 0:
            level = 1
        levels.append(_clamp(level, 0, 100))
    return levels


def group_state(levels) -> tuple[bool, int, tuple[int, ...]]:
    """Channel levels 0-100 -> (is_on, brightness 0-255, colour 0-255).

    The colour is normalised so its brightest primary is 255 (what Home
    Assistant expects); brightness is the brightest channel. All channels at
    0 = off (colour then reported as white).
    """
    vals = [_clamp(int(v), 0, 100) for v in levels]
    peak = max(vals) if vals else 0
    if peak == 0:
        return False, 0, tuple(255 for _ in vals)
    colour = tuple(round(v / peak * 255) for v in vals)
    brightness = round(peak / 100 * 255)
    return True, brightness, colour


def kelvin_bytes(kelvin) -> list[int]:
    """Kelvin -> [high, low] as HDL Studio stores a DALI colour temperature."""
    k = _clamp(int(kelvin), MIN_KELVIN, MAX_KELVIN)
    return [k // 256, k % 256]


def dali_colour_values(kind: str, colour=None, kelvin=None) -> list[int]:
    """Colour bytes for 0xE45C for a light colour kind.

    rgb   -> (r, g, b)
    rgbw  -> (r, g, b, w)
    rgbcw -> (r, g, b, cold, warm)   (Home Assistant RGBWW order)
    rgbwaf-> (r, g, b, w) + amber 0, free 0
    cct   -> Kelvin high, low
    """
    if kind == "cct":
        return kelvin_bytes(kelvin if kelvin is not None else 4000)
    colour = [int(_clamp(int(c), 0, 255)) for c in (colour or ())]
    want = {"rgb": 3, "rgbw": 4, "rgbcw": 5, "rgbwaf": 4}.get(kind, 3)
    colour = (colour + [0] * want)[:want]
    if kind == "rgbwaf":
        colour += [0, 0]
    return colour
