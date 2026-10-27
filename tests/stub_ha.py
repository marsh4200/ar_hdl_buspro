"""Minimal stand-ins for Home Assistant / voluptuous / aiohttp.

Lets the integration's HA-layer modules (light.py, climate.py,
config_flow.py) be imported and their own logic exercised on a machine
without Home Assistant installed. Only what those modules touch is modelled;
everything else resolves to a permissive dummy. Not a substitute for the
real-HA tests in tests/ha/.

Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).
"""
from __future__ import annotations

import enum
import importlib.abc
import importlib.machinery
import os
import sys
import types

_STUB_ROOTS = ("homeassistant", "voluptuous", "aiohttp", "async_timeout", "yarl")


class _Dummy:
    """Accepts any construction, call, attribute or subclassing."""

    def __init__(self, *args, **kwargs):
        self._args = args
        self._kwargs = kwargs

    def __init_subclass__(cls, **kwargs):
        pass

    def __call__(self, *args, **kwargs):
        return _Dummy(*args, **kwargs)

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _Dummy()

    def __iter__(self):
        return iter(())

    def __bool__(self):
        return True

    def __or__(self, other):
        return self

    __ror__ = __or__


class _DummyMeta(type):
    def __getattr__(cls, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _Dummy()


def _dummy_class(name):
    return _DummyMeta(name, (_Dummy,), {})


class _StubModule(types.ModuleType):
    def __call__(self, *args, **kwargs):
        # A lowercase name resolves to a stub module; it may really be a
        # function (cv.some_schema(...)), so make it callable too.
        return _Dummy(*args, **kwargs)

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        if name.isupper():
            value = name.lower()  # constants: a string is good enough
        elif name[0].islower():
            import importlib
            value = importlib.import_module(f"{self.__name__}.{name}")
        else:
            value = _dummy_class(name)
        setattr(self, name, value)
        return value


class _Finder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path, target=None):
        if fullname.split(".")[0] in _STUB_ROOTS:
            return importlib.machinery.ModuleSpec(fullname, self, is_package=True)
        return None

    def create_module(self, spec):
        mod = _StubModule(spec.name)
        mod.__path__ = []
        return mod

    def exec_module(self, module):
        _populate(module)


# ----- the parts whose real behaviour matters --------------------------------
class ColorMode(str, enum.Enum):
    ONOFF = "onoff"
    BRIGHTNESS = "brightness"
    COLOR_TEMP = "color_temp"
    RGB = "rgb"
    RGBW = "rgbw"
    RGBWW = "rgbww"
    XY = "xy"
    HS = "hs"


class HVACMode(str, enum.Enum):
    OFF = "off"
    HEAT = "heat"
    COOL = "cool"
    HEAT_COOL = "heat_cool"
    AUTO = "auto"
    DRY = "dry"
    FAN_ONLY = "fan_only"


class HVACAction(str, enum.Enum):
    OFF = "off"
    HEATING = "heating"
    COOLING = "cooling"
    DRYING = "drying"
    IDLE = "idle"
    FAN = "fan"


class ClimateEntityFeature(enum.IntFlag):
    TARGET_TEMPERATURE = 1
    FAN_MODE = 8
    PRESET_MODE = 16
    TURN_OFF = 128
    TURN_ON = 256


class Entity:
    hass = None
    _attr_has_entity_name = False

    async def async_added_to_hass(self):
        pass

    async def async_will_remove_from_hass(self):
        pass

    def async_on_remove(self, func):
        pass

    def async_write_ha_state(self):
        self.__dict__["_writes"] = self.__dict__.get("_writes", 0) + 1


class RestoreEntity(Entity):
    _last_state = None

    async def async_get_last_state(self):
        return self._last_state


class LightEntity(Entity):
    pass


class ClimateEntity(Entity):
    @property
    def min_temp(self):
        return getattr(self, "_attr_min_temp", 7)

    @property
    def max_temp(self):
        return getattr(self, "_attr_max_temp", 35)


def callback(func):
    return func


class UnitOfTemperature:
    CELSIUS = "°C"


def _populate(module):
    name = module.__name__
    if name == "homeassistant.components.light":
        module.ColorMode = ColorMode
        module.LightEntity = LightEntity
        module.ATTR_BRIGHTNESS = "brightness"
        module.ATTR_RGB_COLOR = "rgb_color"
        module.ATTR_RGBW_COLOR = "rgbw_color"
        module.ATTR_RGBWW_COLOR = "rgbww_color"
        module.ATTR_COLOR_TEMP_KELVIN = "color_temp_kelvin"
    elif name == "homeassistant.components.climate":
        module.ClimateEntity = ClimateEntity
        module.ClimateEntityFeature = ClimateEntityFeature
        module.HVACMode = HVACMode
        module.HVACAction = HVACAction
        module.FAN_AUTO = "auto"
        module.FAN_LOW = "low"
        module.FAN_MEDIUM = "medium"
        module.FAN_HIGH = "high"
    elif name == "homeassistant.helpers.entity":
        module.Entity = Entity
    elif name == "homeassistant.helpers.restore_state":
        module.RestoreEntity = RestoreEntity
    elif name == "homeassistant.core":
        module.callback = callback
    elif name == "homeassistant.const":
        module.ATTR_TEMPERATURE = "temperature"
        module.UnitOfTemperature = UnitOfTemperature
    elif name == "homeassistant.helpers.device_registry":
        module.DeviceInfo = dict


def install(pkg_dir: str):
    """Install the stubs and make the integration importable as a package.

    Returns the integration package name to import submodules from.
    """
    if not any(isinstance(f, _Finder) for f in sys.meta_path):
        sys.meta_path.insert(0, _Finder())
    root = os.path.dirname(os.path.dirname(pkg_dir))  # .../custom_components/..
    if root not in sys.path:
        sys.path.insert(0, root)
    return "custom_components." + os.path.basename(pkg_dir)
