"""Message framing for the Nuki Opener BLE protocol.

Unencrypted messages (pairing characteristic)::

    command (2, LE) | payload (n) | CRC (2, LE)

Encrypted messages (USDIO characteristic)::

    nonce (24) | auth_id (4) | length (2, LE) | ciphertext (length)

where the ciphertext decrypts to::

    auth_id (4) | command (2, LE) | payload (n) | CRC (2, LE)
"""

from __future__ import annotations

import nacl.exceptions

from .const import Command
from .crc import append_crc, verify_crc
from .crypto import decrypt, encrypt
from .errors import NukiCrcError, NukiProtocolError

ENCRYPTED_HEADER_SIZE = 24 + 4 + 2

# Sizes of the complete unencrypted messages the Opener sends on the pairing
# characteristic, used to reassemble fragmented indications.
_PLAIN_MESSAGE_SIZES: dict[Command, int] = {
    Command.PUBLIC_KEY: 2 + 32 + 2,
    Command.CHALLENGE: 2 + 32 + 2,
    Command.AUTHORIZATION_ID: 2 + 84 + 2,
    Command.STATUS: 2 + 1 + 2,
    Command.ERROR_REPORT: 2 + 3 + 2,
}

_MAX_BUFFER = 4096


def encode_plain(command: Command, payload: bytes = b"") -> bytes:
    """Frame an unencrypted message."""
    return append_crc(command.to_bytes(2, "little") + payload)


def decode_plain(message: bytes) -> tuple[Command, bytes]:
    """Parse an unencrypted message; returns ``(command, payload)``."""
    if len(message) < 4:
        raise NukiProtocolError(f"message too short: {message.hex()}")
    if not verify_crc(message):
        raise NukiCrcError(f"bad CRC in message: {message.hex()}")
    return Command(int.from_bytes(message[0:2], "little")), message[2:-2]


def encode_encrypted(
    auth_id: bytes,
    command: Command,
    payload: bytes,
    shared_key: bytes,
    nonce: bytes | None = None,
) -> bytes:
    """Frame and encrypt a message for the USDIO characteristic."""
    plaintext = append_crc(auth_id + command.to_bytes(2, "little") + payload)
    nonce, ciphertext = encrypt(plaintext, shared_key, nonce)
    return nonce + auth_id + len(ciphertext).to_bytes(2, "little") + ciphertext


def decode_encrypted(message: bytes, shared_key: bytes) -> tuple[bytes, Command, bytes]:
    """Decrypt and parse a USDIO message; returns ``(auth_id, command, payload)``."""
    if len(message) < ENCRYPTED_HEADER_SIZE:
        raise NukiProtocolError(f"encrypted message too short: {message.hex()}")
    nonce = message[0:24]
    outer_auth_id = message[24:28]
    length = int.from_bytes(message[28:30], "little")
    ciphertext = message[30:]
    if len(ciphertext) != length:
        raise NukiProtocolError(
            f"encrypted length mismatch: expected {length}, got {len(ciphertext)}"
        )
    try:
        plaintext = decrypt(nonce, ciphertext, shared_key)
    except nacl.exceptions.CryptoError as err:
        raise NukiProtocolError("failed to decrypt message") from err
    if len(plaintext) < 8:
        raise NukiProtocolError(f"decrypted message too short: {plaintext.hex()}")
    if not verify_crc(plaintext):
        raise NukiCrcError(f"bad CRC in decrypted message: {plaintext.hex()}")
    auth_id = plaintext[0:4]
    if auth_id != outer_auth_id:
        raise NukiProtocolError("authorization id mismatch between ADATA and PDATA")
    return auth_id, Command(int.from_bytes(plaintext[4:6], "little")), plaintext[6:-2]


class MessageReassembler:
    """Reassemble complete messages from a stream of GATT indications.

    The Nuki spec limits characteristic values to 20 bytes, so a single
    logical message may arrive split over several indications (or, with a
    large negotiated MTU, as a single one — possibly even several messages
    back to back).
    """

    def __init__(self, encrypted: bool) -> None:
        self._encrypted = encrypted
        self._buffer = bytearray()

    def reset(self) -> None:
        self._buffer.clear()

    def feed(self, data: bytes) -> list[bytes]:
        """Add received bytes; return any complete raw messages."""
        self._buffer.extend(data)
        if len(self._buffer) > _MAX_BUFFER:
            self._buffer.clear()
            raise NukiProtocolError("receive buffer overflow")
        messages = []
        while (size := self._expected_size()) is not None and len(self._buffer) >= size:
            messages.append(bytes(self._buffer[:size]))
            del self._buffer[:size]
        return messages

    def _expected_size(self) -> int | None:
        """Size of the message at the buffer head, or None if undetermined yet."""
        if self._encrypted:
            if len(self._buffer) < ENCRYPTED_HEADER_SIZE:
                return None
            length = int.from_bytes(self._buffer[28:30], "little")
            return ENCRYPTED_HEADER_SIZE + length
        if len(self._buffer) < 2:
            return None
        command = Command(int.from_bytes(self._buffer[0:2], "little"))
        if (size := _PLAIN_MESSAGE_SIZES.get(command)) is not None:
            return size
        # Unknown message type: accept the buffer as one message once its CRC
        # matches. Scanning is acceptable here because unknown types can only
        # appear if the firmware introduces new pairing responses.
        for end in range(5, len(self._buffer) + 1):
            if verify_crc(bytes(self._buffer[:end])):
                return end
        return None
