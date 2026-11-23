"""Relay vs dimmer classification from the HDL catalogue.

Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).

    python3 tests/test_relay_dimmer_classification.py

Regression for the 0x0208 (HDL-MR1210A.433, 12ch 10A relay) misclassification:
the catalogue carries an empty family for many relay/dimmer codes, so when the
device also doesn't self-report its function list, the only thing left that
tells a relay from a dimmer is HDL's recorded function triple -- (1, 1, n) for
relays, (1, 0, n) for dimmers. _infer_device_type must consult it before the
last-ditch "saw a mid-level reading, call it a light" guess.
"""
import importlib
import os
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
import stub_ha  # noqa: E402

PKG_DIR = os.path.abspath(os.path.join(HERE, "..", "custom_components", "ar_hdl_buspro"))
PKG = stub_ha.install(PKG_DIR)
cf = importlib.import_module(f"{PKG}.config_flow")
const = importlib.import_module(f"{PKG}.const")


class Disc:
    """Minimal stand-in for a scan discovery record."""

    def __init__(self, type_code, *, self_functions=None, channel_count=12,
                 op_codes=("ReadStatusOfChannelsResponse",), dimmer_evidence=False,
                 looks_like_keypad=False):
        self.type_code = type_code
        self.self_functions = self_functions
        self.channel_count = channel_count
        self.op_codes = set(op_codes)
        self.dimmer_evidence = dimmer_evidence
        self.looks_like_keypad = looks_like_keypad


def infer(disc):
    flow = cf.ARHDLOptionsFlow.__new__(cf.ARHDLOptionsFlow)
    # dimmer_codes passed explicitly so the method never touches flow state.
    return flow._infer_device_type(disc, set())


def test_0x0208_relay_not_dimmer():
    # The exact failure: a relay that didn't self-report, and the scan happened
    # to catch a mid-level channel so the old guess said "light".
    d = Disc("0x0208", self_functions=None, dimmer_evidence=True)
    assert infer(d) == const.DEVICE_TYPE_SWITCH, infer(d)


def test_0x0208_relay_without_dimmer_evidence():
    d = Disc("0x0208", self_functions=None, dimmer_evidence=False)
    assert infer(d) == const.DEVICE_TYPE_SWITCH, infer(d)


def test_every_empty_family_relay_resolves_to_switch():
    # All 14 empty-family (1,1,n) relay codes, under the worst case
    # (no self-report, a stray mid-level reading).
    import re
    src = open(os.path.join(PKG_DIR, "hdl_catalog.py"), encoding="utf-8").read()
    checked = 0
    for m in re.finditer(
        r"(0x[0-9A-Fa-f]+):\s*\('([^']*)',\s*'[^']*',\s*'([^']*)',\s*\(([^)]*)\)\)",
        src,
    ):
        code, _model, fam, fn = m.groups()
        nums = [int(x) for x in fn.split(",")] if fn.strip() else []
        if fam == "" and nums[:2] == [1, 1]:
            d = Disc(code, self_functions=None, dimmer_evidence=True)
            assert infer(d) == const.DEVICE_TYPE_SWITCH, f"{code} -> {infer(d)}"
            checked += 1
    assert checked >= 10, f"only checked {checked}"


def test_catalogue_dimmer_claim_is_not_trusted():
    # HDL's function list mislabels some relays as dimmers (0x01C8 records
    # (1, 0, 6) but is a relay), so a catalogue "dimmer" claim must NOT force
    # a light. Without live dimmer evidence it stays a switch; genuine dimmers
    # are identified by the scan's dimmer evidence (next test), pins, or the
    # device's own report -- not by the catalogue alone.
    d = Disc("0x0256", self_functions=None, channel_count=8, dimmer_evidence=False)
    assert infer(d) == const.DEVICE_TYPE_SWITCH, infer(d)


def test_catalogue_dimmer_with_live_evidence_is_light():
    d = Disc("0x0256", self_functions=None, channel_count=8, dimmer_evidence=True)
    assert infer(d) == const.DEVICE_TYPE_LIGHT, infer(d)


def test_0x01C8_mislabelled_relay_stays_switch():
    # The canonical "catalogue says dimmer, hardware is a relay" case. A real
    # relay never reports a mid-level channel, so dimmer_evidence is False and
    # the dropped catalogue "dimmer" claim leaves it correctly a switch.
    d = Disc("0x01C8", self_functions=None, channel_count=6, dimmer_evidence=False)
    assert infer(d) == const.DEVICE_TYPE_SWITCH, infer(d)


def test_tagged_relay_family_unchanged():
    # 0x0203 already has family 'relay'; behaviour must not change.
    d = Disc("0x0203", self_functions=None, dimmer_evidence=True)
    assert infer(d) == const.DEVICE_TYPE_SWITCH, infer(d)


def test_unknown_channel_device_keeps_dimmer_guess():
    # A code absent from the catalogue keeps the old evidence-based fallback,
    # so genuinely unknown dimmers are still detected.
    d = Disc("0xEEEE", self_functions=None, dimmer_evidence=True)
    assert infer(d) == const.DEVICE_TYPE_LIGHT, infer(d)
    d2 = Disc("0xEEEE", self_functions=None, dimmer_evidence=False)
    assert infer(d2) == const.DEVICE_TYPE_SWITCH, infer(d2)


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("ALL PASSED")


if __name__ == "__main__":
    main()
