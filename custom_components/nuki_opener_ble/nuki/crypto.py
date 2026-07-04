"""Cryptography for the Nuki BLE protocol.

The protocol uses the NaCl toolbox:

- ``dh1``:  X25519 (``crypto_scalarmult_curve25519``)
- ``kdf1``: HSalsa20 with a zero nonce and the "expand 32-byte k" constant
- ``h1``:   HMAC-SHA256
- ``e1``:   XSalsa20-Poly1305 (``crypto_secretbox``)

``dh1`` followed by ``kdf1`` is exactly NaCl's ``crypto_box_beforenm``.
"""

from __future__ import annotations

import hashlib
import hmac as _hmac

import nacl.bindings
import nacl.public
import nacl.secret
import nacl.utils

KEY_SIZE = 32
SECRETBOX_NONCE_SIZE = nacl.secret.SecretBox.NONCE_SIZE  # 24
CHALLENGE_NONCE_SIZE = 32


def generate_keypair() -> tuple[bytes, bytes]:
    """Generate an X25519 keypair; returns ``(private, public)``."""
    key = nacl.public.PrivateKey.generate()
    return bytes(key), bytes(key.public_key)


def public_key_from_private(private_key: bytes) -> bytes:
    """Derive the X25519 public key for a private key."""
    return bytes(nacl.public.PrivateKey(private_key).public_key)


def derive_shared_key(private_key: bytes, peer_public_key: bytes) -> bytes:
    """Derive the long-term shared secret (dh1 + kdf1)."""
    return nacl.bindings.crypto_box_beforenm(peer_public_key, private_key)


def hmac_sha256(key: bytes, data: bytes) -> bytes:
    """Authentication function h1."""
    return _hmac.new(key, data, hashlib.sha256).digest()


def encrypt(plaintext: bytes, key: bytes, nonce: bytes | None = None) -> tuple[bytes, bytes]:
    """Encrypt with XSalsa20-Poly1305; returns ``(nonce, ciphertext)``.

    The ciphertext includes the 16-byte Poly1305 authenticator.
    """
    if nonce is None:
        nonce = nacl.utils.random(SECRETBOX_NONCE_SIZE)
    box = nacl.secret.SecretBox(key)
    return nonce, box.encrypt(plaintext, nonce).ciphertext


def decrypt(nonce: bytes, ciphertext: bytes, key: bytes) -> bytes:
    """Decrypt an XSalsa20-Poly1305 ciphertext.

    Raises ``nacl.exceptions.CryptoError`` if authentication fails.
    """
    box = nacl.secret.SecretBox(key)
    return box.decrypt(ciphertext, nonce)


def random_bytes(size: int) -> bytes:
    """Return cryptographically secure random bytes."""
    return nacl.utils.random(size)
