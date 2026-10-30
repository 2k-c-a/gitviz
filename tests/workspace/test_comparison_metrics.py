from __future__ import annotations

import unittest

from app.workspace.comparisons import measure
from app.workspace.detection import check_script_modes
from app.workspace.diagnostics import extract
from app.workspace.scoped_files import ScopeError


class MetricTests(unittest.TestCase):
    def test_metrics_are_reproducible_and_unsupported_is_not_zero(self) -> None:
        files = {'app.py': b'def own(x):\n    if x and x > 1: return 1\n    return 0\n', 'app.ts': b'const x = 1;\n'}
        result = measure(files)
        self.assertEqual(result, measure(files))
        self.assertEqual(result['pythonFunctionCount'], 1)
        self.assertEqual(result['pythonBranchMetric'], 3)
        self.assertEqual(result['denominatorPythonFiles'], 1)
        self.assertEqual(result['unsupported'], ['app.ts'])
        self.assertIsNone(result['duplication'])
        self.assertIsNone(measure({'app.ts': b'const x = 1;'})['pythonBranchMetric'])
        self.assertIsNone(measure({'broken.py': b'def ('})['pythonBranchMetric'])

    def test_duplicate_token_windows_have_explicit_denominator_and_no_semantic_claim(self) -> None:
        source = b'def own(x):\n    if x and x > 1:\n        return x + 1\n    else:\n        return x - 1\n'
        value = measure({'a.py': source, 'b.py': source})
        self.assertGreater(value['duplication']['tokenWindows'], 0)
        self.assertEqual(value['duplication']['tokenWindows'], value['duplication']['duplicatedTokenWindows'])
        self.assertEqual(value['denominatorPythonDuplicationFiles'], 2)

    def test_only_selected_snapshot_diagnostic_locations_are_linked(self) -> None:
        snapshot = {'files': [{'fileId': 'fixture', 'path': 'app.py', 'state': 'read', 'afterHash': 'a' * 64}]}
        output = [{'stream': 'stderr', 'text': 'C:\\fixture\\app.py:2:1: F401 unused\n../secret.py:1:1: F401 hidden\napp.py:0:1: F401 bad\napp.py:2:1: F401 duplicate\n'}]
        result = extract(output, snapshot, 'C:\\fixture')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['fileId'], 'fixture')
        self.assertEqual(result[0]['line'], 2)
        self.assertNotIn('unused', str(result))

    def test_known_install_and_fix_modes_are_not_validation_candidates(self) -> None:
        for command in ['eslint --fix .', 'ruff check --fix-only .', 'npm ci', 'pnpm install', 'yarn add dependency', 'python -m pip install fixture', 'prettier --write .']:
            with self.subTest(command=command), self.assertRaisesRegex(ScopeError, 'COMMAND_NOT_ALLOWED'):
                check_script_modes({'test': command})
        check_script_modes({'pretest': 'node prepare.cjs', 'test': 'npm run unit', 'posttest': 'node report.cjs'})
