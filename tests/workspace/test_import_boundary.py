from __future__ import annotations

import subprocess
import sys
import unittest


class ImportBoundaryTests(unittest.TestCase):
    def test_agent_import_has_no_server_side_effects(self):
        code = """
import sys
import app.workspace.__main__
assert not any(x in sys.modules for x in ('app.main','app.database','app.core.database','celery','sqlalchemy','dotenv'))
"""
        result = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
