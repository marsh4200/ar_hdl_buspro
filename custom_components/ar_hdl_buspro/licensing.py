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


Server ID (v2)
--------------
The Server ID is derived deterministically from Home Assistant's own
instance UUID (`.storage/core.uuid`), not minted randomly into this
integration's own store. Deleting `.storage/ar_hdl_buspro.license`
therefore reproduces the *same* Server ID, so:

  * an installed key keeps working after a storage reset, and
  * a storage reset no longer mints a fresh demo window.

Installs created before v2 have a random Server ID already pinned in their
store; that value is kept verbatim so keys issued against it stay valid.
See `_async_resolve_server_id`.


Trial anchor
------------
The demo window's start is written to three independent places (this
integration's store, a second store, and every config entry's data). The
*earliest* anchor found wins, and a present-but-unreadable anchor is
treated as long expired rather than as "starts now" - corrupting the
timestamp locks rather than unlocks. A monotonic high-water mark is kept
alongside it so winding the system clock backwards does not extend the
window.


What this module cannot do
--------------------------
Everything here runs on the customer's machine from source they can read.
None of it is a cryptographic barrier against a determined, competent
attacker: the signature check cannot be forged, but it can be *removed*.
The layers below (functional gating at the telegram send path, the
integrity manifest, the compiled build) exist to raise the cost of that
removal from a one-line edit to a deliberate, multi-file, documented act -
which is what the LICENCE, not the code, is ultimately enforced on.
"""

# Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).
# All rights reserved. Proprietary and confidential.
# Licensed software - see LICENSE in the repository root. Unauthorised
# copying, redistribution, modification, or circumvention of the licence
# check in licensing.py is prohibited.
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

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

# Second, deliberately dull-looking store holding a copy of the trial
# anchor. Someone clearing "the licence file" to reset the demo window
# generally does not clear this one too.
SHADOW_STORAGE_KEY = "ar_hdl_buspro.runtime"
SHADOW_STORAGE_VERSION = 1

# Key under which the anchor is mirrored into each config entry's data.
# Config entries live in .storage/core.config_entries - deleting that file
# destroys the user's entire Home Assistant configuration, so this copy is
# the one that is genuinely awkward to remove.
ENTRY_ANCHOR_KEY = "rt_anchor"

DATA_LICENSE = "ar_hdl_buspro_license"

SIGNAL_LICENSE_CHANGED = "ar_hdl_buspro_license_changed"

STATUS_LICENSED = "licensed"
STATUS_TRIAL = "trial"
STATUS_TRIAL_EXPIRED = "trial_expired"
STATUS_LICENSE_EXPIRED = "license_expired"
STATUS_INVALID = "invalid"
STATUS_UNCONFIGURED = "unconfigured"
STATUS_TAMPERED = "tampered"
# Server ID reached the licence server but is not approved yet.
STATUS_PENDING = "pending_approval"

# ---------------------------------------------------------------------------
# Online activation
# ---------------------------------------------------------------------------
# The licence server mints SHORT-LIVED keys and the install renews them in
# the background. That single decision provides both halves of what the
# offline-only scheme could not do:
#
#   * revocation - stop renewing a Server ID and its key simply expires, and
#   * an offline grace period - a site that loses internet keeps running
#     until the key it already holds runs out.
#
# So the grace window IS the key lifetime; there is no separate "last seen"
# clock to be tampered with, and no second verification path. A renewal
# response is just another Ed25519-signed key checked the same way as one
# pasted in by hand, which is why pointing the install at a hostile URL
# gains nothing.
ACTIVATE_PATH = "/api/activation/activate"

# How often to try a renewal. Deliberately far shorter than the key
# lifetime: a site with flaky internet gets many chances to catch up before
# anything expires.
RENEW_INTERVAL = timedelta(hours=12)

# Give up on a single HTTP attempt quickly - this runs in the background and
# must never hold up setup.
ACTIVATION_TIMEOUT = 15

# Statuses under which entities are allowed to work.
_ACTIVE_STATUSES = frozenset({STATUS_LICENSED, STATUS_TRIAL})

# Only persist a new high-water mark this often, so a normal running
# system is not rewriting .storage every evaluation.
_HIGH_WATER_WRITE_INTERVAL = timedelta(hours=6)

# Domain separation for the unlock token handed to the telegram send path.
_GATE_SALT = b"ar_hdl_buspro/gate/v2"


# ---------------------------------------------------------------------------
# Integrity manifest
# ---------------------------------------------------------------------------
# Release builds ship a generated `_integrity.py` holding SHA-256 digests of
# the gated modules (see build/gen_manifest.py). A working tree without it
# runs normally, so development is unaffected; a *release* without it is a
# build mistake and is reported loudly rather than silently unlocking.
def _integrity_failure() -> str | None:
    """Return the name of the first altered gated module, or None."""
    try:
        from . import _integrity  # type: ignore[attr-defined]
    except ImportError:
        return None

    try:
        import os

        base = os.path.dirname(os.path.abspath(__file__))
        for relpath, expected in _integrity.MANIFEST.items():
            full = os.path.join(base, relpath)
            try:
                with open(full, "rb") as handle:
                    digest = hashlib.sha256(handle.read()).hexdigest()
            except OSError:
                return relpath
            if not hmac.compare_digest(digest, expected):
                return relpath
    except Exception:  # noqa: BLE001 - a broken manifest must not crash setup
        _LOGGER.debug("Integrity manifest check failed to run", exc_info=True)
        return None

    return None


# ---------------------------------------------------------------------------
# Unlock token
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class UnlockToken:
    """Capability object handed to the telegram send path when active.

    This is not a cryptographic secret - everything needed to derive it is
    in this file. Its job is to make the enforcement point a value that has
    to be *produced* rather than a boolean that can be flipped, so that
    disabling the licence means understanding and editing three cooperating
    modules instead of changing one `return` statement.
    """

    digest: bytes

    def matches(self, server_id: str, status: str) -> bool:
        """Return True if this token was minted for this install and status."""
        return hmac.compare_digest(self.digest, _gate_digest(server_id, status))


def _gate_digest(server_id: str, status: str) -> bytes:
    """Derive the unlock digest for an install/status pair."""
    return hashlib.blake2s(
        _GATE_SALT
        + bytes.fromhex(_PUBLIC_KEY_HEX or "00")
        + server_id.encode()
        + b"|"
        + status.encode(),
        digest_size=16,
    ).digest()


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------
def _b64u_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


_VERSION_CACHE: str | None = None


def _integration_version() -> str:
    """Return this integration's version from manifest.json.

    Sent with every activation call so the licence server can see what each
    site is running - useful for support, and for spotting an install that
    stopped updating.
    """
    global _VERSION_CACHE  # noqa: PLW0603
    if _VERSION_CACHE is None:
        try:
            import os

            manifest = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "manifest.json"
            )
            with open(manifest, encoding="utf-8") as handle:
                _VERSION_CACHE = str(json.load(handle).get("version", "unknown"))
        except Exception:  # noqa: BLE001
            _VERSION_CACHE = "unknown"
    return _VERSION_CACHE


def _client_timeout(seconds: int):
    """Return an aiohttp timeout, or None if aiohttp is unavailable."""
    try:
        import aiohttp

        return aiohttp.ClientTimeout(total=seconds)
    except ImportError:  # pragma: no cover - aiohttp ships with HA
        return None


def verify_license_key(
    key: str, server_id: str, now: datetime | None = None
) -> tuple[dict | None, str | None]:
    """Verify a licence key against this install.

    Returns (payload, None) when the key is genuine, unexpired and issued
    for this product and Server ID, or (None, reason) otherwise. `reason`
    is a translation key from strings.json, not a user-facing sentence.

    `now` lets the caller pass the clock-floored time (see
    `ARHDLLicenseManager._effective_now`). With short-lived renewable keys
    that matters: without it, winding the host clock backwards would extend
    a key that has already run out.
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

    expires = _parse_dt(payload.get("expires_at"))
    if expires is not None and (now or datetime.now(timezone.utc)) >= expires:
        return None, "expired"

    return payload, None


# ---------------------------------------------------------------------------
# Server ID
# ---------------------------------------------------------------------------
# Sentinel written into the store the first time a v2-derived Server ID is
# used, so a later load can tell "derived" from "pinned legacy value".
_SERVER_ID_SCHEME = "server_id_scheme"
_SCHEME_DERIVED = "instance-v2"


def _derive_server_id(instance_uuid: str) -> str:
    """Derive a stable Server ID from Home Assistant's instance UUID.

    Deterministic, so it survives deletion of this integration's store, and
    one-way, so the Server ID a customer emails over does not disclose the
    HA instance UUID itself.
    """
    return hashlib.sha256(
        b"ar_hdl_buspro/server-id/v2|" + instance_uuid.encode()
    ).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Trial anchor
# ---------------------------------------------------------------------------
# A "corrupt" marker that sorts earlier than any real timestamp, so an
# unparseable anchor expires the demo window instead of restarting it.
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


@dataclass
class _Anchor:
    """Resolved trial anchor: when the window opened, and how far the
    clock has ever been seen to advance."""

    started: datetime
    high_water: datetime

    def as_dict(self, server_id: str) -> dict[str, str]:
        """Serialise for storage, bound to the install it was minted on."""
        return {
            "sid": server_id,
            "ts": self.started.isoformat(),
            "hw": self.high_water.isoformat(),
        }


def _read_anchor(raw: Any, server_id: str) -> _Anchor | None:
    """Interpret one stored anchor record.

    Returns None when the slot is genuinely empty, and an *expired* anchor
    when the slot is populated but unreadable or bound to another install.
    That asymmetry is the point: blanking the field must not be a way to
    restart the demo window.
    """
    if raw in (None, "", {}):
        return None

    if not isinstance(raw, dict):
        return _Anchor(started=_EPOCH, high_water=_EPOCH)

    sid = str(raw.get("sid", "")).lower()
    if sid and sid != server_id.lower():
        # Anchor copied in from another install - do not honour it as a
        # fresh window.
        return _Anchor(started=_EPOCH, high_water=_EPOCH)

    started = _parse_dt(raw.get("ts"))
    if started is None:
        return _Anchor(started=_EPOCH, high_water=_EPOCH)

    high_water = _parse_dt(raw.get("hw")) or started
    return _Anchor(started=started, high_water=max(started, high_water))


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
        self._shadow: Store = Store(hass, SHADOW_STORAGE_VERSION, SHADOW_STORAGE_KEY)
        self._data: dict = {}
        self._shadow_data: dict = {}
        self._state: LicenseState | None = None
        self._anchor: _Anchor | None = None
        self._server_id: str = ""
        self._tampered: str | None = None
        self._last_hw_write: datetime | None = None

    # ----- lifecycle -------------------------------------------------------
    async def async_load(self) -> LicenseState:
        """Load (or create) stored licence data and evaluate it."""
        self._tampered = _integrity_failure()
        if self._tampered:
            _LOGGER.error(
                "AR HDL BUSPRO integrity check failed for %s: this build has "
                "been modified. The integration will stay locked. Reinstall "
                "from HACS to restore it",
                self._tampered,
            )

        stored = await self._store.async_load()
        self._data = dict(stored) if stored else {}
        shadow = await self._shadow.async_load()
        self._shadow_data = dict(shadow) if shadow else {}

        self._server_id = await self._async_resolve_server_id()
        self._anchor = await self._async_resolve_anchor()

        await self._async_persist_anchor(force=True)
        return self.evaluate()

    async def _async_resolve_server_id(self) -> str:
        """Return this install's Server ID, honouring legacy pinned values."""
        pinned = self._data.get("server_id")
        scheme = self._data.get(_SERVER_ID_SCHEME)

        # A pinned value from a pre-v2 install: keep it verbatim, forever.
        # Keys already issued to that customer are bound to it.
        if pinned and scheme != _SCHEME_DERIVED:
            return str(pinned)

        try:
            from homeassistant.helpers import instance_id

            uuid = await instance_id.async_get(self.hass)
        except Exception:  # noqa: BLE001 - never block setup on this
            _LOGGER.debug("Could not read the HA instance UUID", exc_info=True)
            uuid = ""

        if not uuid:
            # Fall back to whatever is already pinned rather than minting a
            # new identity that would invalidate an installed key.
            return str(pinned or "")

        derived = _derive_server_id(uuid)
        if pinned != derived or scheme != _SCHEME_DERIVED:
            self._data["server_id"] = derived
            self._data[_SERVER_ID_SCHEME] = _SCHEME_DERIVED
            await self._store.async_save(self._data)
        return derived

    async def _async_resolve_anchor(self) -> _Anchor:
        """Collect every stored anchor and reduce them to one.

        Earliest start wins and the latest high-water mark wins, so adding
        storage locations can only ever shorten a demo window, never
        lengthen one.
        """
        server_id = self._server_id
        candidates: list[_Anchor] = []

        for raw in (
            self._data.get("trial_anchor"),
            self._shadow_data.get("trial_anchor"),
            *(
                entry.data.get(ENTRY_ANCHOR_KEY)
                for entry in self.hass.config_entries.async_entries(PRODUCT)
            ),
        ):
            anchor = _read_anchor(raw, server_id)
            if anchor is not None:
                candidates.append(anchor)

        # Honour a pre-v2 `trial_started` string if that is all there is.
        legacy = _parse_dt(self._data.get("trial_started"))
        if legacy is not None:
            candidates.append(_Anchor(started=legacy, high_water=legacy))
        elif self._data.get("trial_started") is not None:
            candidates.append(_Anchor(started=_EPOCH, high_water=_EPOCH))

        now = datetime.now(timezone.utc)
        if not candidates:
            # Genuinely new install.
            return _Anchor(started=now, high_water=now)

        started = min(anchor.started for anchor in candidates)
        high_water = max(
            [anchor.high_water for anchor in candidates] + [started]
        )
        return _Anchor(started=started, high_water=high_water)

    async def _async_persist_anchor(self, force: bool = False) -> None:
        """Write the resolved anchor back to every storage location."""
        if self._anchor is None:
            return

        now = datetime.now(timezone.utc)
        if not force and self._last_hw_write is not None:
            if now - self._last_hw_write < _HIGH_WATER_WRITE_INTERVAL:
                return
        self._last_hw_write = now

        record = self._anchor.as_dict(self._server_id)

        if self._data.get("trial_anchor") != record:
            self._data["trial_anchor"] = record
            self._data.pop("trial_started", None)
            await self._store.async_save(self._data)

        if self._shadow_data.get("trial_anchor") != record:
            self._shadow_data["trial_anchor"] = record
            await self._shadow.async_save(self._shadow_data)

        # The config-entry mirror deliberately carries only the immutable
        # half of the anchor (install + start), never the moving high-water
        # mark. Rewriting entry data fires the entry's update listener,
        # which reloads the integration; a field that changes every few
        # hours would turn that into a reload loop.
        entry_record = {"sid": record["sid"], "ts": record["ts"]}
        for entry in self.hass.config_entries.async_entries(PRODUCT):
            existing = entry.data.get(ENTRY_ANCHOR_KEY)
            if isinstance(existing, dict) and (
                existing.get("sid"),
                existing.get("ts"),
            ) == (entry_record["sid"], entry_record["ts"]):
                continue
            self.hass.config_entries.async_update_entry(
                entry, data={**entry.data, ENTRY_ANCHOR_KEY: entry_record}
            )

    async def async_sync_anchor(self, entry: ConfigEntry | None = None) -> None:
        """Re-mirror the anchor, e.g. after a new config entry is added."""
        await self._async_persist_anchor(force=True)

    # ----- accessors -------------------------------------------------------
    @property
    def server_id(self) -> str:
        """Return this install's Server ID (hex, give this to the operator)."""
        return self._server_id

    @property
    def state(self) -> LicenseState:
        """Return the last evaluated state, evaluating once if needed."""
        if self._state is None:
            return self.evaluate()
        return self._state

    @property
    def unlock(self) -> UnlockToken | None:
        """Return the capability token when operation is permitted."""
        state = self.state
        if not state.active:
            return None
        return UnlockToken(digest=_gate_digest(self._server_id, state.status))

    # ----- evaluation ------------------------------------------------------
    @callback
    def evaluate(self) -> LicenseState:
        """Recompute the licence state from stored data and the clock."""
        server_id = self._server_id

        if self._tampered:
            state = LicenseState(
                status=STATUS_TAMPERED,
                server_id=server_id,
                reason="tampered",
            )
            self._state = state
            self.hass.data[DATA_LICENSE] = self
            return state

        stored_key = self._data.get("license_key")

        if stored_key:
            payload, reason = verify_license_key(
                stored_key, server_id, now=self._effective_now()
            )
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

    @callback
    def _effective_now(self) -> datetime:
        """Return the current time, floored at the highest ever observed.

        Winding the host clock backwards must not hand back demo days that
        have already been spent.
        """
        now = datetime.now(timezone.utc)
        if self._anchor is None:
            return now
        if now < self._anchor.high_water:
            return self._anchor.high_water
        self._anchor.high_water = now
        self.hass.async_create_task(self._async_persist_anchor())
        return now

    def _evaluate_trial(self, server_id: str) -> LicenseState:
        """Evaluate the 2-day demo window."""
        if self._anchor is None:
            # Manager not loaded - fail closed rather than granting a window.
            return LicenseState(
                status=STATUS_TRIAL_EXPIRED, server_id=server_id, trial_days_left=0
            )

        ends = self._anchor.started + timedelta(days=TRIAL_DAYS)
        remaining = ends - self._effective_now()

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
        if self._tampered:
            return self.state, "tampered"

        payload, reason = verify_license_key(
            key, self.server_id, now=self._effective_now()
        )
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

    # ----- online activation ----------------------------------------------
    @property
    def activation_url(self) -> str:
        """Return the configured licence server base URL."""
        return str(self._data.get("activation_url") or "").strip()

    async def async_set_activation_url(self, url: str) -> None:
        """Persist the licence server base URL (blank clears it)."""
        url = (url or "").strip().rstrip("/")
        if url:
            self._data["activation_url"] = url
        else:
            self._data.pop("activation_url", None)
        await self._store.async_save(self._data)

    @property
    def contact_name(self) -> str:
        """Return the requester's name sent with activation requests."""
        return str(self._data.get("contact_name") or "").strip()

    @property
    def contact_email(self) -> str:
        """Return the requester's email sent with activation requests."""
        return str(self._data.get("contact_email") or "").strip()

    @property
    def has_contact(self) -> bool:
        """Return True once a name and email have been recorded."""
        return bool(self.contact_name and self.contact_email)

    async def async_set_contact(self, name: str, email: str) -> None:
        """Persist the requester's name and email."""
        self._data["contact_name"] = (name or "").strip()
        self._data["contact_email"] = (email or "").strip()
        await self._store.async_save(self._data)

    @property
    def last_contact(self) -> str | None:
        """Return when the licence server was last reached, if ever."""
        return self._data.get("last_contact")

    async def async_activate(
        self, url: str | None = None
    ) -> tuple[LicenseState, str | None]:
        """Ask the licence server for a key for this Server ID.

        Used both for the installer pressing "activate" and for the
        background renewal - they are the same request, because a renewal
        is just another issue for the same Server ID.

        Returns (state, reason). `reason` is None on success, otherwise a
        translation key: `pending_approval` when the Server ID has reached
        the server but is not approved, `activation_refused` when the
        server declined it outright, `cannot_reach_server` on any network
        or protocol failure.
        """
        if self._tampered:
            return self.state, "tampered"

        base = (url or self.activation_url or "").strip().rstrip("/")
        if not base:
            return self.state, "no_activation_url"

        try:
            from homeassistant.helpers.aiohttp_client import (
                async_get_clientsession,
            )

            session = async_get_clientsession(self.hass)
            async with session.post(
                base + ACTIVATE_PATH,
                json={
                    "server_id": self.server_id,
                    "product": PRODUCT,
                    "version": _integration_version(),
                    "name": self.contact_name,
                    "email": self.contact_email,
                },
                timeout=_client_timeout(ACTIVATION_TIMEOUT),
            ) as response:
                if response.status in (404, 405, 501):
                    # Reached a server, but not one with an activation
                    # endpoint - almost always the URL pointing at the
                    # licence *portal* (which only collects requests) rather
                    # than the licence server, or the server not having the
                    # activation endpoint deployed yet.
                    _LOGGER.error(
                        "No AR Smart Home activation endpoint at %s%s (HTTP "
                        "%s). That address answered, but it does not offer "
                        "activation - check it points at the licence server, "
                        "not the request portal",
                        base,
                        ACTIVATE_PATH,
                        response.status,
                    )
                    return self.state, "no_activation_endpoint"
                if response.status == 429:
                    # Checked very recently. This is emphatically NOT a
                    # refusal - an installer who presses the button twice
                    # must not be told their licence was declined.
                    return self.state, "checked_recently"
                if response.status != 200:
                    _LOGGER.warning(
                        "Licence server at %s answered HTTP %s",
                        base,
                        response.status,
                    )
                    return self.state, "cannot_reach_server"
                body = await response.json(content_type=None)
        except Exception as err:  # noqa: BLE001 - offline is the normal case
            _LOGGER.debug("Licence activation call to %s failed: %s", base, err)
            return self.state, "cannot_reach_server"

        if not isinstance(body, dict):
            return self.state, "cannot_reach_server"

        # The server was reached; record that even when it says "not yet",
        # so the status screen can show when contact last happened.
        self._data["last_contact"] = datetime.now(timezone.utc).isoformat()

        status = str(body.get("status", "")).lower()

        if status == "issued":
            key = str(body.get("license_key") or "")
            payload, reason = verify_license_key(
                key, self.server_id, now=self._effective_now()
            )
            if payload is None:
                # Signed by the wrong key, for the wrong install, or already
                # expired. Keep whatever is already stored.
                _LOGGER.warning(
                    "The licence server returned a key this install cannot "
                    "use (%s)", reason
                )
                await self._store.async_save(self._data)
                return self.state, reason or "invalid_key"

            self._data["license_key"] = key.strip()
            if base != self.activation_url:
                self._data["activation_url"] = base
            await self._store.async_save(self._data)
            return self.evaluate(), None

        await self._store.async_save(self._data)

        if status == "pending":
            state = LicenseState(
                status=STATUS_PENDING,
                server_id=self.server_id,
                reason="pending_approval",
            )
            # Do not overwrite a working licence with "pending" - a renewal
            # for an install awaiting a *renewal* approval keeps running on
            # the key it already holds until that key expires.
            if not self.state.active:
                self._state = state
            return self.state, "pending_approval"

        if status == "denied":
            # The ONLY case that is a genuine refusal. Everything else that
            # can go wrong on the way - wrong URL, portal instead of licence
            # server, endpoint not deployed, proxy in the way - must not be
            # reported to an installer as "your licence was declined", which
            # sends them chasing a licensing problem that does not exist.
            reason = str(body.get("reason") or "")
            _LOGGER.warning(
                "Licence server declined this install (%s)", reason or "no reason given"
            )
            return self.state, "activation_refused"

        _LOGGER.error(
            "Unexpected reply from the licence server at %s: %r. Expected a "
            "status of issued, pending or denied",
            base,
            body,
        )
        return self.state, "cannot_reach_server"

    async def async_renew_if_due(self) -> None:
        """Background renewal. Never raises, never blocks setup."""
        if self._tampered or not self.activation_url:
            return
        # Nothing to renew for an install that has never activated; the
        # installer has to make the first call deliberately.
        if not self._data.get("license_key"):
            return
        await self.async_activate()


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

    Fails *closed*: callers run only after `async_get_manager` has been
    awaited in `async_setup_entry`, so a missing manager means something
    has gone wrong rather than "startup is still in progress".
    """
    manager: ARHDLLicenseManager | None = hass.data.get(DATA_LICENSE)
    if manager is None:
        return False
    return manager.state.active


@callback
def get_unlock(hass: HomeAssistant) -> UnlockToken | None:
    """Return the current unlock token, or None when operation is barred."""
    manager: ARHDLLicenseManager | None = hass.data.get(DATA_LICENSE)
    if manager is None:
        return None
    return manager.unlock
