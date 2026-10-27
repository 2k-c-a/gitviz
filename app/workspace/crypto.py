"""Versioned AEAD envelopes; no plaintext persistence or diagnostic payloads."""
from __future__ import annotations

import base64
import json
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class PayloadCipher:
    def __init__(self, key: bytes, key_version: int):
        if len(key) != 32 or type(key_version) is not int or key_version < 1:
            raise ValueError("INVALID_KEY")
        self._cipher = AESGCM(key)
        self.key_version = key_version

    def encrypt(self, value: bytes, aad: bytes) -> bytes:
        nonce = secrets.token_bytes(12)
        return json.dumps({"keyVersion": self.key_version,
                           "nonce": base64.b64encode(nonce).decode("ascii"),
                           "ciphertext": base64.b64encode(self._cipher.encrypt(nonce, value, aad)).decode("ascii")},
                          separators=(",", ":")).encode("ascii")

    def decrypt(self, envelope: bytes, aad: bytes) -> bytes:
        try:
            data = json.loads(envelope)
            if set(data) != {"keyVersion", "nonce", "ciphertext"} or type(data["keyVersion"]) is not int:
                raise ValueError()
            if data["keyVersion"] != self.key_version:
                raise ValueError()
            nonce = base64.b64decode(data["nonce"], validate=True)
            if len(nonce) != 12:
                raise ValueError()
            return self._cipher.decrypt(nonce, base64.b64decode(data["ciphertext"], validate=True), aad)
        except Exception:
            raise ValueError("DECRYPTION_FAILED") from None
