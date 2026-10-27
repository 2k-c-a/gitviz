from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.workspace.scoped_files import ScopeError, canonical, discover, pinned, verify


class ScopedFilesTests(unittest.TestCase):
    def test_root_identity_and_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            (root / ".git").mkdir()
            grant = discover(root)
            verify(grant)
            root.rename(root.with_name("old"))
            root.mkdir()
            (root / ".git").mkdir()
            with self.assertRaisesRegex(ScopeError, "PROJECT_MOVED"):
                verify(grant)
            with self.assertRaisesRegex(ScopeError, "PATH_OUTSIDE_SCOPE"):
                canonical(Path("relative"))

    @unittest.skipUnless(os.name == "nt", "real Windows junction and pinned handles")
    def test_junction_escape_and_root_rename_are_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, outside = Path(tmp) / "repo", Path(tmp) / "outside"
            root.mkdir()
            outside.mkdir()
            (root / ".git").mkdir()
            grant = discover(root)
            with pinned(grant):
                with self.assertRaises(OSError):
                    root.rename(root.with_name("moved"))
            junction = root / "escape"
            subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32/cmd.exe"), "/d", "/c", "mklink", "/J", str(junction), str(outside)], check=True, capture_output=True)
            try:
                with self.assertRaisesRegex(ScopeError, "PATH_OUTSIDE_SCOPE"):
                    with pinned(grant):
                        self.fail("junction must be rejected before yielding")
            finally:
                os.rmdir(junction)
