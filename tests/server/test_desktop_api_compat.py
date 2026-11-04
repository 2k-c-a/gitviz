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
        # 2026-11-03: '내 분석 프로젝트'에서 숨김(repository_access.hidden_at, nullable 컬럼 추가만).
        # 2026-11-04 (AD-33): AI 리뷰를 앱 전용 BYOK로 바꾸며 서버에 저장했던 key·코드 원문을 지운다(데이터 삭제만, 표·열 유지).
        self.assertEqual(directory.get_heads(), ["39b676ca36f2"])
        self.assertEqual(directory.get_revision("39b676ca36f2").down_revision, "e7a9c1d3f5b4")
        purge = (ROOT / "migrations/versions/39b676ca36f2_ai_review_byok_purge_server_secrets.py").read_text(encoding="utf8")
        upgrade, downgrade = purge.split("def downgrade")
        self.assertNotIn("DROP TABLE", upgrade)
        self.assertNotIn("DROP COLUMN", upgrade)
        self.assertIn("raise RuntimeError", downgrade)
        self.assertEqual(directory.get_revision("e7a9c1d3f5b4").down_revision, "f1b3d5e7a9c2")
        # 2026-11-03 (AD-32): AI insight reports and conversations are added on top of chat history.
        self.assertEqual(directory.get_revision("f1b3d5e7a9c2").down_revision, "d4f6a8c0e2b3")
        hidden = (ROOT / "migrations/versions/e7a9c1d3f5b4_repository_access_hidden.py").read_text(encoding="utf8")
        self.assertIn("ALTER TABLE repository_access ADD COLUMN hidden_at TIMESTAMP WITH TIME ZONE')", hidden)
        self.assertNotIn("DROP", hidden.split("def downgrade")[0])
        self.assertNotIn("NOT NULL", hidden)
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
