"""Offline licence verification for AR HDL BUSPRO.

Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).
All rights reserved. Proprietary and confidential. See LICENSE.

This is the verifier half of the AR Smart Home Server licensing scheme, the
same one WorkshopIQ and the other products in the family use. It only ever
*verifies*; nothing here can mint a key. Keys are signed by the private
Ed25519 key that lives on the licence server and nowhere else.

Token format (must stay byte-identical to the server's
`app/services/signing.py`):

    WIQL1.<base64url(payload_json)>.<base64url(signature)>

where payload_json is compact, sorted-key JSON:

    {"client":..,"expires_at":..,"issued_at":..,"license_id":..,
     "product":"ar_hdl_buspro","server_id":..}

Verification is fully offline - a client site with no internet still
activates. The cost of that is there is no revocation: a key, once issued,
keeps working on the Server ID it was issued for.

The Server ID is minted once per Home Assistant install and stored in
.storage, so it survives restarts, updates and HACS upgrades. It only
changes if the integration's stored data is deleted.
"""

# Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).
# All rights reserved. Proprietary and confidential.
# Licensed software - see LICENSE in the repository root. Unauthorised
# copying, redistribution, modification, or circumvention of the licence
# check in licensing.py is prohibited.
from __future__ import annotations

import base64
import binascii
import json
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Build-time configuration
# ---------------------------------------------------------------------------
# The AR Smart Home Server signing key's PUBLIC half, from the licence
# server's "Signing key" tab. Safe to ship: it can verify signatures but
# never create them. Shared by every install - it is not per customer.
#
# Do not change this unless the licence server's private key is replaced,
# which would invalidate every licence already issued for this product.
#
# If this is ever left empty the integration runs permanently in
# trial/locked mode and logs an error, deliberately: a build that silently
# accepted everything would be a lock that does nothing.
_PUBLIC_KEY_HEX = "ecb4802e522dc2cc0a820824406ba004d4f70b9afea7e1325095d77309842f25"

PRODUCT = "ar_hdl_buspro"
TOKEN_PREFIX = "WIQL1."
TRIAL_DAYS = 2

STORAGE_KEY = "ar_hdl_buspro.license"
STORAGE_VERSION = 1

DATA_LICENSE = "ar_hdl_buspro_license"

SIGNAL_LICENSE_CHANGED = "ar_hdl_buspro_license_changed"

STATUS_LICENSED = "licensed"
STATUS_TRIAL = "trial"
STATUS_TRIAL_EXPIRED = "trial_expired"
STATUS_LICENSE_EXPIRED = "license_expired"
STATUS_INVALID = "invalid"
STATUS_UNCONFIGURED = "unconfigured"

# Statuses under which entities are allowed to work.
_ACTIVE_STATUSES = frozenset({STATUS_LICENSED, STATUS_TRIAL})


@dataclass(frozen=True)
class LicenseState:
    """The result of evaluating the stored licence at a point in time."""

    status: str
    server_id: str
    client: str | None = None
    license_id: str | None = None
    expires_at: str | None = None
    trial_days_left: int = 0
    reason: str | None = None

    @property
    def active(self) -> bool:
        """Return True if the integration is allowed to operate."""
        return self.status in _ACTIVE_STATUSES

    @property
    def licensed(self) -> bool:
        """Return True if a valid perpetual/unexpired key is installed."""
        return self.status == STATUS_LICENSED


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------
def _b64u_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _parse_expiry(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def verify_license_key(key: str, server_id: str) -> tuple[dict | None, str | None]:
    """Verify a licence key against this install.

    Returns (payload, None) when the key is genuine, unexpired and issued
    for this product and Server ID, or (None, reason) otherwise. `reason`
    is a translation key from strings.json, not a user-facing sentence.
    """
    if not _PUBLIC_KEY_HEX:
        _LOGGER.error(
            "No licence public key is baked into this build of AR HDL BUSPRO "
            "(_PUBLIC_KEY_HEX in licensing.py is empty). No key can be "
            "accepted until a release is built with it set"
        )
        return None, "no_public_key"

    key = (key or "").strip()
    if not key.startswith(TOKEN_PREFIX):
        return None, "invalid_key"

    try:
        payload_b64, signature_b64 = key[len(TOKEN_PREFIX):].split(".")
        payload_bytes = _b64u_decode(payload_b64)
        signature = _b64u_decode(signature_b64)
    except (ValueError, binascii.Error):
        return None, "invalid_key"

    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )

        public_key = Ed25519PublicKey.from_public_bytes(
            bytes.fromhex(_PUBLIC_KEY_HEX)
        )
        public_key.verify(signature, payload_bytes)
    except InvalidSignature:
        return None, "invalid_key"
    except Exception:  # noqa: BLE001 - a malformed key must never raise out
        _LOGGER.debug("Licence signature check failed", exc_info=True)
        return None, "invalid_key"

    try:
        payload = json.loads(payload_bytes)
    except ValueError:
        return None, "invalid_key"

    if payload.get("product") != PRODUCT:
        return None, "wrong_product"

    if str(payload.get("server_id", "")).lower() != server_id.lower():
        return None, "wrong_server"

    expires = _parse_expiry(payload.get("expires_at"))
    if expires is not None and datetime.now(timezone.utc) >= expires:
        return None, "expired"

    return payload, None


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------
class ARHDLLicenseManager:
    """Owns the Server ID, the stored key and the trial clock.

    One instance per Home Assistant install (not per config entry) - the
    licence covers the install, so two gateways on one HA share it.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialise the manager."""
        self.hass = hass
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict = {}
        self._state: LicenseState | None = None

    # ----- lifecycle -------------------------------------------------------
    async def async_load(self) -> LicenseState:
        """Load (or create) stored licence data and evaluate it."""
        stored = await self._store.async_load()
        self._data = dict(stored) if stored else {}

        dirty = False
        if not self._data.get("server_id"):
            self._data["server_id"] = secrets.token_hex(16)
            dirty = True
        if not self._data.get("trial_started"):
            self._data["trial_started"] = datetime.now(timezone.utc).isoformat()
            dirty = True
        if dirty:
            await self._store.async_save(self._data)

        return self.evaluate()

    # ----- accessors -------------------------------------------------------
    @property
    def server_id(self) -> str:
        """Return this install's Server ID (hex, give this to the operator)."""
        return self._data.get("server_id", "")

    @property
    def state(self) -> LicenseState:
        """Return the last evaluated state, evaluating once if needed."""
        if self._state is None:
            return self.evaluate()
        return self._state

    # ----- evaluation ------------------------------------------------------
    @callback
    def evaluate(self) -> LicenseState:
        """Recompute the licence state from stored data and the clock."""
        server_id = self.server_id
        stored_key = self._data.get("license_key")

        if stored_key:
            payload, reason = verify_license_key(stored_key, server_id)
            if payload is not None:
                state = LicenseState(
                    status=STATUS_LICENSED,
                    server_id=server_id,
                    client=payload.get("client"),
                    license_id=payload.get("license_id"),
                    expires_at=payload.get("expires_at"),
                )
            elif reason == "expired":
                state = LicenseState(
                    status=STATUS_LICENSE_EXPIRED,
                    server_id=server_id,
                    reason=reason,
                )
            else:
                state = LicenseState(
                    status=STATUS_INVALID, server_id=server_id, reason=reason
                )
        else:
            state = self._evaluate_trial(server_id)

        self._state = state
        self.hass.data[DATA_LICENSE] = self
        return state

    def _evaluate_trial(self, server_id: str) -> LicenseState:
        """Evaluate the 2-day demo window."""
        started = _parse_expiry(self._data.get("trial_started"))
        if started is None:
            # Unparseable/absent start - treat as starting now rather than
            # locking someone out over a corrupt timestamp.
            started = datetime.now(timezone.utc)

        ends = started + timedelta(days=TRIAL_DAYS)
        remaining = ends - datetime.now(timezone.utc)

        if remaining.total_seconds() <= 0:
            return LicenseState(
                status=STATUS_TRIAL_EXPIRED, server_id=server_id, trial_days_left=0
            )

        # Round up so the last partial day still reads as "1 day left".
        days_left = max(1, -(-int(remaining.total_seconds()) // 86400))
        return LicenseState(
            status=STATUS_TRIAL, server_id=server_id, trial_days_left=days_left
        )

    # ----- mutation --------------------------------------------------------
    async def async_set_key(self, key: str) -> tuple[LicenseState, str | None]:
        """Validate and persist a licence key.

        Returns (state, error_reason). Nothing is stored if the key does not
        verify - a bad paste must never overwrite a working licence.
        """
        payload, reason = verify_license_key(key, self.server_id)
        if payload is None:
            return self.state, reason

        self._data["license_key"] = key.strip()
        await self._store.async_save(self._data)
        return self.evaluate(), None

    async def async_clear_key(self) -> LicenseState:
        """Remove the stored licence key (back to trial/locked)."""
        self._data.pop("license_key", None)
        await self._store.async_save(self._data)
        return self.evaluate()


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------
async def async_get_manager(hass: HomeAssistant) -> ARHDLLicenseManager:
    """Return the install-wide licence manager, creating it on first use."""
    manager = hass.data.get(DATA_LICENSE)
    if manager is None:
        manager = ARHDLLicenseManager(hass)
        hass.data[DATA_LICENSE] = manager
        await manager.async_load()
    return manager


@callback
def is_active(hass: HomeAssistant) -> bool:
    """Cheap synchronous check used by entity availability.

    Reads the last evaluated state; the hourly re-check in __init__.py is
    what moves it when the demo window runs out.
    """
    manager: ARHDLLicenseManager | None = hass.data.get(DATA_LICENSE)
    if manager is None:
        # Manager not loaded yet - don't blank out entities mid-startup.
        return True
    return manager.state.active
