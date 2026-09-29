"""AES-256-GCM encryption for secrets at rest (ADR-0009)."""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

CURRENT_KEY_VERSION = 1


@dataclass(frozen=True)
class EncryptedBlob:
    ciphertext: bytes
    nonce: bytes
    key_version: int


class SecretCipher:
    def __init__(self, master_key_b64: str) -> None:
        try:
            key = base64.b64decode(master_key_b64, validate=True)
        except Exception as exc:  # noqa: BLE001
            raise ValueError("YTL_MASTER_KEY must be base64-encoded") from exc
        if len(key) != 32:
            raise ValueError("YTL_MASTER_KEY must decode to exactly 32 bytes")
        self._keys = {CURRENT_KEY_VERSION: AESGCM(key)}

    def encrypt(self, plaintext: str, *, purpose: str) -> EncryptedBlob:
        nonce = os.urandom(12)
        ct = self._keys[CURRENT_KEY_VERSION].encrypt(nonce, plaintext.encode("utf-8"), purpose.encode())
        return EncryptedBlob(ciphertext=ct, nonce=nonce, key_version=CURRENT_KEY_VERSION)

    def decrypt(self, blob: EncryptedBlob, *, purpose: str) -> str:
        aes = self._keys.get(blob.key_version)
        if aes is None:
            raise ValueError(f"Unknown secret key version {blob.key_version}")
        return aes.decrypt(blob.nonce, blob.ciphertext, purpose.encode()).decode("utf-8")
