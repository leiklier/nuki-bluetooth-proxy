"""Tests for the crypto primitives against official spec vectors."""

import pytest

from custom_components.nuki_opener_ble.nuki import crypto

from . import vectors


def test_public_key_derivation() -> None:
    assert crypto.public_key_from_private(vectors.CL_PRIVATE_KEY) == vectors.CL_PUBLIC_KEY


def test_shared_key_derivation() -> None:
    # dh1 followed by kdf1, i.e. crypto_box_beforenm.
    shared = crypto.derive_shared_key(vectors.CL_PRIVATE_KEY, vectors.DEVICE_PUBLIC_KEY)
    assert shared == vectors.SHARED_KEY


def test_hmac_sha256_authenticator() -> None:
    value_r = vectors.CL_PUBLIC_KEY + vectors.DEVICE_PUBLIC_KEY + vectors.CHALLENGE_1
    assert crypto.hmac_sha256(vectors.SHARED_KEY, value_r) == vectors.AUTHENTICATOR_1


def test_encrypt_spec_vector() -> None:
    plaintext = bytes.fromhex("020000000100 0C00 418D".replace(" ", ""))
    nonce, ciphertext = crypto.encrypt(plaintext, vectors.SHARED_KEY, vectors.STATES_REQUEST_NONCE)
    assert nonce + ciphertext == (
        vectors.STATES_REQUEST_NONCE + vectors.STATES_REQUEST_ENCRYPTED[30:]
    )


def test_encrypt_decrypt_roundtrip() -> None:
    key = crypto.random_bytes(crypto.KEY_SIZE)
    nonce, ciphertext = crypto.encrypt(b"hello opener", key)
    assert len(nonce) == crypto.SECRETBOX_NONCE_SIZE
    assert crypto.decrypt(nonce, ciphertext, key) == b"hello opener"


def test_decrypt_rejects_tampering() -> None:
    key = crypto.random_bytes(crypto.KEY_SIZE)
    nonce, ciphertext = crypto.encrypt(b"hello opener", key)
    import nacl.exceptions

    with pytest.raises(nacl.exceptions.CryptoError):
        crypto.decrypt(nonce, bytes([ciphertext[0] ^ 0xFF]) + ciphertext[1:], key)
