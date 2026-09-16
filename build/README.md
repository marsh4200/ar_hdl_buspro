# Release hardening — AR HDL BUSPRO

Copyright (c) 2026 Marsh — AR Smart Home (arsmarthome.co.za).

This directory holds the build-time half of the licence enforcement. None
of it is needed to run the integration from a working tree; it only shapes
what a *released* build looks like.

## What the layers actually buy

Read this part before deciding how far to go, because the honest answer
changes what's worth the effort.

Every check runs on the customer's machine, on code they possess. That is
not a fixable property — it is what shipping software to someone else
means. So none of this makes the licence unbreakable. What it does is move
the bypass from *trivial and accidental* to *deliberate and effortful*,
which matters because those are different populations of people:

| Layer | Stops | Doesn't stop |
|---|---|---|
| Ed25519 signature | Forging or editing a key | Deleting the check |
| Derived Server ID | Resetting the demo window by deleting `.storage` | Reinstalling HA from scratch |
| Multi-location trial anchor | Clearing or corrupting one file to reset the trial | Removing all three copies |
| Send-path gate | Automations and service calls working on an expired install | Someone who reads `network_interface.py` |
| Integrity manifest | In-place edits to the gated modules | Deleting or regenerating the manifest |
| Compiled module | Reading and editing the scheme in a text editor | Binary patching, or someone extracting a working `.so` |

The last two layers depend on each other more than it looks. Compiling
`licensing.py` hides the scheme, but a compiled module's globals are still
writable from Python — a one-line `licensing._PUBLIC_KEY_HEX = <their key>`
added to `__init__.py` would let someone sign their own licences against a
binary they cannot read. The integrity manifest is what closes that, because
`__init__.py` is in it. Ship the two together or neither.

The first four are cheap and worth doing unconditionally. The last two
cost real maintenance; take them on when the threat you actually face is a
competitor redistributing the integration, not a customer fiddling.

Worth saying plainly: if `github.com/marsh4200/ar_hdl_buspro` is a public
repo, the source is already out there and the compiled build only protects
*future* releases. The LICENCE file, not the code, is what you would
enforce against redistribution.

## Files

- `gen_manifest.py` — writes `custom_components/ar_hdl_buspro/_integrity.py`,
  SHA-256 digests of the modules that enforce the licence. Run it **last**,
  after every other file in the bundle is final.
- `setup_licensing.py` — Cython build producing `licensing.*.so`.
- `licensing_stub.py` — the inert `licensing.py` used in strict releases,
  imported only on a platform the release has no binary for.

## Release order

The order matters — the manifest hashes whatever is on disk at the time.

1. Finish all source changes.
2. Build the `.so` for every platform in the matrix
   (`.github/workflows/build-licensing.yml` does this with QEMU + manylinux).
3. Copy the `.so` files into `custom_components/ar_hdl_buspro/`.
4. **Strict releases only:** copy `licensing_stub.py` over
   `custom_components/ar_hdl_buspro/licensing.py`.
5. Run `python3 build/gen_manifest.py`.
6. Remove `build/`, `__pycache__/` and generated `.c` files from the bundle.

## The platform matrix, and the risk in it

Python prefers an extension module over a same-named `.py`, so a matching
`licensing.*.so` wins automatically and a non-matching one is ignored. The
consequence: **a customer on a (Python version, architecture) pair you did
not build for gets the lock stub and a dead integration.**

Current matrix: Python 3.12 / 3.13 × `x86_64` / `aarch64` / `armv7`. That
covers HA OS and supervised installs on Intel NUCs and Raspberry Pi 4/5.
It does not cover someone running HA Core in a venv on an unusual Python,
or a 32-bit Pi OS variant you haven't seen.

Two ways to manage that:

- **Staged rollout (recommended).** Run the workflow with `strict: false`
  for a release or two. The `.so` files ship and are used where they match,
  but real `licensing.py` still works everywhere else — so nobody is locked
  out while you confirm the matrix covers your fleet. Flip to strict once
  it does.
- **Go strict immediately** and handle the support calls. Every HA release
  that bumps Python needs a new build *before* your customers update,
  otherwise they all fall to the stub at once. That is the standing cost of
  this layer — put a reminder on HA's release schedule.

## Verifying a build

From the repo root, with the `.so` in place:

```bash
cd custom_components/ar_hdl_buspro
python3 -c "import licensing; print(licensing.__file__)"
```

It should print the `.so`, not `licensing.py`. If it prints the `.py`, the
version/arch tag does not match your interpreter and a customer on that
platform would get the stub.
