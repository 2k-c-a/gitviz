"""Execute only in a dedicated gitviz_test database, isolated temporary schema."""

import sys
import os
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
import unittest
from uuid import uuid4
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ai_test_support as support

URL = support.require_test_database()
sys.path.insert(0, str(support.ROOT))
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.exc import DBAPIError


class Migration(unittest.TestCase):
    def test_expand_preserves_old_tables_and_records_and_refuses_destructive_downgrade(self):
        engine = create_engine(URL)
        schema = "gitviz_p03_migration_" + uuid4().hex
        try:
            with engine.connect() as conn:
                conn.execute(text(f"CREATE SCHEMA {schema}"))
                conn.execute(text(f"SET search_path TO {schema}"))
                conn.commit()
                config = Config(str(support.ROOT / "alembic.ini"))
                config.attributes["connection"] = conn
                command.upgrade(config, "a9d2f4c6e8b0")
                conn.commit()
                uid = str(uuid4())
                conn.execute(
                    text("INSERT INTO users(id,github_id,github_login) VALUES(:id,123456,'keep-desktop-migration')"),
                    {"id": uid},
                )
                rid = conn.execute(
                    text(
                        "INSERT INTO repositories(url,name,github_id,is_private) VALUES('https://github.com/fixture/p03','p03',123456,false) RETURNING id"
                    )
                ).scalar()
                conn.execute(
                    text(
                        "INSERT INTO analysis_snapshots(repository_id,target_sha,analyzed_at,window_end,formula_version,parameters,payload,payload_sha256) VALUES(:r,:s,now(),now(),'v1','{}','{}',:h)"
                    ),
                    {"r": rid, "s": "a" * 40, "h": "b" * 64},
                )
                conn.commit()
                before = set(inspect(conn).get_table_names(schema=schema))
                # Preserve every row of every existing table, not only representative counts.
                previous = {
                    name: conn.execute(
                        text(f"SELECT row_to_json(t)::text FROM {name} t ORDER BY row_to_json(t)::text")
                    ).all()
                    for name in before
                }
                conn.commit()
                # Fail at the second new table: PostgreSQL must roll back the first
                # new table and migration version while preserving all old data.
                conn.execute(text("CREATE TABLE desktop_session (fixture_conflict integer)"))
                conn.commit()
                with self.assertRaises(DBAPIError):
                    command.upgrade(config, "head")
                conn.rollback()
                self.assertNotIn("desktop_auth_transaction", inspect(conn).get_table_names(schema=schema))
                self.assertEqual(conn.execute(text("SELECT version_num FROM alembic_version")).scalar(), "a9d2f4c6e8b0")
                self.assertEqual(
                    conn.execute(text("SELECT github_login FROM users")).scalar(), "keep-desktop-migration"
                )
                conn.execute(text("DROP TABLE desktop_session"))  # This test's conflict fixture only.
                conn.commit()
                command.upgrade(config, "head")
                conn.commit()
                after = set(inspect(conn).get_table_names(schema=schema))
                self.assertEqual(after - before, {"desktop_auth_transaction", "desktop_session"})
                for name, rows in previous.items():
                    if name != "alembic_version":
                        self.assertEqual(
                            conn.execute(
                                text(f"SELECT row_to_json(t)::text FROM {name} t ORDER BY row_to_json(t)::text")
                            ).all(),
                            rows,
                        )
                conn.commit()
                with self.assertRaises(RuntimeError):
                    command.downgrade(config, "a9d2f4c6e8b0")
                conn.rollback()
                self.assertIn("desktop_session", inspect(conn).get_table_names(schema=schema))
                conn.commit()
        finally:
            with engine.begin() as conn:
                conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
            engine.dispose()


if __name__ == "__main__":
    unittest.main(verbosity=2)
