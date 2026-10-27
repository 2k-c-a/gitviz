from __future__ import annotations

import hashlib
import unittest
import uuid

from app.workspace.impact import python_references


class PythonParserTests(unittest.TestCase):
    def test_fixed_ast_locations_unicode_and_direct_calls_without_execution(self) -> None:
        source = 'import os\n이름 = "텍스트"\ndef f():\n    return 1\nf()\nobj.dynamic()\nraise RuntimeError("MUST_NOT_EXECUTE")\n'.encode()
        descriptor = {'fileId': str(uuid.uuid4()), 'path': '한글.py', 'facet': 'selected', 'sourceHash': hashlib.sha256(source).hexdigest()}
        records, limits = python_references(descriptor, source)
        self.assertTrue(any(r['symbol'] == 'f' and r['resolved'] and r['targetLine'] == 3 for r in records))
        self.assertTrue(any(r['symbol'] == 'obj.dynamic' and not r['resolved'] for r in records))
        for record in records:
            self.assertEqual(source[record['startByte']:record['endByte']].decode(), record['symbol'])
            self.assertEqual(record['kind'], 'static_reference')
            self.assertTrue(record['observed'])
        self.assertIn('syntax-only-not-runtime-callgraph', limits)

    def test_import_parameter_assignment_and_decorator_rebinding_are_unresolved(self) -> None:
        for text in ['def f(): pass\nimport f\nf()\n', 'def f(): pass\nf = lambda: 2\nf()\n',
                     'def f(): pass\ndef g(f): f()\n', '@wrap\ndef f(): pass\nf()\n']:
            source = text.encode()
            records, _ = python_references({'fileId': str(uuid.uuid4()), 'path': 'a.py', 'facet': 'staged', 'sourceHash': hashlib.sha256(source).hexdigest()}, source)
            self.assertTrue(all(not r['resolved'] for r in records if r['symbol'] == 'f'))

    def test_bad_syntax_and_bom_do_not_invent_evidence(self) -> None:
        descriptor = {'fileId': str(uuid.uuid4()), 'path': 'a.py', 'facet': 'selected', 'sourceHash': 'a' * 64}
        self.assertEqual(python_references(descriptor, b'def broken(')[0], [])
        source = b'\xef\xbb\xbfdef f(): pass\r\nf()\r\n'
        for record in python_references(descriptor, source)[0]:
            self.assertEqual(source[record['startByte']:record['endByte']].decode(), record['symbol'])
