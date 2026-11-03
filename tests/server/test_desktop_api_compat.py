"""Static contract equivalence and HTTP permission seams; no live DB/network."""

import json
import os
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["DATABASE_URL"] = "postgresql://gitviz_test:unused@localhost/gitviz_test"
os.environ["DATABASE_URL_ASYNC"] = ""
from app.schemas import ai, community
from alembic.config import Config
from alembic.script import ScriptDirectory


class Compatibility(unittest.TestCase):
    def test_exported_request_schemas_equal_existing_server_models(self):
        bodies = json.loads((ROOT / "contracts/desktop/v1/ai-bodies.json").read_text(encoding="utf8"))
        for name, schema in bodies.items():
            self.assertEqual(schema, getattr(ai, name).model_json_schema(), name)
        bodies = json.loads((ROOT / "contracts/desktop/v1/community-bodies.json").read_text(encoding="utf8"))
        for name, schema in bodies.items():
            self.assertEqual(schema, getattr(community, name).model_json_schema(), name)

    def test_single_additive_head(self):
        directory = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
        # 2026-11-01 (AD-25, AD-27): the community tables and then chat history sit on top of the desktop sessions.
        # 2026-11-03 (AD-32): AI insight reports and conversations are added on top of chat history.
        self.assertEqual(directory.get_heads(), ["f1b3d5e7a9c2"])
        self.assertEqual(directory.get_revision("f1b3d5e7a9c2").down_revision, "d4f6a8c0e2b3")
        self.assertEqual(directory.get_revision("d4f6a8c0e2b3").down_revision, "c3e5a7b9d1f2")
        self.assertEqual(directory.get_revision("c3e5a7b9d1f2").down_revision, "b7e1d9c3a502")
        self.assertEqual(directory.get_revision("b7e1d9c3a502").down_revision, "a9d2f4c6e8b0")
        for name in ("b7e1d9c3a502_desktop_sessions.py", "c3e5a7b9d1f2_community.py",
                     "d4f6a8c0e2b3_community_chat.py", "f1b3d5e7a9c2_ai_insights.py"):
            migration = (ROOT / "migrations/versions" / name).read_text(encoding="utf8")
            self.assertNotIn("ALTER TABLE", migration)
            self.assertNotIn("DROP TABLE", migration)

    def test_existing_pages_local_scripts_and_no_executable_attributes(self):
        for name in ["timeline", "network", "code-analysis", "search", "myprojects", "history", "ai-review"]:
            html = (ROOT / f"frontend/pages/{name}.html").read_text(encoding="utf8")
            self.assertNotRegex(html, r'<script[^>]+src="https?://')
            self.assertNotRegex(html, r"\bon\w+\s*=")
        for path in (ROOT / "frontend/js").glob("*.js"):
            self.assertNotRegex(path.read_text(encoding="utf8"), r'\bon(?:click|change|input|keydown)\s*=\s*["\x27]')

    def test_approved_vendor_bytes_match_manifest(self):
        import hashlib

        root = ROOT / "frontend/vendor"
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf8"))
        for record in manifest["files"]:
            data = (root / record["file"]).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), record["sha256"])
            self.assertEqual(len(data), record["bytes"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
