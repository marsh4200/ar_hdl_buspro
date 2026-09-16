# Licence hardening — change summary

Copyright (c) 2026 Marsh — AR Smart Home (arsmarthome.co.za).

Drop these files over a v5.0.0 checkout. No existing behaviour changes for
a licensed install; no licence key already issued is invalidated.

## The holes this closes

**1. Infinite trial, no coding required.** The Server ID and trial clock
both lived in `.storage/ar_hdl_buspro.license`. Deleting that file minted a
new Server ID and a fresh 2-day window — reachable from the File Editor
add-on, repeatable forever. Corrupting `trial_started` did the same thing,
because `_evaluate_trial` treated an unparseable timestamp as "starts now".

**2. Expired installs kept working.** Enforcement was entity availability
only. Home Assistant does not block service calls to `unavailable`
entities, so on a trial-expired install every automation, script and
`light.turn_on` kept driving the bus — the entities just looked dead in the
UI. This was the larger of the two, and it was functional, not cosmetic.

**3. Fail-open defaults.** `is_active()` returned `True` when the manager
was absent, and entities started with `_license_active = True`.

## What changed

| File | Change |
|---|---|
| `licensing.py` | Server ID derived from HA's instance UUID; three-location trial anchor; fail-closed parsing; clock-rollback high-water mark; unlock token; integrity check; `tampered` status |
| `pybuspro/transport/network_interface.py` | `send_telegram` now requires a minted unlock token — the functional enforcement point |
| `pybuspro/buspro.py` | Carries `unlock_provider` / `license_server_id`, both unset (inert) by default |
| `gateway.py` | Installs the unlock provider on the Buspro client |
| `entity.py` | `_license_active` starts `False` |
| `__init__.py` | Mirrors the trial anchor into the config entry at setup |
| `strings.json`, `translations/en.json` | `tampered`, `unsupported_platform` messages |
| `build/`, `.github/workflows/build-licensing.yml` | Integrity manifest generator, Cython build, lock stub, release workflow |
| `tests/test_licensing.py` | 27 checks over the above |

### Server ID: derived, not minted

`sha256("ar_hdl_buspro/server-id/v2|" + <HA instance UUID>)[:32]`

Deterministic, so deleting the licence store reproduces the same ID — an
installed key keeps working *and* a reset grants no new trial. One-way, so
a Server ID emailed to you does not disclose the customer's instance UUID.

**Existing customers are unaffected.** A pre-v2 install has a random Server
ID already pinned in its store; `_async_resolve_server_id` keeps that value
verbatim and never re-derives it. Only fresh installs get v2 IDs.

The one case needing a support action: a *legacy* customer who deletes their
store loses the pinned ID, gets a v2 one, and needs a re-issued key. New
installs never hit this.

### Trial anchor

Written to three places — the licence store, a second `ar_hdl_buspro.runtime`
store, and every config entry's data. Earliest start wins, so adding
locations can only shorten a window, never extend one. A slot that is
present but unreadable, or bound to a different Server ID, counts as *long
expired* rather than empty: blanking a field locks instead of unlocking.

The config-entry copy is the one that matters — it lives in
`.storage/core.config_entries`, and deleting that destroys the customer's
entire HA setup. It deliberately carries only the immutable half (install +
start), never the moving high-water mark, because rewriting entry data fires
the update listener and a field that changed every few hours would cause a
reload loop.

A monotonic high-water mark is persisted alongside, so winding the host
clock back does not return spent days.

### Send-path gate

`NetworkInterface.send_telegram` now calls `_send_permitted()` first.
Control telegrams and status reads both pass through it, so an unlicensed
install is genuinely inert rather than merely grey in the UI. The unlock
token has to be *minted* for this Server ID and status — a patched
`is_active()` returning `True` does not satisfy it, and neither does a
truthy stand-in. Warning is logged once per hour, not per telegram.

## Honest limits

Everything here runs on the customer's machine from code they possess, so
none of it is a cryptographic barrier. The signature check still cannot be
forged; it can still be *removed*. What these layers change is the cost —
from a one-line edit any curious person might try, to a deliberate,
multi-file act against three cooperating modules and an integrity manifest.

That distinction is worth real money because those are different
populations of people. It is not the same as being unbreakable, and a build
that claimed otherwise would be lying to you.

`build/README.md` has the layer-by-layer table of what each one does and
does not stop, plus the compiled-build trade-offs — particularly the
platform matrix, where a customer on a Python version you did not build for
gets locked out rather than merely unprotected.

One thing no code change touches: if `github.com/marsh4200/ar_hdl_buspro` is
public, the pre-hardening source is already out there, and the compiled
build only protects future releases. Against redistribution it is the
LICENCE that gets enforced, not the lock.

## Verify

```bash
python3 tests/test_licensing.py          # 27 checks
python3 build/gen_manifest.py            # release step, run last
```
