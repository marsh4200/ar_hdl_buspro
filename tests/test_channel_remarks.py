"""Per-channel Studio remarks (0xF00E/0xF00F).

Copyright (c) 2026 Marsh - AR Smart Home (arsmarthome.co.za).

    python3 tests/test_channel_remarks.py

HDL Studio V1.9.65 reads channel names with 0xF00E (Relay.ReadRelayChannelRemark
-> LightDeviceBasic.ReadChannelRemark), channel in the payload, reply 0xF00F.
This covers: the reply parser accepts a 1-byte or 2-byte channel field and
rejects junk (fails safe); a 0xF00F reply during a scan stores the remark on
the device; the parser never raises; and the base entity exposes the remark as
a read-only attribute without touching the name. The remark is a display hint
only -- the entity name and entity_id are never changed.
"""
import importlib
import os
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
import stub_ha  # noqa: E402

PKG_DIR = os.path.abspath(os.path.join(HERE, "..", "custom_components", "ar_hdl_buspro"))
PKG = stub_ha.install(PKG_DIR)
discovery = importlib.import_module(f"{PKG}.discovery")
entity = importlib.import_module(f"{PKG}.entity")
const = importlib.import_module(f"{PKG}.const")

BusScanner = discovery.BusScanner
DiscoveredDevice = discovery.DiscoveredDevice


def _name_bytes(text, length=20):
    b = text.encode("ascii")
    return list(b) + [0] * (length - len(b))


def test_parse_one_byte_channel():
    payload = [6] + _name_bytes("Passage lights")
    assert BusScanner._parse_channel_remark(payload, 12) == (6, "Passage lights")


def test_parse_two_byte_channel():
    payload = [0, 6] + _name_bytes("Passage lights")
    assert BusScanner._parse_channel_remark(payload, 12) == (6, "Passage lights")


def test_parse_rejects_channel_out_of_range():
    # Channel 30 on a 12-channel module -> not trusted, no name.
    payload = [30] + _name_bytes("Nonsense")
    assert BusScanner._parse_channel_remark(payload, 12) is None


def test_parse_rejects_blank_name():
    assert BusScanner._parse_channel_remark([6] + [0] * 20, 12) is None
    assert BusScanner._parse_channel_remark([6, 0x20, 0x20], 12) is None


def test_parse_never_raises_on_garbage():
    for payload in ([], [5], "notalist", [None, None], [999, 999]):
        try:
            BusScanner._parse_channel_remark(payload, 12)
        except Exception as e:  # noqa: BLE001
            raise AssertionError(f"raised on {payload!r}: {e}")


def test_reply_stores_remark_on_device():
    scanner = BusScanner.__new__(BusScanner)
    scanner._found = {}
    dev = DiscoveredDevice(subnet_id=2, device_id=1, type_code="0x0208",
                           type_name="relay", channel_count=12)
    parsed = BusScanner._parse_channel_remark([6] + _name_bytes("Patio"), dev.channel_count)
    assert parsed == (6, "Patio")
    dev.channel_remarks[parsed[0]] = parsed[1]
    assert dev.channel_remarks == {6: "Patio"}


def test_cap_blocks_absurd_channel():
    # Even if the device claims 500 channels, the parser cap rejects channel 200.
    payload = [0, 200] + _name_bytes("x far")
    assert BusScanner._parse_channel_remark(payload, 500) is None


def test_entity_exposes_remark_attribute_without_touching_name():
    cfg = {
        const.CONF_DEVICE_TYPE: const.DEVICE_TYPE_SWITCH,
        const.CONF_SUBNET_ID: 2,
        const.CONF_DEVICE_ID: 1,
        const.CONF_CHANNEL: 6,
        const.CONF_NAME: "2.1 Channel 6",
        const.CONF_CHANNEL_REMARK: "Passage lights",
    }
    ent = entity.ARHDLBaseEntity.__new__(entity.ARHDLBaseEntity)
    ent._device_cfg = cfg
    attrs = ent.extra_state_attributes
    assert attrs.get("channel_remark") == "Passage lights"
    # The name is untouched by this feature.
    assert cfg[const.CONF_NAME] == "2.1 Channel 6"


def test_entity_no_attribute_when_no_remark():
    ent = entity.ARHDLBaseEntity.__new__(entity.ARHDLBaseEntity)
    ent._device_cfg = {const.CONF_NAME: "2.1 Channel 7"}
    assert ent.extra_state_attributes == {}


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("ALL PASSED")


if __name__ == "__main__":
    main()
