"""Diagnostics support for the AR HDL BUSPRO integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import ARHDLData
from .const import (
    CONF_GATEWAY_HOST,
    CONF_LOCAL_IP,
    DOMAIN,
)

TO_REDACT = {CONF_GATEWAY_HOST, CONF_LOCAL_IP}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    data: ARHDLData | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)

    diag = {
        "entry": {
            "title": entry.title,
            "version": entry.version,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
            "unique_id": entry.unique_id,
        }
    }

    if data is not None:
        gw_info = data.gateway.diagnostics()
        # Redact the host explicitly here too in case it appears verbatim.
        diag["gateway"] = async_redact_data(gw_info, {"host", "local_ip"})

    return diag
