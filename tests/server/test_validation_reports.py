"""Report-output checks without application imports, DB, or network."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.server import validation_reports


class ValidationReportOutput(unittest.TestCase):
    def test_default_is_outside_documentation_and_names_are_unique(self):
        a = validation_reports.new_report_path('snapshot-integration')
        b = validation_reports.new_report_path('snapshot-integration')
        self.assertEqual(a.parent, validation_reports.REPORT_ROOT)
        self.assertEqual(a.parent.parts[-2:], ('reports', 'validation'))
        self.assertNotEqual(a, b)

    def test_report_preserves_payload_and_does_not_overwrite(self):
        # TemporaryDirectory owns this newly created path and cleans only its contents.
        with tempfile.TemporaryDirectory(prefix='gitviz-report-test-') as folder:
            root = Path(folder).resolve()
            with patch.object(validation_reports, 'REPORT_ROOT', root):
                payload = {'authorization_mode': 'test_fixture', 'checks': [{'passed': False, 'note': '검사'}]}
                first = validation_reports.write_validation_report('snapshot-integration', payload)
                second = validation_reports.write_validation_report('snapshot-integration', [])
                self.assertEqual(json.loads(first.read_text(encoding='utf-8')), payload)
                self.assertNotEqual(first, second)
                with patch.object(validation_reports, 'new_report_path', return_value=first):
                    with self.assertRaises(FileExistsError):
                        validation_reports.write_validation_report('snapshot-integration', [])
                self.assertEqual(json.loads(first.read_text(encoding='utf-8')), payload)

    def test_kind_cannot_escape_report_directory(self):
        for kind in ('../archive', '/tmp/file', 'A', 'a/b', ''):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                validation_reports.new_report_path(kind)


if __name__ == '__main__':
    unittest.main()
