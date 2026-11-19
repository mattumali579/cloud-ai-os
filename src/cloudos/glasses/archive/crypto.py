"""Passphrase encryption for archive bytes before they leave the machine.

The passphrase is an argument or an environment variable. It is never written
into the repository. Losing it makes the ciphertext unreadable.
"""

from __future__ import annotations

import base64
import os

_HEADER = b"GLASSES-ARCHIVE-1\n"
_ITERATIONS = 20_000


class ArchiveCryptoError(RuntimeError):
    pass


def encrypt_bytes(plaintext: bytes, passphrase: str, *, salt: bytes | None = None) -> bytes:
    if not passphrase:
        raise ArchiveCryptoError("a passphrase is required before encryption")
    salt = salt or os.urandom(16)
    token = _fernet(passphrase, salt).encrypt(plaintext)
    return _HEADER + salt.hex().encode("ascii") + b"\n" + token


def decrypt_bytes(blob: bytes, passphrase: str) -> bytes:
    if not blob.startswith(_HEADER):
        raise ArchiveCryptoError("not a glasses archive ciphertext")
    rest = blob[len(_HEADER) :]
    line, _, token = rest.partition(b"\n")
    try:
        salt = bytes.fromhex(line.decode("ascii"))
    except ValueError as exc:
        raise ArchiveCryptoError("archive salt is unreadable") from exc
    try:
        return _fernet(passphrase, salt).decrypt(token)
    except Exception as exc:  # noqa: BLE001 — bad key or truncated blob
        raise ArchiveCryptoError("archive ciphertext could not be opened") from exc


def _fernet(passphrase: str, salt: bytes):
    try:
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    except ImportError as exc:
        raise ArchiveCryptoError(
            "archive encryption needs the cryptography package from the glasses extra"
        ) from exc
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=_ITERATIONS)
    key = base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))
    return Fernet(key)
