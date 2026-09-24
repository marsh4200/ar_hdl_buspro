#!/usr/bin/env python3
"""Cython build for the compiled licensing module.

Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).
All rights reserved. Proprietary and confidential.

Compiles `custom_components/ar_hdl_buspro/licensing.py` into a native
extension module (`licensing.cpython-<ver>-<arch>-linux-gnu.so`) so the
Server ID derivation, the trial anchor logic and the unlock-token
derivation are no longer readable or editable text on the customer's disk.

Usage (from the repository root):

    pip install cython setuptools
    python3 build/setup_licensing.py build_ext --inplace

The .so lands beside licensing.py. Python's import machinery prefers an
extension module over a same-named .py, so the compiled half wins
automatically wherever its version/arch tag matches the running
interpreter, and the .py is used everywhere else - which is why a strict
release replaces that .py with build/licensing_stub.py.

See build/README.md for the release order and the platform matrix.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

from setuptools import setup  # noqa: I001
from Cython.Build import cythonize

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(REPO, "custom_components", "ar_hdl_buspro")
SOURCE = os.path.join(PKG, "licensing.py")


def _staged_source() -> str:
    """Copy licensing.py to a temp .py Cython can name the module from.

    Cython derives the extension's module name from the file name, and the
    module has to end up importable as `licensing` inside the package, so
    the file is compiled under its own name from a scratch directory to
    keep build artefacts (the generated .c) out of the shipped tree.
    """
    if not os.path.isfile(SOURCE):
        print(f"error: {SOURCE} not found", file=sys.stderr)
        raise SystemExit(1)

    tmp = tempfile.mkdtemp(prefix="ar_hdl_licensing_")
    staged = os.path.join(tmp, "licensing.py")
    shutil.copy2(SOURCE, staged)
    return staged


setup(
    name="ar_hdl_buspro_licensing",
    ext_modules=cythonize(
        [_staged_source()],
        compiler_directives={
            "language_level": "3",
            # Keep docstrings out of the binary: they describe the scheme.
            "embedsignature": False,
            "emit_code_comments": False,
        },
        quiet=False,
    ),
    script_args=sys.argv[1:] or ["build_ext", "--inplace"],
    options={"build_ext": {"build_lib": PKG, "inplace": 1}},
)
