from __future__ import annotations

import unittest

from app.workspace.crypto import PayloadCipher


class CryptoTests(unittest.TestCase):
    def test_roundtrip_restart_and_nonce(self):
        first = PayloadCipher(bytes(range(32)), 1)
        encrypted = first.encrypt(b"private-code-canary", b"project/a/schema1")
        self.assertNotIn(b"private-code-canary", encrypted)
        self.assertNotEqual(encrypted, first.encrypt(b"private-code-canary", b"project/a/schema1"))
        self.assertEqual(PayloadCipher(bytes(range(32)), 1).decrypt(encrypted, b"project/a/schema1"), b"private-code-canary")

    def test_changed_aad_key_version_and_payload_rejected(self):
        cipher = PayloadCipher(bytes(range(32)), 1)
        encrypted = cipher.encrypt(b"secret", b"expected")
        for other, data, aad in ((cipher, encrypted, b"different"),
                                 (PayloadCipher(bytes(32), 1), encrypted, b"expected"),
                                 (PayloadCipher(bytes(range(32)), 2), encrypted, b"expected"),
                                 (cipher, encrypted[:-4] + b'xx"}', b"expected")):
            with self.assertRaisesRegex(ValueError, "^DECRYPTION_FAILED$"):
                other.decrypt(data, aad)
