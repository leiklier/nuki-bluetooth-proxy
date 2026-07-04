"""CRC-16/CCITT-FALSE as used by the Nuki BLE protocol.

Polynomial 0x1021, initial value 0xFFFF, no reflection, no final XOR.
The checksum is appended to messages in little-endian byte order.
"""

from __future__ import annotations


def _build_table() -> tuple[int, ...]:
    table = []
    for byte in range(256):
        crc = byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021 if crc & 0x8000 else crc << 1) & 0xFFFF
        table.append(crc)
    return tuple(table)


_TABLE = _build_table()


def crc16(data: bytes) -> int:
    """Return the CRC-16/CCITT-FALSE checksum of ``data``."""
    crc = 0xFFFF
    for byte in data:
        crc = ((crc << 8) & 0xFFFF) ^ _TABLE[((crc >> 8) ^ byte) & 0xFF]
    return crc


def append_crc(data: bytes) -> bytes:
    """Return ``data`` with its checksum appended (little-endian)."""
    return data + crc16(data).to_bytes(2, "little")


def verify_crc(message: bytes) -> bool:
    """Check the trailing little-endian checksum of a complete message.

    A checksum of 0 is accepted: some firmware versions occasionally send
    messages with a zeroed CRC field (observed in the field by pyNukiBT).
    """
    if len(message) < 3:
        return False
    received = int.from_bytes(message[-2:], "little")
    return received == 0 or received == crc16(message[:-2])
