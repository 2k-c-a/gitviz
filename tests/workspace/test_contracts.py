from __future__ import annotations

import io
import json
import unittest
from pathlib import Path

from app.workspace.contracts import MAX_FRAME, ProtocolError, decode, read_frame, validate


class ContractTests(unittest.TestCase):
    def test_shared_fixtures(self):
        root = Path(__file__).resolve().parents[2]
        for fixture in json.loads((root / "contracts/workspace/v1/fixtures.json").read_text()):
            with self.subTest(fixture=fixture["name"]):
                if fixture["valid"]:
                    validate(fixture["schema"], fixture["value"])
                else:
                    with self.assertRaises(ProtocolError):
                        validate(fixture["schema"], fixture["value"])

    def test_parser_rejects_ambiguous_or_unbounded_input(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e999}', b'\xff', b'"\\ud800"',
                    b'[' * 17 + b'0' + b']' * 17, b' ' * (MAX_FRAME + 1), b''):
            with self.subTest(raw=raw[:40]), self.assertRaises(ProtocolError):
                decode(raw)

    def test_frames_and_incomplete_eof(self):
        stream = io.BytesIO(b'{"x":1}\n{"x":2}\n')
        self.assertEqual(read_frame(stream), {"x": 1})
        self.assertEqual(read_frame(stream), {"x": 2})
        self.assertIsNone(read_frame(stream))
        with self.assertRaises(ProtocolError):
            read_frame(io.BytesIO(b'{"x":1}'))
