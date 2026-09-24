"""Fallback licensing module for strict (compiled-only) releases.

Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).
All rights reserved. Proprietary and confidential.

A strict release ships compiled `licensing.*.so` files and copies THIS
file over `custom_components/ar_hdl_buspro/licensing.py`. Python prefers a
matching extension module over a same-named source file, so this is only
ever imported on an interpreter/architecture the release has no binary
for. It keeps Home Assistant loading and surfaces an actionable message
instead of an ImportError traceback, and it grants nothing.

If a customer reports the "unsupported platform" repair issue, get their
Python version and architecture from the HA System page and add that pair
to the matrix in .github/workflows/build-licensing.yml.
"""

# Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).
# All rights reserved. Proprietary and confidential.
from __future__ import annotations

import logging
import platform
import sys
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.core import HomeAssistant, callback

_LOGGER = logging.getLogger(__name__)

PRODUCT = "ar_hdl_buspro"
TOKEN_PREFIX = "WIQL1."
TRIAL_DAYS = 2

DATA_LICENSE = "ar_hdl_buspro_license"
SIGNAL_LICENSE_CHANGED = "ar_hdl_buspro_license_changed"

STATUS_LICENSED = "licensed"
STATUS_TRIAL = "trial"
STATUS_TRIAL_EXPIRED = "trial_expired"
STATUS_LICENSE_EXPIRED = "license_expired"
STATUS_INVALID = "invalid"
STATUS_UNCONFIGURED = "unconfigured"
STATUS_TAMPERED = "tampered"
STATUS_UNSUPPORTED = "unsupported_platform"
STATUS_PENDING = "pending_approval"

# Mirrored from the real module so importers keep working on this platform.
ACTIVATE_PATH = "/api/activation/activate"
RENEW_INTERVAL = timedelta(hours=12)
ACTIVATION_TIMEOUT = 15


def _platform_tag() -> str:
    return (
        f"python{sys.version_info.major}.{sys.version_info.minor} "
        f"{platform.machine()}"
    )


@dataclass(frozen=True)
class LicenseState:
    """Mirror of the real state object, permanently inactive."""

    status: str
    server_id: str
    client: str | None = None
    license_id: str | None = None
    expires_at: str | None = None
    trial_days_left: int = 0
    reason: str | None = None

    @property
    def active(self) -> bool:
        """Always False: no compiled licensing module for this platform."""
        return False

    @property
    def licensed(self) -> bool:
        """Always False."""
        return False


@dataclass(frozen=True)
class UnlockToken:
    """Never minted by this module."""

    digest: bytes

    def matches(self, server_id: str, status: str) -> bool:
        """Always False."""
        return False


def verify_license_key(key: str, server_id: str) -> tuple[dict | None, str | None]:
    """Reject every key: this build cannot verify on this platform."""
    return None, "unsupported_platform"


class ARHDLLicenseManager:
    """Inert stand-in for the real manager."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Log the unsupported platform once, at load."""
        self.hass = hass
        _LOGGER.error(
            "AR HDL BUSPRO ships a compiled licensing module and this "
            "release has no binary for %s, so the integration cannot "
            "activate. Send that platform string to "
            "https://activatelicense.arsmarthome.co.za and a build will be "
            "added",
            _platform_tag(),
        )

    async def async_load(self) -> LicenseState:
        """Return the permanently inactive state."""
        return self.state

    async def async_sync_anchor(self, entry=None) -> None:
        """No-op."""

    @property
    def activation_url(self) -> str:
        """No activation is possible on an unsupported platform."""
        return ""

    async def async_set_activation_url(self, url: str) -> None:
        """No-op."""

    @property
    def last_contact(self) -> str | None:
        """Never contacted."""
        return None

    async def async_activate(self, url: str | None = None):
        """Refuse to activate."""
        return self.state, "unsupported_platform"

    async def async_renew_if_due(self) -> None:
        """No-op."""

    @property
    def server_id(self) -> str:
        """No Server ID is derived on an unsupported platform."""
        return ""

    @property
    def state(self) -> LicenseState:
        """Return the permanently inactive state."""
        return LicenseState(
            status=STATUS_UNSUPPORTED,
            server_id="",
            reason=_platform_tag(),
        )

    @property
    def unlock(self) -> UnlockToken | None:
        """Never unlocks."""
        return None

    @callback
    def evaluate(self) -> LicenseState:
        """Return the permanently inactive state."""
        return self.state

    async def async_set_key(self, key: str) -> tuple[LicenseState, str | None]:
        """Refuse to store a key."""
        return self.state, "unsupported_platform"

    async def async_clear_key(self) -> LicenseState:
        """Nothing to clear."""
        return self.state


async def async_get_manager(hass: HomeAssistant) -> ARHDLLicenseManager:
    """Return the inert manager, creating it on first use."""
    manager = hass.data.get(DATA_LICENSE)
    if manager is None:
        manager = ARHDLLicenseManager(hass)
        hass.data[DATA_LICENSE] = manager
    return manager


@callback
def is_active(hass: HomeAssistant) -> bool:
    """Always False."""
    return False


@callback
def get_unlock(hass: HomeAssistant) -> UnlockToken | None:
    """Always None."""
    return None
