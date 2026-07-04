"""Tests for message framing and reassembly against official spec vectors."""

import pytest

from custom_components.nuki_opener_ble.nuki import protocol
from custom_components.nuki_opener_ble.nuki.const import Command
from custom_components.nuki_opener_ble.nuki.errors import NukiCrcError, NukiProtocolError
from custom_components.nuki_opener_ble.nuki.messages import build_request_data

from . import vectors


def test_encode_plain_spec_vectors() -> None:
    assert (
        protocol.encode_plain(Command.REQUEST_DATA, build_request_data(Command.PUBLIC_KEY))
        == vectors.REQUEST_PUBLIC_KEY_MESSAGE
    )
    assert (
        protocol.encode_plain(Command.PUBLIC_KEY, vectors.CL_PUBLIC_KEY)
        == vectors.CL_PUBLIC_KEY_MESSAGE
    )
    assert (
        protocol.encode_plain(Command.AUTHORIZATION_AUTHENTICATOR, vectors.AUTHENTICATOR_1)
        == vectors.AUTHORIZATION_AUTHENTICATOR_MESSAGE
    )


def test_decode_plain_spec_vector() -> None:
    command, payload = protocol.decode_plain(vectors.PUBLIC_KEY_RESPONSE)
    assert command == Command.PUBLIC_KEY
    assert payload == vectors.DEVICE_PUBLIC_KEY


def test_decode_plain_rejects_bad_crc() -> None:
    corrupted = vectors.PUBLIC_KEY_RESPONSE[:-1] + b"\x01"
    with pytest.raises(NukiCrcError):
        protocol.decode_plain(corrupted)


def test_decode_plain_rejects_short_message() -> None:
    with pytest.raises(NukiProtocolError):
        protocol.decode_plain(b"\x01")


def test_encode_encrypted_spec_vectors() -> None:
    assert (
        protocol.encode_encrypted(
            vectors.AUTH_ID,
            Command.REQUEST_DATA,
            build_request_data(Command.OPENER_STATES),
            vectors.SHARED_KEY,
            vectors.STATES_REQUEST_NONCE,
        )
        == vectors.STATES_REQUEST_ENCRYPTED
    )
    assert (
        protocol.encode_encrypted(
            vectors.AUTH_ID,
            Command.REQUEST_DATA,
            build_request_data(Command.CHALLENGE),
            vectors.SHARED_KEY,
            vectors.CHALLENGE_REQUEST_NONCE,
        )
        == vectors.CHALLENGE_REQUEST_ENCRYPTED
    )


def test_encode_encrypted_lock_action_spec_vector() -> None:
    # The plaintext PDATA documented in the spec ...
    payload = vectors.LOCK_ACTION_PLAINTEXT[6:-2]
    # ... must produce exactly the documented ciphertext.
    assert (
        protocol.encode_encrypted(
            vectors.AUTH_ID,
            Command.LOCK_ACTION,
            payload,
            vectors.SHARED_KEY,
            vectors.LOCK_ACTION_NONCE,
        )
        == vectors.LOCK_ACTION_ENCRYPTED
    )


def test_decode_encrypted_spec_response() -> None:
    message = b"".join(vectors.STATES_RESPONSE_CHUNKS)
    auth_id, command, payload = protocol.decode_encrypted(message, vectors.SHARED_KEY)
    assert auth_id == vectors.AUTH_ID
    assert command == Command.OPENER_STATES
    assert payload == vectors.STATES_RESPONSE_PAYLOAD


def test_decode_encrypted_roundtrip() -> None:
    message = protocol.encode_encrypted(
        vectors.AUTH_ID, Command.STATUS, b"\x00", vectors.SHARED_KEY
    )
    auth_id, command, payload = protocol.decode_encrypted(message, vectors.SHARED_KEY)
    assert (auth_id, command, payload) == (vectors.AUTH_ID, Command.STATUS, b"\x00")


def test_decode_encrypted_rejects_wrong_key() -> None:
    message = b"".join(vectors.STATES_RESPONSE_CHUNKS)
    with pytest.raises(NukiProtocolError):
        protocol.decode_encrypted(message, bytes(32))


def test_decode_encrypted_rejects_length_mismatch() -> None:
    message = b"".join(vectors.STATES_RESPONSE_CHUNKS)
    with pytest.raises(NukiProtocolError):
        protocol.decode_encrypted(message[:-1], vectors.SHARED_KEY)


class TestMessageReassembler:
    def test_plain_message_in_chunks(self) -> None:
        reassembler = protocol.MessageReassembler(encrypted=False)
        message = vectors.AUTHORIZATION_ID_MESSAGE
        for chunk_start in range(0, len(message) - 20, 20):
            assert reassembler.feed(message[chunk_start : chunk_start + 20]) == []
        remaining_start = (len(message) - 1) // 20 * 20
        assert reassembler.feed(message[remaining_start:]) == [message]

    def test_plain_single_indication(self) -> None:
        reassembler = protocol.MessageReassembler(encrypted=False)
        assert reassembler.feed(vectors.PUBLIC_KEY_RESPONSE) == [vectors.PUBLIC_KEY_RESPONSE]

    def test_plain_back_to_back_messages(self) -> None:
        reassembler = protocol.MessageReassembler(encrypted=False)
        data = vectors.STATUS_COMPLETE_MESSAGE + vectors.PUBLIC_KEY_RESPONSE
        assert reassembler.feed(data) == [
            vectors.STATUS_COMPLETE_MESSAGE,
            vectors.PUBLIC_KEY_RESPONSE,
        ]

    def test_encrypted_message_in_chunks(self) -> None:
        reassembler = protocol.MessageReassembler(encrypted=True)
        chunks = vectors.STATES_RESPONSE_CHUNKS
        for chunk in chunks[:-1]:
            assert reassembler.feed(chunk) == []
        assert reassembler.feed(chunks[-1]) == [b"".join(chunks)]

    def test_encrypted_back_to_back_messages(self) -> None:
        reassembler = protocol.MessageReassembler(encrypted=True)
        message = b"".join(vectors.STATES_RESPONSE_CHUNKS)
        assert reassembler.feed(message + vectors.STATES_REQUEST_ENCRYPTED) == [
            message,
            vectors.STATES_REQUEST_ENCRYPTED,
        ]

    def test_reset_clears_partial_data(self) -> None:
        reassembler = protocol.MessageReassembler(encrypted=True)
        reassembler.feed(vectors.STATES_RESPONSE_CHUNKS[0])
        reassembler.reset()
        message = b"".join(vectors.STATES_RESPONSE_CHUNKS)
        assert reassembler.feed(message) == [message]

    def test_buffer_overflow_protection(self) -> None:
        reassembler = protocol.MessageReassembler(encrypted=False)
        with pytest.raises(NukiProtocolError):
            # An unknown command that never yields a valid CRC.
            reassembler.feed(bytes([0xEE] * 5000))
