"""Tests for Nuki iBeacon advertisement parsing."""

from custom_components.nuki_opener_ble.nuki.advertisement import parse_manufacturer_data
from custom_components.nuki_opener_ble.nuki.const import OPENER_SERVICE_UUID


def make_ibeacon(uuid_hex: str, measured_power: int) -> dict[int, bytes]:
    return {
        76: bytes.fromhex("0215")
        + bytes.fromhex(uuid_hex)
        + (0x0011).to_bytes(2, "big")
        + (0x2233).to_bytes(2, "big")
        + bytes([measured_power])
    }


OPENER_UUID_HEX = OPENER_SERVICE_UUID.replace("-", "")


def test_parse_opener_beacon() -> None:
    adv = parse_manufacturer_data(make_ibeacon(OPENER_UUID_HEX, 0xC4))
    assert adv is not None
    assert adv.is_opener
    assert adv.service_uuid == OPENER_SERVICE_UUID
    assert adv.major == 0x0011
    assert adv.minor == 0x2233
    assert not adv.state_changed


def test_state_changed_flag() -> None:
    adv = parse_manufacturer_data(make_ibeacon(OPENER_UUID_HEX, 0xC5))
    assert adv is not None
    assert adv.state_changed


def test_smart_lock_beacon_is_not_opener() -> None:
    adv = parse_manufacturer_data(make_ibeacon("a92ee200550111e4916c0800200c9a66", 0xC4))
    assert adv is not None
    assert not adv.is_opener


def test_homekit_advertisement_is_ignored() -> None:
    # Nuki devices also broadcast HomeKit frames under the Apple ID.
    assert parse_manufacturer_data({76: bytes.fromhex("061d9876")}) is None


def test_other_manufacturer_is_ignored() -> None:
    assert parse_manufacturer_data({0x0059: bytes(23)}) is None
    assert parse_manufacturer_data({}) is None
