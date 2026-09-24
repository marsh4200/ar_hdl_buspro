"""Test harness for the AR HDL BUSPRO licensing module.

Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).
All rights reserved. Proprietary and confidential.

Run from the repository root:

    python3 tests/test_licensing.py

Works against either licensing.py or a compiled licensing.*.so - Python
picks the extension module when one matches, which is exactly what a
release build wants verified.

Stubs just enough of Home Assistant to exercise the real module.
"""
from __future__ import annotations

import asyncio
import base64
import copy
import json
import sys
import types
from datetime import datetime, timedelta, timezone

# --------------------------------------------------------------------------
# Home Assistant stubs
# --------------------------------------------------------------------------
ha = types.ModuleType("homeassistant")
core = types.ModuleType("homeassistant.core")
helpers = types.ModuleType("homeassistant.helpers")
storage = types.ModuleType("homeassistant.helpers.storage")
instance_id_mod = types.ModuleType("homeassistant.helpers.instance_id")
config_entries = types.ModuleType("homeassistant.config_entries")

INSTANCE_UUID = "6f1c9a0e8b7d4f2a9c3e5d7b1a0f4e82"

FAKE_DISK: dict[str, dict] = {}


def callback(fn):
    return fn


class HomeAssistant:
    def __init__(self):
        self.data = {}
        self.config_entries = FakeEntryManager()
        self._tasks = []

    def async_create_task(self, coro):
        task = asyncio.ensure_future(coro)
        self._tasks.append(task)
        return task

    async def drain(self):
        while self._tasks:
            pending = self._tasks
            self._tasks = []
            await asyncio.gather(*pending, return_exceptions=True)


class FakeEntry:
    def __init__(self, entry_id, data=None):
        self.entry_id = entry_id
        self.data = data or {}


class FakeEntryManager:
    def __init__(self):
        self.entries = []

    def async_entries(self, domain):
        return list(self.entries)

    def async_update_entry(self, entry, data=None):
        if data is not None:
            entry.data = data


class Store:
    def __init__(self, hass, version, key):
        self.key = key

    async def async_load(self):
        return copy.deepcopy(FAKE_DISK.get(self.key))

    async def async_save(self, data):
        FAKE_DISK[self.key] = copy.deepcopy(data)


async def _async_get_instance_id(hass):
    return INSTANCE_UUID


core.HomeAssistant = HomeAssistant
core.callback = callback
storage.Store = Store
instance_id_mod.async_get = _async_get_instance_id
helpers.instance_id = instance_id_mod
config_entries.ConfigEntry = FakeEntry

sys.modules.update({
    "homeassistant": ha,
    "homeassistant.core": core,
    "homeassistant.helpers": helpers,
    "homeassistant.helpers.storage": storage,
    "homeassistant.helpers.instance_id": instance_id_mod,
    "homeassistant.config_entries": config_entries,
})

import os

# Repo-relative, so this runs from a checkout: tests/ -> repo root -> package.
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "custom_components", "ar_hdl_buspro"))
import licensing  # noqa: E402

# --------------------------------------------------------------------------
# Test signing key
# --------------------------------------------------------------------------
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey,
)

PRIV = Ed25519PrivateKey.generate()
PUB_HEX = PRIV.public_key().public_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PublicFormat.Raw,
).hex()
licensing._PUBLIC_KEY_HEX = PUB_HEX


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def mint(server_id, expires_at=None, product="ar_hdl_buspro", client="Test Lodge"):
    payload = {
        "client": client,
        "expires_at": expires_at,
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "license_id": "LIC-0001",
        "product": product,
        "server_id": server_id,
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return f"WIQL1.{b64u(raw)}.{b64u(PRIV.sign(raw))}"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name}" + (f"  -- {detail}" if detail else ""))


async def fresh_hass(entries=1):
    hass = HomeAssistant()
    for i in range(entries):
        hass.config_entries.entries.append(FakeEntry(f"entry{i}"))
    return hass


async def load(hass):
    mgr = licensing.ARHDLLicenseManager(hass)
    state = await mgr.async_load()
    await hass.drain()
    return mgr, state


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------
async def main():
    global FAKE_DISK

    # 1. Fresh install
    FAKE_DISK = {}
    hass = await fresh_hass()
    mgr, state = await load(hass)
    check("fresh install is in trial", state.status == "trial", state.status)
    check("fresh trial has 2 days", state.trial_days_left == 2,
          str(state.trial_days_left))
    sid1 = mgr.server_id
    check("server id is 32 hex chars", len(sid1) == 32 and all(
        c in "0123456789abcdef" for c in sid1), sid1)

    # 2. Determinism
    check("server id is derived from instance uuid",
          sid1 == licensing._derive_server_id(INSTANCE_UUID))

    # 3. Deleting the primary store does not reset the trial
    disk_backup = copy.deepcopy(FAKE_DISK)
    entries_backup = copy.deepcopy(hass.config_entries.entries[0].data)
    # wind the anchor back so the window is already spent
    spent = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    for key in ("ar_hdl_buspro.license", "ar_hdl_buspro.runtime"):
        FAKE_DISK[key]["trial_anchor"]["ts"] = spent
    hass.config_entries.entries[0].data["rt_anchor"]["ts"] = spent

    del FAKE_DISK["ar_hdl_buspro.license"]
    hass2 = HomeAssistant()
    hass2.config_entries.entries = hass.config_entries.entries
    mgr2, state2 = await load(hass2)
    check("primary store deleted -> same server id", mgr2.server_id == sid1)
    check("primary store deleted -> trial stays expired",
          state2.status == "trial_expired", state2.status)

    # 4. Both stores deleted, entry anchor alone holds the line
    FAKE_DISK.pop("ar_hdl_buspro.license", None)
    FAKE_DISK.pop("ar_hdl_buspro.runtime", None)
    hass3 = HomeAssistant()
    hass3.config_entries.entries = hass.config_entries.entries
    mgr3, state3 = await load(hass3)
    check("both stores deleted -> trial still expired (entry anchor)",
          state3.status == "trial_expired", state3.status)

    # 5. Corrupting the timestamp locks rather than unlocks
    FAKE_DISK = {}
    hass4 = await fresh_hass()
    mgr4, s4 = await load(hass4)
    check("baseline before corruption is trial", s4.status == "trial")
    FAKE_DISK["ar_hdl_buspro.license"]["trial_anchor"]["ts"] = "not-a-date"
    FAKE_DISK["ar_hdl_buspro.runtime"]["trial_anchor"]["ts"] = "not-a-date"
    hass4.config_entries.entries[0].data["rt_anchor"]["ts"] = "not-a-date"
    hass5 = HomeAssistant()
    hass5.config_entries.entries = hass4.config_entries.entries
    _, s5 = await load(hass5)
    check("corrupt timestamp -> expired, not reset",
          s5.status == "trial_expired", s5.status)

    # 6. Clock rollback does not hand back days
    FAKE_DISK = {}
    hass6 = await fresh_hass()
    mgr6, _ = await load(hass6)
    # Pretend the system has been up long enough to spend the window.
    future = datetime.now(timezone.utc) + timedelta(days=3)
    mgr6._anchor.high_water = future
    await mgr6._async_persist_anchor(force=True)
    hass7 = HomeAssistant()
    hass7.config_entries.entries = hass6.config_entries.entries
    _, s7 = await load(hass7)
    check("clock rollback -> window already spent",
          s7.status == "trial_expired", s7.status)

    # 7. Legacy pinned server id is preserved
    FAKE_DISK = {
        "ar_hdl_buspro.license": {
            "server_id": "deadbeefdeadbeefdeadbeefdeadbeef",
            "trial_started": datetime.now(timezone.utc).isoformat(),
        }
    }
    hass8 = await fresh_hass()
    mgr8, s8 = await load(hass8)
    check("legacy server id kept verbatim",
          mgr8.server_id == "deadbeefdeadbeefdeadbeefdeadbeef", mgr8.server_id)
    check("legacy trial_started still honoured", s8.status == "trial", s8.status)

    # 8. A valid key activates
    FAKE_DISK = {}
    hass9 = await fresh_hass()
    mgr9, _ = await load(hass9)
    key = mint(mgr9.server_id)
    state9, err = await mgr9.async_set_key(key)
    check("valid key -> licensed", state9.status == "licensed" and err is None,
          f"{state9.status} err={err}")
    check("licensed state carries client", state9.client == "Test Lodge")

    # 9. Unlock token binding
    token = mgr9.unlock
    check("unlock token minted when licensed", token is not None)
    check("token matches its own install+status",
          token.matches(mgr9.server_id, "licensed"))
    check("token rejects a different server id",
          not token.matches("0" * 32, "licensed"))
    check("token rejects a different status",
          not token.matches(mgr9.server_id, "trial"))

    # 10. Wrong-server and wrong-product keys are refused
    _, err_ws = await mgr9.async_set_key(mint("f" * 32))
    check("key for another server id refused", err_ws == "wrong_server", str(err_ws))
    _, err_wp = await mgr9.async_set_key(
        mint(mgr9.server_id, product="ar_smart_ir"))
    check("key for another product refused", err_wp == "wrong_product", str(err_wp))
    check("a refused key does not disturb the working one",
          mgr9.state.status == "licensed", mgr9.state.status)

    # 11. Expired key
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    _, err_exp = await mgr9.async_set_key(mint(mgr9.server_id, expires_at=past))
    check("expired key refused", err_exp == "expired", str(err_exp))

    # 12. Tampered build locks
    FAKE_DISK = {}
    hass10 = await fresh_hass()
    mgr10 = licensing.ARHDLLicenseManager(hass10)
    original = licensing._integrity_failure
    licensing._integrity_failure = lambda: "entity.py"
    s10 = await mgr10.async_load()
    licensing._integrity_failure = original
    check("tampered build -> locked", s10.status == "tampered", s10.status)
    check("tampered build mints no unlock token", mgr10.unlock is None)
    _, err_t = await mgr10.async_set_key(mint(mgr10.server_id))
    check("tampered build refuses a valid key", err_t == "tampered", str(err_t))

    # 13. is_active / get_unlock fail closed with no manager
    empty = HomeAssistant()
    check("is_active fails closed without a manager",
          licensing.is_active(empty) is False)
    check("get_unlock fails closed without a manager",
          licensing.get_unlock(empty) is None)

    print()
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    return 0


sys.exit(asyncio.run(main()))
