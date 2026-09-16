#!/usr/bin/env python3
"""Generate the integrity manifest shipped with a release build.

Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).
All rights reserved. Proprietary and confidential.

Writes `custom_components/ar_hdl_buspro/_integrity.py`, a dict of
SHA-256 digests for the modules that participate in licence enforcement.
`licensing.async_load` verifies it at startup and locks the integration if
any of them has been altered on disk.

Run this as the LAST step of a release build, after every other file is
final:

    python3 build/gen_manifest.py

What this does and does not buy you
-----------------------------------
It catches a customer editing `entity.py` or `network_interface.py` in
place - the common, casual case. It does not stop someone who also deletes
`_integrity.py`, or regenerates it after editing, because the check and the
data it checks against both live on their machine. It is a speed bump on
the same road as everything else in this directory; the compiled build
(see build/README.md) is what moves the check itself out of reach.

`licensing.py` is deliberately NOT in the manifest: it is the file doing
the checking, so hashing itself from inside itself proves nothing. Protect
that one by compiling it.
"""
from __future__ import annotations

import hashlib
import os
import sys

# Modules that enforce, or can trivially defeat, the licence.
GATED = [
    "__init__.py",
    "entity.py",
    "gateway.py",
    "pybuspro/buspro.py",
    "pybuspro/transport/network_interface.py",
    "pybuspro/transport/udp_client.py",
    "pybuspro/devices/control.py",
    "pybuspro/devices/device.py",
]

HEADER = '''"""Generated integrity manifest - do not edit by hand.

Produced by build/gen_manifest.py at release time. See that file for what
this check is and is not worth.
"""

MANIFEST = {
'''


def main() -> int:
    """Write the manifest next to the integration's modules."""
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pkg = os.path.join(repo, "custom_components", "ar_hdl_buspro")

    if not os.path.isdir(pkg):
        print(f"error: integration package not found at {pkg}", file=sys.stderr)
        return 1

    lines = [HEADER]
    missing = []

    for relpath in GATED:
        full = os.path.join(pkg, relpath)
        if not os.path.isfile(full):
            missing.append(relpath)
            continue
        with open(full, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        lines.append(f'    "{relpath}": "{digest}",\n')

    if missing:
        print(
            "error: gated modules missing: " + ", ".join(missing),
            file=sys.stderr,
        )
        return 1

    lines.append("}\n")

    out = os.path.join(pkg, "_integrity.py")
    with open(out, "w", encoding="utf-8") as handle:
        handle.writelines(lines)

    print(f"wrote {out} ({len(GATED)} modules)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
