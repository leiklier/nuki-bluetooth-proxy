"""Tests for CRC-16/CCITT-FALSE against official spec vectors."""

from custom_components.nuki_opener_ble.nuki.crc import append_crc, crc16, verify_crc

from . import vectors


def test_check_value() -> None:
    # Standard check value for CRC-16/CCITT-FALSE.
    assert crc16(b"123456789") == 0x29B1


def test_spec_vectors() -> None:
    assert append_crc(bytes.fromhex("01000300")) == vectors.REQUEST_PUBLIC_KEY_MESSAGE
    assert append_crc(bytes.fromhex("0300") + vectors.CL_PUBLIC_KEY) == (
        vectors.CL_PUBLIC_KEY_MESSAGE
    )
    assert append_crc(bytes.fromhex("0E0000")) == vectors.STATUS_COMPLETE_MESSAGE
    assert append_crc(bytes.fromhex("0400") + vectors.CHALLENGE_1) == (vectors.CHALLENGE_1_MESSAGE)


def test_verify_crc() -> None:
    assert verify_crc(vectors.AUTHORIZATION_ID_MESSAGE)
    assert verify_crc(vectors.AUTHORIZATION_DATA_MESSAGE)
    assert not verify_crc(vectors.AUTHORIZATION_ID_MESSAGE[:-1] + b"\x00")
    assert not verify_crc(b"")
    assert not verify_crc(b"\x01")


def test_zero_crc_is_accepted() -> None:
    # Some firmware versions send messages with a zeroed CRC field.
    assert verify_crc(b"\x0e\x00\x00\x00\x00")
