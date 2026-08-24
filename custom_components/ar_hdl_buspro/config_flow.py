"""Config flow for the AR HDL BUSPRO integration."""
from __future__ import annotations

import re
import asyncio
import logging
import uuid
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    BINARY_KIND_DRY_CONTACT,
    BINARY_KIND_MOTION,
    BINARY_KIND_SINGLE_CHANNEL,
    BINARY_KIND_UNIVERSAL_SWITCH,
    BINARY_KINDS,
    CLIMATE_PRESETS,
    CONF_BINARY_KIND,
    CONF_CHANNEL,
    CONF_CLOSE_CHANNEL,
    CONF_COVER_MODE,
    CONF_CURTAIN_NUMBER,
    CONF_DEVICE_HW_KIND,
    CONF_DEVICE_ID,
    CONF_DEVICE_TYPE,
    CONF_DEVICES,
    CONF_DIMMABLE,
    CONF_DIMMER_CODES,
    CONF_DISCOVERED,
    CONF_GATEWAY_HOST,
    CONF_GATEWAY_PORT,
    CONF_LOCAL_IP,
    CONF_NAME,
    CONF_OPEN_CHANNEL,
    CONF_PRESET_MODES,
    CONF_RELAY_CHANNEL,
    CONF_RELAY_DEVICE,
    CONF_RELAY_SUBNET,
    CONF_RUNNING_TIME,
    CONF_SCAN_DURATION,
    CONF_SCAN_INTERVAL,
    CONF_SENSOR_KIND,
    CONF_SPLIT_CHANNELS,
    CONF_SUB_NUMBER,
    CONF_SUBNET_ID,
    CONF_TEMP_FAHRENHEIT,
    CONF_TEMP_OFFSET,
    CONF_TRAVEL_TIME,
    COVER_MODE_CURTAIN_MODULE,
    COVER_MODES,
    DEFAULT_TRAVEL_TIME,
    DEFAULT_PORT,
    DEFAULT_RUNNING_TIME,
    DEFAULT_SCAN_DURATION,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TEMP_OFFSET,
    DEVICE_HW_GENERIC,
    DEVICE_HW_12IN1,
    DEVICE_HW_KINDS,
    DEVICE_HW_SENSORS_IN_ONE,
    DEVICE_TYPE_BINARY_SENSOR,
    DEVICE_TYPE_CLIMATE,
    DEVICE_TYPE_COVER,
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_SENSOR,
    DEVICE_TYPE_SWITCH,
    DEVICE_TYPES,
    DOMAIN,
    HDL_DIMMER_TYPE_CODES,
    HDL_KEYPAD_TYPE_CODES,
    HDL_TYPE_NAMES,
    HDL_TYPE_TO_DEVICE_TYPE,
    MAX_SCAN_DURATION,
    MIN_SCAN_DURATION,
    PRESET_NONE,
    ROLE_KEYPAD,
    SENSOR_KIND_ILLUMINANCE,
    SENSOR_KIND_TEMPERATURE,
    SENSOR_KINDS,
)

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schemas (built dynamically since they depend on selectors/defaults)
# ---------------------------------------------------------------------------
def _gateway_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Schema for the gateway-setup step."""
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_GATEWAY_HOST,
                default=defaults.get(CONF_GATEWAY_HOST, vol.UNDEFINED),
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            ),
            vol.Required(
                CONF_GATEWAY_PORT,
                default=defaults.get(CONF_GATEWAY_PORT, DEFAULT_PORT),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=65535, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_LOCAL_IP, default=defaults.get(CONF_LOCAL_IP, "")
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            ),
        }
    )


def _common_address_fields(defaults: dict[str, Any]) -> dict:
    """Subnet/device/name fields used by most device-add forms."""
    return {
        vol.Required(
            CONF_NAME, default=defaults.get(CONF_NAME, vol.UNDEFINED)
        ): str,
        vol.Required(
            CONF_SUBNET_ID, default=defaults.get(CONF_SUBNET_ID, 1)
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, max=255, mode=selector.NumberSelectorMode.BOX
            )
        ),
        vol.Required(
            CONF_DEVICE_ID, default=defaults.get(CONF_DEVICE_ID, 1)
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, max=255, mode=selector.NumberSelectorMode.BOX
            )
        ),
    }


def _light_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            **_common_address_fields(defaults),
            vol.Required(
                CONF_CHANNEL, default=defaults.get(CONF_CHANNEL, 1)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=255, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_DIMMABLE, default=defaults.get(CONF_DIMMABLE, True)
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_RUNNING_TIME,
                default=defaults.get(CONF_RUNNING_TIME, DEFAULT_RUNNING_TIME),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=3600, mode=selector.NumberSelectorMode.BOX
                )
            ),
        }
    )


def _switch_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            **_common_address_fields(defaults),
            vol.Required(
                CONF_CHANNEL, default=defaults.get(CONF_CHANNEL, 1)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=255, mode=selector.NumberSelectorMode.BOX
                )
            ),
        }
    )


def _sensor_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            **_common_address_fields(defaults),
            vol.Required(
                CONF_SENSOR_KIND,
                default=defaults.get(CONF_SENSOR_KIND, SENSOR_KINDS[0]),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=SENSOR_KINDS,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key=CONF_SENSOR_KIND,
                )
            ),
            vol.Optional(
                CONF_DEVICE_HW_KIND,
                default=defaults.get(CONF_DEVICE_HW_KIND, DEVICE_HW_GENERIC),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=DEVICE_HW_KINDS,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key=CONF_DEVICE_HW_KIND,
                )
            ),
            vol.Optional(
                CONF_TEMP_OFFSET,
                default=defaults.get(CONF_TEMP_OFFSET, DEFAULT_TEMP_OFFSET),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=-50, max=50, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_TEMP_FAHRENHEIT,
                default=defaults.get(CONF_TEMP_FAHRENHEIT, False),
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=defaults.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=3600, mode=selector.NumberSelectorMode.BOX
                )
            ),
        }
    )


def _binary_sensor_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            **_common_address_fields(defaults),
            vol.Required(
                CONF_BINARY_KIND,
                default=defaults.get(CONF_BINARY_KIND, BINARY_KINDS[0]),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=BINARY_KINDS,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key=CONF_BINARY_KIND,
                )
            ),
            vol.Optional(
                CONF_SUB_NUMBER, default=defaults.get(CONF_SUB_NUMBER, 0)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=255, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=defaults.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=3600, mode=selector.NumberSelectorMode.BOX
                )
            ),
        }
    )



def _cover_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            **_common_address_fields(defaults),
            vol.Required(
                CONF_COVER_MODE,
                default=defaults.get(CONF_COVER_MODE, COVER_MODE_CURTAIN_MODULE),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=COVER_MODES,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key=CONF_COVER_MODE,
                )
            ),
            vol.Optional(
                CONF_CURTAIN_NUMBER,
                default=defaults.get(CONF_CURTAIN_NUMBER, 1),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=32, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_OPEN_CHANNEL,
                default=defaults.get(CONF_OPEN_CHANNEL, 1),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=64, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_CLOSE_CHANNEL,
                default=defaults.get(CONF_CLOSE_CHANNEL, 2),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=64, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_TRAVEL_TIME,
                default=defaults.get(CONF_TRAVEL_TIME, DEFAULT_TRAVEL_TIME),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=600, mode=selector.NumberSelectorMode.BOX
                )
            ),
        }
    )

def _climate_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            **_common_address_fields(defaults),
            vol.Optional(
                CONF_PRESET_MODES,
                default=defaults.get(CONF_PRESET_MODES, [PRESET_NONE]),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=CLIMATE_PRESETS,
                    mode=selector.SelectSelectorMode.LIST,
                    multiple=True,
                    translation_key="climate_preset",
                )
            ),
            vol.Optional(
                CONF_RELAY_SUBNET, default=defaults.get(CONF_RELAY_SUBNET, 0)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=255, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_RELAY_DEVICE, default=defaults.get(CONF_RELAY_DEVICE, 0)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=255, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_RELAY_CHANNEL, default=defaults.get(CONF_RELAY_CHANNEL, 0)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=255, mode=selector.NumberSelectorMode.BOX
                )
            ),
        }
    )


DEVICE_SCHEMA_BUILDERS = {
    DEVICE_TYPE_LIGHT: _light_schema,
    DEVICE_TYPE_SWITCH: _switch_schema,
    DEVICE_TYPE_SENSOR: _sensor_schema,
    DEVICE_TYPE_BINARY_SENSOR: _binary_sensor_schema,
    DEVICE_TYPE_COVER: _cover_schema,
    DEVICE_TYPE_CLIMATE: _climate_schema,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _normalize_device_input(
    device_type: str, user_input: dict[str, Any]
) -> dict[str, Any]:
    """Coerce selector values (which may be floats) into the right types."""
    out = dict(user_input)
    out[CONF_DEVICE_TYPE] = device_type
    int_fields = (
        CONF_SUBNET_ID,
        CONF_DEVICE_ID,
        CONF_CHANNEL,
        CONF_RUNNING_TIME,
        CONF_SCAN_INTERVAL,
        CONF_TEMP_OFFSET,
        CONF_SUB_NUMBER,
        CONF_CURTAIN_NUMBER,
        CONF_OPEN_CHANNEL,
        CONF_CLOSE_CHANNEL,
        CONF_TRAVEL_TIME,
        CONF_RELAY_SUBNET,
        CONF_RELAY_DEVICE,
        CONF_RELAY_CHANNEL,
    )
    for field in int_fields:
        if field in out and out[field] is not None and out[field] != "":
            try:
                out[field] = int(out[field])
            except (TypeError, ValueError):
                pass
    return out


def _device_summary(device: dict[str, Any]) -> str:
    """Build a short human-readable label for the device list."""
    dtype = device.get(CONF_DEVICE_TYPE, "?")
    name = device.get(CONF_NAME, "?")
    sub = device.get(CONF_SUBNET_ID, "?")
    dev = device.get(CONF_DEVICE_ID, "?")
    ch = device.get(CONF_CHANNEL)
    addr = f"{sub}.{dev}" + (f".{ch}" if ch is not None else "")
    return f"{name} [{dtype} @ {addr}]"


def _validate_gateway_inputs(host: str, port: int) -> str | None:
    """Validate gateway inputs without touching the network.

    Returns an error key (matching strings.json) or None when inputs are OK.
    Note: we deliberately do NOT try to "ping" the gateway here -- HDL Buspro
    is connectionless UDP, so there is no handshake we could test, and a local
    socket bind only proves the local port is free (which is unrelated to
    whether the gateway is reachable).
    """
    import socket

    if not host:
        return "invalid_host"
    # Accept both hostnames and IPs.
    try:
        socket.getaddrinfo(host, None)
    except socket.gaierror:
        return "invalid_host"
    if not (1 <= port <= 65535):
        return "invalid_port"
    return None


# ---------------------------------------------------------------------------
# ConfigFlow
# ---------------------------------------------------------------------------
CHOICE_MANUAL = "manual"
CHOICE_RESCAN = "rescan"
CONF_GATEWAY_CHOICE = "gateway_choice"
GATEWAY_DISCOVERY_TIMEOUT = 3.0


class ARHDLConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for AR HDL BUSPRO."""

    VERSION = 2

    def __init__(self) -> None:
        """Initialize the flow."""
        self._discovered_gateways: list[Any] = []
        self._prefill: dict[str, Any] = {}

    async def _async_run_gateway_discovery(self) -> None:
        """Probe UDP/6000 for HDL gateways on the local network."""
        from .gateway_discovery import async_discover_gateways

        try:
            self._discovered_gateways = await async_discover_gateways(
                self.hass, timeout=GATEWAY_DISCOVERY_TIMEOUT
            )
        except Exception as err:  # noqa: BLE001 - discovery must never block setup
            _LOGGER.warning("HDL gateway auto-detection failed: %s", err)
            self._discovered_gateways = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """First step: auto-detect gateways on UDP/6000 and offer a pick list."""
        if user_input is not None:
            choice = user_input[CONF_GATEWAY_CHOICE]
            if choice == CHOICE_RESCAN:
                # Re-entering the step with no input triggers a fresh scan.
                self._discovered_gateways = []
                return await self.async_step_user()
            if choice == CHOICE_MANUAL:
                self._prefill = {}
                return await self.async_step_manual()
            # choice is a discovered gateway IP -> prefill the details form.
            self._prefill = {
                CONF_GATEWAY_HOST: choice,
                CONF_GATEWAY_PORT: DEFAULT_PORT,
            }
            return await self.async_step_manual()

        await self._async_run_gateway_discovery()
        if not self._discovered_gateways:
            # Nothing answered the broadcast probe (different L2 segment,
            # blocked broadcasts, ...) -- fall back to manual entry.
            return await self.async_step_manual()

        options = [
            selector.SelectOptionDict(value=gw.ip, label=gw.label)
            for gw in self._discovered_gateways
        ]
        options.append(
            selector.SelectOptionDict(value=CHOICE_RESCAN, label="Scan again")
        )
        options.append(
            selector.SelectOptionDict(
                value=CHOICE_MANUAL, label="Enter address manually"
            )
        )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_GATEWAY_CHOICE,
                        default=self._discovered_gateways[0].ip,
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
            description_placeholders={
                "count": str(len(self._discovered_gateways))
            },
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Enter/confirm the gateway details."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_GATEWAY_HOST].strip()
            port = int(user_input[CONF_GATEWAY_PORT])
            local_ip = user_input.get(CONF_LOCAL_IP, "").strip()

            # Validate the inputs only. HDL Buspro is connectionless UDP --
            # there is no handshake we can use to "ping" a gateway here, and a
            # local socket bind test only proves the local port is free (which
            # is unrelated to whether the gateway is reachable). The actual
            # bind happens in async_setup_entry, where ConfigEntryNotReady
            # gives us proper retry semantics.
            validation_error = _validate_gateway_inputs(host, port)
            if validation_error is not None:
                errors["base"] = validation_error
            else:
                # Prevent duplicates on the same gateway host:port pair.
                await self.async_set_unique_id(f"{host}:{port}")
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"AR HDL BUSPRO ({host})",
                    data={
                        CONF_GATEWAY_HOST: host,
                        CONF_GATEWAY_PORT: port,
                        CONF_LOCAL_IP: local_ip,
                    },
                    options={CONF_DEVICES: []},
                )

        return self.async_show_form(
            step_id="manual",
            data_schema=_gateway_schema(user_input or self._prefill),
            errors=errors,
        )

    async def async_step_import(
        self, import_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Import a legacy YAML/buspro config entry into ar_hdl_buspro."""
        return await self.async_step_manual(import_data)

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow handler."""
        return ARHDLOptionsFlow(entry)


# ---------------------------------------------------------------------------
# OptionsFlow - the heart of the no-YAML experience
# ---------------------------------------------------------------------------
class ARHDLOptionsFlow(OptionsFlow):
    """Options flow allowing users to manage everything from the UI."""

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize options flow."""
        # NOTE: do not assign self.config_entry directly; recent HA versions
        # provide it automatically via the OptionsFlow base class and warn on
        # explicit assignment. We keep a private reference instead.
        self._entry = entry
        self._editing_device_id: str | None = None
        self._adding_device_type: str | None = None
        self._scan_results: list[Any] = []
        self._scan_task: asyncio.Task | None = None
        self._scan_started: float = 0.0
        self._scan_duration: int = DEFAULT_SCAN_DURATION
        self._scan_error: str | None = None

    # ----- helpers ---------------------------------------------------------
    @property
    def devices(self) -> list[dict[str, Any]]:
        """Return the current list of configured devices."""
        return list(self._entry.options.get(CONF_DEVICES, []))

    def _save_devices(self, devices: list[dict[str, Any]]):
        """Persist a new device list as the options payload."""
        return self.async_create_entry(
            title="", data={**self._entry.options, CONF_DEVICES: devices}
        )

    # ----- main menu -------------------------------------------------------
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the main options menu."""
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "gateway",
                "detect_gateway",
                "scan_bus",
                "add_device",
                "edit_device",
                "remove_device",
            ],
        )

    # ----- gateway settings -------------------------------------------------
    async def async_step_gateway(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit gateway connection settings."""
        errors: dict[str, str] = {}

        if user_input is not None:
            new_data = {
                CONF_GATEWAY_HOST: user_input[CONF_GATEWAY_HOST].strip(),
                CONF_GATEWAY_PORT: int(user_input[CONF_GATEWAY_PORT]),
                CONF_LOCAL_IP: user_input.get(CONF_LOCAL_IP, "").strip(),
            }
            validation_error = _validate_gateway_inputs(
                new_data[CONF_GATEWAY_HOST], new_data[CONF_GATEWAY_PORT]
            )
            if validation_error is not None:
                errors["base"] = validation_error
                return self.async_show_form(
                    step_id="gateway",
                    data_schema=_gateway_schema({**self._entry.data, **user_input}),
                    errors=errors,
                )
            self.hass.config_entries.async_update_entry(
                self._entry,
                data={**self._entry.data, **new_data},
                title=f"AR HDL BUSPRO ({new_data[CONF_GATEWAY_HOST]})",
            )
            # Saving options (even empty) triggers the update listener -> reload.
            return self.async_create_entry(title="", data=dict(self._entry.options))

        return self.async_show_form(
            step_id="gateway",
            data_schema=_gateway_schema(self._entry.data),
            errors=errors,
        )

    # ----- gateway auto-detection ------------------------------------------
    async def async_step_detect_gateway(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Probe the network for HDL gateways and let the user pick one."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_GATEWAY_CHOICE]
            self.hass.config_entries.async_update_entry(
                self._entry,
                data={
                    **self._entry.data,
                    CONF_GATEWAY_HOST: host,
                    CONF_GATEWAY_PORT: DEFAULT_PORT,
                },
                title=f"AR HDL BUSPRO ({host})",
            )
            # Saving options triggers the update listener -> reload with the
            # new gateway (and a freshly resolved source-IP filter).
            return self.async_create_entry(title="", data=dict(self._entry.options))

        from .gateway_discovery import async_discover_gateways

        try:
            gateways = await async_discover_gateways(
                self.hass, timeout=GATEWAY_DISCOVERY_TIMEOUT
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("HDL gateway auto-detection failed: %s", err)
            gateways = []
        if not gateways:
            return self.async_abort(reason="no_gateways_found")

        current = self._entry.data.get(CONF_GATEWAY_HOST)
        options = [
            selector.SelectOptionDict(
                value=gw.ip,
                label=gw.label + ("  ✓ current" if gw.ip == current else ""),
            )
            for gw in gateways
        ]
        return self.async_show_form(
            step_id="detect_gateway",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_GATEWAY_CHOICE, default=gateways[0].ip
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
            errors=errors,
            description_placeholders={"count": str(len(gateways))},
        )

    # ----- bus scan: discover devices automatically ------------------------
    def _live_buspro(self):
        """Return the connected Buspro client for this entry, or None."""
        data = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)
        gateway = getattr(data, "gateway", None)
        if gateway is None or not gateway.available:
            return None
        return gateway.hdl

    async def async_step_scan_bus(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Scan the bus for devices, with a live countdown while it runs."""
        errors: dict[str, str] = {}
        if self._scan_error:
            errors["base"] = self._scan_error
            self._scan_error = None

        if self._scan_task is None and user_input is not None:
            buspro = self._live_buspro()
            if buspro is None:
                return self.async_abort(reason="gateway_unavailable")

            self._scan_duration = int(
                user_input.get(CONF_SCAN_DURATION, DEFAULT_SCAN_DURATION)
            )

            # Import lazily so a missing/edited library can't break the import
            # of the whole config flow module.
            from .discovery import SCAN_TIMEOUT_MARGIN, BusScanner

            scanner = BusScanner(buspro)
            self._scan_started = self.hass.loop.time()
            # The real scan runs independently in the background; the flow
            # only needs to know when it's done to move on.
            self._scan_task = self.hass.async_create_task(
                asyncio.wait_for(
                    scanner.scan(self._scan_duration),
                    timeout=self._scan_duration + SCAN_TIMEOUT_MARGIN,
                ),
                name=f"{DOMAIN} bus scan",
            )

        if self._scan_task is not None:
            if not self._scan_task.done():
                elapsed = self.hass.loop.time() - self._scan_started
                seconds_left = max(0, round(self._scan_duration - elapsed))
                # A throwaway 1s task just paces the countdown tick - it's not
                # the scan itself, so a slow render doesn't delay the scan and
                # the scan finishing early doesn't wait on this tick.
                return self.async_show_progress(
                    step_id="scan_bus",
                    progress_action="bus_scan",
                    description_placeholders={"seconds_left": str(seconds_left)},
                    progress_task=self.hass.async_create_task(asyncio.sleep(1)),
                )

            task, self._scan_task = self._scan_task, None
            try:
                self._scan_results = task.result()
            except (asyncio.TimeoutError, RuntimeError, OSError) as err:
                _LOGGER.warning("AR HDL BUSPRO bus scan failed: %s", err)
                self._scan_error = "scan_failed"
                return self.async_show_progress_done(next_step_id="scan_bus_retry")

            if not self._scan_results:
                return self.async_show_progress_done(
                    next_step_id="scan_bus_no_devices"
                )
            return self.async_show_progress_done(next_step_id="scan_results")

        return self.async_show_form(
            step_id="scan_bus",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_DURATION, default=DEFAULT_SCAN_DURATION
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=MIN_SCAN_DURATION,
                            max=MAX_SCAN_DURATION,
                            step=1,
                            unit_of_measurement="s",
                            mode=selector.NumberSelectorMode.SLIDER,
                        )
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_scan_bus_retry(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-show the scan form after a failed scan attempt."""
        return await self.async_step_scan_bus(user_input)

    async def async_step_scan_bus_no_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Abort after a scan that found nothing."""
        return self.async_abort(reason="no_devices_found")

    @staticmethod
    def _parse_dimmer_codes(text: str | None) -> set[str]:
        """Parse user-entered dimmer type codes into normalized '0xABCD' form.

        Accepts comma/space/semicolon separated tokens with or without the 0x
        prefix (e.g. '0x0602, 26D 0x120b'). Invalid tokens are ignored.
        """
        codes: set[str] = set()
        if not text:
            return codes
        for token in re.split(r"[\s,;]+", text.strip()):
            if not token:
                continue
            raw = token.lower().removeprefix("0x")
            if not raw or len(raw) > 4:
                continue
            try:
                value = int(raw, 16)
            except ValueError:
                continue
            codes.add(f"0x{value:04X}")
        return codes

    def _saved_dimmer_codes(self) -> set[str]:
        """Return dimmer type codes previously saved into the entry options."""
        saved = self._entry.options.get(CONF_DIMMER_CODES, [])
        if isinstance(saved, str):
            return self._parse_dimmer_codes(saved)
        return {str(c) for c in saved}

    def _infer_device_type(
        self, disc, dimmer_codes: set[str] | None = None
    ) -> str:
        """Decide an ar_hdl_buspro device_type from what the device replied with.

        Reply operate codes are the most reliable signal (they work even when
        the raw type code isn't in our table). Fall back to the type-code map,
        then to a plain switch.
        """
        if dimmer_codes is None:
            dimmer_codes = self._saved_dimmer_codes()
        ops = disc.op_codes
        if (
            "ReadSensorStatusResponse" in ops
            or "ReadSensorsInOneStatusResponse" in ops
        ):
            return DEVICE_TYPE_SENSOR
        if "ReadFloorHeatingStatusResponse" in ops:
            return DEVICE_TYPE_CLIMATE
        if "ReadDryContactStatusResponse" in ops:
            return DEVICE_TYPE_BINARY_SENSOR
        # Curtain modules answer the curtain-status probe (0xE3E3) or echo
        # keypad open/close commands (0xE3E1). Either is definitive - check
        # before the generic channel branch, since some curtain modules also
        # answer ReadStatusOfChannels and would otherwise land on switch.
        if (
            "ReadStatusOfCurtainSwitchResponse" in ops
            or "CurtainSwitchControlResponse" in ops
        ):
            return DEVICE_TYPE_COVER
        # Keypads/panels: known type codes, or the traffic pattern (sends
        # control telegrams, never answers a channel read). Checked after the
        # sensor/climate ops above so DLPs still land on climate.
        if disc.type_code in HDL_KEYPAD_TYPE_CODES or getattr(
            disc, "looks_like_keypad", False
        ):
            return ROLE_KEYPAD
        if (
            "ReadStatusOfChannelsResponse" in ops
            or "SingleChannelControlResponse" in ops
        ):
            # Channel device. Dimmer if the type code is known (built-in table
            # or user-pinned codes), or if the scan saw an intermediate
            # brightness level; otherwise a switch (user can flip it later).
            if (
                disc.type_code in HDL_DIMMER_TYPE_CODES
                or disc.type_code in dimmer_codes
                or getattr(disc, "dimmer_evidence", False)
            ):
                return DEVICE_TYPE_LIGHT
            return HDL_TYPE_TO_DEVICE_TYPE.get(disc.type_code, DEVICE_TYPE_SWITCH)
        return HDL_TYPE_TO_DEVICE_TYPE.get(disc.type_code, DEVICE_TYPE_SWITCH)

    def _discovery_label(self, disc, role: str, already: bool) -> str:
        """Build the checklist label for a discovered device."""
        parts = [disc.address, role]
        if disc.type_code and disc.type_code != "0x0000":
            parts.append(disc.type_code)
        if disc.channel_count:
            parts.append(f"{disc.channel_count}ch")
        # Prefer the vendored enum name; otherwise fall back to our own table so
        # identified-but-unenumerated hardware still reads sensibly.
        friendly = (
            disc.type_name
            if disc.type_name and disc.type_name != "Unknown"
            else HDL_TYPE_NAMES.get(disc.type_code)
        )
        if friendly:
            parts.append(friendly)
        label = "  ".join(parts[:2]) + "  ·  " + " · ".join(parts[2:])
        if role == ROLE_KEYPAD:
            label += "  ·  buttons only, no entities"
        if already:
            label += "  \u2713 in config"
        return label

    async def async_step_scan_results(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick which discovered devices to import."""
        results = self._scan_results
        if not results:
            return self.async_abort(reason="no_devices_found")

        # Mark addresses that already exist in the config so the user can tell
        # what is new (we still allow re-importing -- it never deletes anything).
        existing_addrs = {
            (d.get(CONF_SUBNET_ID), d.get(CONF_DEVICE_ID)) for d in self.devices
        }

        options: list[selector.SelectOptionDict] = []
        for disc in results:
            role = self._infer_device_type(disc)
            already = (disc.subnet_id, disc.device_id) in existing_addrs
            options.append(
                selector.SelectOptionDict(
                    value=disc.key,
                    label=self._discovery_label(disc, role, already),
                )
            )

        if user_input is not None:
            chosen_keys = set(user_input.get(CONF_DISCOVERED, []))
            split = bool(user_input.get(CONF_SPLIT_CHANNELS, True))
            # Dimmer codes: whatever the user typed wins for this import and is
            # persisted for every future scan/import.
            dimmer_codes = self._parse_dimmer_codes(
                user_input.get(CONF_DIMMER_CODES, "")
            )
            by_key = {disc.key: disc for disc in results}
            devices = self.devices
            # Existing (subnet, device, channel) tuples, so a re-scan only fills
            # gaps instead of creating duplicates of what is already configured.
            existing_triples = {
                (
                    d.get(CONF_SUBNET_ID),
                    d.get(CONF_DEVICE_ID),
                    # Covers key on curtain number instead of channel; fall
                    # through so a re-scan recognises them and fills gaps
                    # instead of duplicating.
                    d.get(CONF_CHANNEL, d.get(CONF_CURTAIN_NUMBER)),
                )
                for d in devices
            }
            added = 0
            skipped_keypads = 0
            for key in chosen_keys:
                disc = by_key.get(key)
                if disc is None:
                    continue
                dtype = self._infer_device_type(disc, dimmer_codes)
                if dtype == ROLE_KEYPAD:
                    # Keypads have no controllable channels; importing one as
                    # a switch just creates a dead entity. Skip and count.
                    skipped_keypads += 1
                    continue
                if dtype == DEVICE_TYPE_SENSOR:
                    # A multi-sensor is one physical device but several HA
                    # entities. Import the full bundle (temperature + lux +
                    # motion) so the user isn't left hand-adding the rest.
                    added += self._import_sensor_bundle(disc, devices)
                    continue
                channel_device = dtype in (
                    DEVICE_TYPE_LIGHT,
                    DEVICE_TYPE_SWITCH,
                    DEVICE_TYPE_COVER,
                )
                if split and channel_device and disc.channel_count:
                    channels = range(1, disc.channel_count + 1)
                else:
                    channels = (None,)
                for channel in channels:
                    eff_channel = channel if channel is not None else (
                        1 if channel_device else None
                    )
                    if (
                        disc.subnet_id,
                        disc.device_id,
                        eff_channel,
                    ) in existing_triples:
                        continue
                    devices.append(
                        self._build_imported_device(disc, dtype, channel)
                    )
                    existing_triples.add(
                        (disc.subnet_id, disc.device_id, eff_channel)
                    )
                    added += 1
            if skipped_keypads:
                _LOGGER.info(
                    "AR HDL BUSPRO import: skipped %d keypad(s) (buttons only, "
                    "no entities to create)",
                    skipped_keypads,
                )
            _LOGGER.info("AR HDL BUSPRO import: added %d new device entr(y/ies)", added)
            return self.async_create_entry(
                title="",
                data={
                    **self._entry.options,
                    CONF_DEVICES: devices,
                    CONF_DIMMER_CODES: sorted(dimmer_codes),
                },
            )

        new_count = sum(
            1
            for disc in results
            if (disc.subnet_id, disc.device_id) not in existing_addrs
        )
        return self.async_show_form(
            step_id="scan_results",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_DISCOVERED, default=[]
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            mode=selector.SelectSelectorMode.LIST,
                            multiple=True,
                        )
                    ),
                    vol.Optional(
                        CONF_SPLIT_CHANNELS, default=True
                    ): selector.BooleanSelector(),
                    vol.Optional(
                        CONF_DIMMER_CODES,
                        default=", ".join(sorted(self._saved_dimmer_codes())),
                    ): selector.TextSelector(),
                }
            ),
            description_placeholders={
                "found": str(len(results)),
                "new": str(new_count),
            },
        )

    def _import_sensor_bundle(
        self, disc, devices: list[dict[str, Any]]
    ) -> int:
        """Append a full entity bundle for a discovered multi-sensor.

        One physical HDL sensor (12in1 / 8in1 / sensors-in-one) surfaces as
        three HA entities: temperature sensor, illuminance sensor and motion
        binary sensor. They share the (subnet, device) address so HA groups
        them under one device. Existing entries of the same kind are left
        alone so a re-scan never duplicates. Returns how many were added.
        """
        subnet, device = disc.subnet_id, disc.device_id
        name = f"HDL {disc.address}"
        # Hardware kind from the type code; this drives payload decoding
        # (e.g. the 12in1's -20 temperature offset on auto broadcasts).
        hw_kind = {
            "0x0134": DEVICE_HW_12IN1,        # SB_CMS_12in1
            "0x0150": DEVICE_HW_SENSORS_IN_ONE,  # HDL_MSP07M
        }.get(disc.type_code, DEVICE_HW_GENERIC)
        # Poll every 60s so readings arrive even when the sensor doesn't
        # broadcast on its own; broadcasts still update instantly.
        scan = 60

        def have(dev_type: str, kind_key: str, kind_val: str) -> bool:
            return any(
                d.get(CONF_SUBNET_ID) == subnet
                and d.get(CONF_DEVICE_ID) == device
                and d.get(CONF_DEVICE_TYPE) == dev_type
                and d.get(kind_key) == kind_val
                for d in devices
            )

        added = 0
        for kind in (SENSOR_KIND_TEMPERATURE, SENSOR_KIND_ILLUMINANCE):
            if have(DEVICE_TYPE_SENSOR, CONF_SENSOR_KIND, kind):
                continue
            devices.append(
                {
                    "id": uuid.uuid4().hex,
                    CONF_DEVICE_TYPE: DEVICE_TYPE_SENSOR,
                    CONF_NAME: name,
                    CONF_SUBNET_ID: subnet,
                    CONF_DEVICE_ID: device,
                    CONF_SENSOR_KIND: kind,
                    CONF_DEVICE_HW_KIND: hw_kind,
                    CONF_TEMP_OFFSET: DEFAULT_TEMP_OFFSET,
                    CONF_SCAN_INTERVAL: scan,
                }
            )
            added += 1
        if not have(
            DEVICE_TYPE_BINARY_SENSOR, CONF_BINARY_KIND, BINARY_KIND_MOTION
        ):
            devices.append(
                {
                    "id": uuid.uuid4().hex,
                    CONF_DEVICE_TYPE: DEVICE_TYPE_BINARY_SENSOR,
                    CONF_NAME: name,
                    CONF_SUBNET_ID: subnet,
                    CONF_DEVICE_ID: device,
                    CONF_BINARY_KIND: BINARY_KIND_MOTION,
                    CONF_SUB_NUMBER: 0,
                    CONF_SCAN_INTERVAL: scan,
                }
            )
            added += 1
        return added

    @staticmethod
    def _build_imported_device(
        disc, dtype: str, channel: int | None = None
    ) -> dict[str, Any]:
        """Build a minimal valid device dict for an imported discovery.

        Required addressing is filled from the scan; type-specific details get
        safe defaults the user can refine in the edit form afterwards. When a
        channel is supplied, the name is suffixed so per-channel entries are
        distinguishable.
        """
        name = f"HDL {disc.address}"
        if channel is not None:
            name = f"HDL {disc.address} ch{channel}"
        base: dict[str, Any] = {
            "id": uuid.uuid4().hex,
            CONF_DEVICE_TYPE: dtype,
            CONF_NAME: name,
            CONF_SUBNET_ID: disc.subnet_id,
            CONF_DEVICE_ID: disc.device_id,
        }
        if dtype == DEVICE_TYPE_LIGHT:
            base.update(
                {
                    CONF_CHANNEL: channel or 1,
                    CONF_DIMMABLE: True,
                    CONF_RUNNING_TIME: DEFAULT_RUNNING_TIME,
                }
            )
        elif dtype == DEVICE_TYPE_SWITCH:
            base[CONF_CHANNEL] = channel or 1
        elif dtype == DEVICE_TYPE_COVER:
            # Discovered curtain hardware is always a real curtain module
            # (CurtainSwitchControl open/close/stop), never a relay pair -
            # relay-pair curtains are a wiring convention the user sets up by
            # hand. The per-channel split maps onto curtain numbers.
            base.update(
                {
                    CONF_COVER_MODE: COVER_MODE_CURTAIN_MODULE,
                    CONF_CURTAIN_NUMBER: channel or 1,
                }
            )
        elif dtype == DEVICE_TYPE_SENSOR:
            base.update(
                {
                    CONF_SENSOR_KIND: SENSOR_KINDS[0],
                    CONF_DEVICE_HW_KIND: DEVICE_HW_GENERIC,
                    CONF_TEMP_OFFSET: DEFAULT_TEMP_OFFSET,
                    CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                }
            )
        elif dtype == DEVICE_TYPE_BINARY_SENSOR:
            base.update(
                {
                    CONF_BINARY_KIND: BINARY_KINDS[0],
                    CONF_SUB_NUMBER: 0,
                    CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                }
            )
        elif dtype == DEVICE_TYPE_CLIMATE:
            base.update(
                {
                    CONF_PRESET_MODES: [PRESET_NONE],
                    CONF_RELAY_SUBNET: 0,
                    CONF_RELAY_DEVICE: 0,
                    CONF_RELAY_CHANNEL: 0,
                }
            )
        return base

    # ----- add device: pick a type, then show the matching form ------------
    async def async_step_add_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick which kind of device to add."""
        if user_input is not None:
            self._adding_device_type = user_input[CONF_DEVICE_TYPE]
            return await self.async_step_add_device_form()

        return self.async_show_form(
            step_id="add_device",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_DEVICE_TYPE, default=DEVICE_TYPE_LIGHT
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=DEVICE_TYPES,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                            translation_key=CONF_DEVICE_TYPE,
                        )
                    )
                }
            ),
        )

    async def async_step_add_device_form(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the type-specific add form."""
        dtype = self._adding_device_type or DEVICE_TYPE_LIGHT
        schema_builder = DEVICE_SCHEMA_BUILDERS[dtype]

        if user_input is not None:
            device = _normalize_device_input(dtype, user_input)
            device["id"] = uuid.uuid4().hex
            devices = self.devices
            devices.append(device)
            return self._save_devices(devices)

        return self.async_show_form(
            step_id="add_device_form",
            data_schema=schema_builder({}),
            description_placeholders={"device_type": dtype},
        )

    # ----- edit device: pick one, then show the matching form --------------
    async def async_step_edit_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick an existing device to edit."""
        devices = self.devices
        if not devices:
            return self.async_abort(reason="no_devices")

        options = [
            selector.SelectOptionDict(value=d["id"], label=_device_summary(d))
            for d in devices
        ]

        if user_input is not None:
            self._editing_device_id = user_input["device_id_choice"]
            return await self.async_step_edit_device_form()

        return self.async_show_form(
            step_id="edit_device",
            data_schema=vol.Schema(
                {
                    vol.Required("device_id_choice"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_edit_device_form(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the type-specific edit form."""
        devices = self.devices
        target = next(
            (d for d in devices if d["id"] == self._editing_device_id), None
        )
        if target is None:
            return self.async_abort(reason="device_not_found")

        dtype = target[CONF_DEVICE_TYPE]
        schema_builder = DEVICE_SCHEMA_BUILDERS[dtype]

        if user_input is not None:
            updated = _normalize_device_input(dtype, user_input)
            updated["id"] = target["id"]
            new_devices = [
                updated if d["id"] == target["id"] else d for d in devices
            ]
            return self._save_devices(new_devices)

        return self.async_show_form(
            step_id="edit_device_form",
            data_schema=schema_builder(target),
            description_placeholders={
                "device_type": dtype,
                "device_name": target.get(CONF_NAME, ""),
            },
        )

    # ----- remove device ---------------------------------------------------
    async def async_step_remove_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove a configured device."""
        devices = self.devices
        if not devices:
            return self.async_abort(reason="no_devices")

        options = [
            selector.SelectOptionDict(value=d["id"], label=_device_summary(d))
            for d in devices
        ]

        if user_input is not None:
            target_id = user_input["device_id_choice"]
            new_devices = [d for d in devices if d["id"] != target_id]
            return self._save_devices(new_devices)

        return self.async_show_form(
            step_id="remove_device",
            data_schema=vol.Schema(
                {
                    vol.Required("device_id_choice"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )
