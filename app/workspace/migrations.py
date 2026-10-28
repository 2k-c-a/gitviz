"""Foundation schema; network and server migrations are deliberately unrelated."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

SCHEMA_VERSION = 8


def version(connection: sqlite3.Connection) -> int:
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_meta'").fetchone():
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchone():
            raise ValueError("UNSUPPORTED_SCHEMA")
        return 0
    rows = connection.execute("SELECT version FROM schema_meta ORDER BY version").fetchall()
    if rows not in ([(1,)], [(2,)], [(3,)], [(4,)], [(5,)], [(6,)], [(7,)], [(SCHEMA_VERSION,)]):
        raise ValueError("UNSUPPORTED_SCHEMA")
    tables: set[str] = set()
    if rows[0][0] >= 2:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"schema_meta", "local_project", "project_setting", "local_operation"} <= tables:
            raise ValueError("UNSUPPORTED_SCHEMA")
        if rows[0][0] >= 3 and not {'change_snapshot', 'change_file', 'command_plan'} <= tables:
            raise ValueError('UNSUPPORTED_SCHEMA')
    if rows[0][0] >= 4:
        from .review_schema import TABLES
        if not TABLES <= tables:
            raise ValueError('UNSUPPORTED_SCHEMA')
    if rows[0][0] >= 5:
        from .proposal_schema import TABLES as PROPOSAL_TABLES
        if not PROPOSAL_TABLES <= tables:
            raise ValueError('UNSUPPORTED_SCHEMA')
    if rows[0][0] >= 6:
        from .validation_schema import TABLES as VALIDATION_TABLES
        if not VALIDATION_TABLES <= tables:
            raise ValueError('UNSUPPORTED_SCHEMA')
    if rows[0][0] >= 7:
        from .notification_schema import TABLES as NOTIFICATION_TABLES
        if not NOTIFICATION_TABLES <= tables:
            raise ValueError('UNSUPPORTED_SCHEMA')
    if rows[0][0] >= 8:
        from .remote_schema import TABLES as REMOTE_TABLES
        if not REMOTE_TABLES <= tables:
            raise ValueError('UNSUPPORTED_SCHEMA')
    return rows[0][0]


def migrate(connection: sqlite3.Connection) -> None:
    if version(connection) == SCHEMA_VERSION:
        return
    # Rebuild only the review kind CHECK. Child rows remain untouched; validate
    # all FKs before commit and restore enforcement even on a failed transaction.
    connection.execute('PRAGMA foreign_keys=OFF')
    try:
        connection.execute("BEGIN IMMEDIATE")
        if version(connection) == 0:
            connection.execute("CREATE TABLE schema_meta (version INTEGER PRIMARY KEY CHECK(version=1), "
                           "applied_at TEXT NOT NULL, app_version TEXT NOT NULL)")
            connection.execute("INSERT INTO schema_meta VALUES (?,?,?)",
                               (1, datetime.now(timezone.utc).isoformat(), "0.1.0"))
        if version(connection) < 2:
            _projects(connection)
        if version(connection) < 3:
            connection.execute("CREATE TABLE change_snapshot (id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES local_project(id) ON DELETE CASCADE,"
                               "head_oid TEXT,object_format TEXT NOT NULL CHECK(object_format IN ('sha1','sha256')),index_hash TEXT NOT NULL,input_hash TEXT NOT NULL,"
                               "scope TEXT NOT NULL CHECK(json_valid(scope)),captured_at TEXT NOT NULL,coverage TEXT NOT NULL CHECK(json_valid(coverage)),"
                               "expires_at REAL,manifest TEXT NOT NULL CHECK(json_valid(manifest)),UNIQUE(project_id,id))")
            connection.execute("CREATE INDEX ix_change_snapshot_project ON change_snapshot(project_id,captured_at,id)")
            connection.execute("CREATE TABLE change_file (id TEXT PRIMARY KEY,project_id TEXT NOT NULL,snapshot_id TEXT NOT NULL,path TEXT NOT NULL,old_path TEXT,"
                               "facet TEXT NOT NULL CHECK(facet IN ('staged','unstaged','untracked','selected')),before_hash TEXT,after_hash TEXT,payload BLOB,key_version INTEGER,"
                               "line_map TEXT NOT NULL CHECK(json_valid(line_map)),content_state TEXT NOT NULL CHECK(content_state IN ('read','excluded')),exclusion_reason TEXT,"
                               "FOREIGN KEY(project_id,snapshot_id) REFERENCES change_snapshot(project_id,id) ON DELETE CASCADE,"
                               "UNIQUE(snapshot_id,path,facet),CHECK((content_state='read' AND payload IS NOT NULL AND (before_hash IS NOT NULL OR after_hash IS NOT NULL)) OR (content_state='excluded' AND payload IS NULL)))")
            connection.execute("CREATE TABLE command_plan (id TEXT PRIMARY KEY,project_id TEXT NOT NULL,snapshot_id TEXT NOT NULL,executable TEXT NOT NULL,argv TEXT NOT NULL CHECK(json_valid(argv)),"
                               "cwd_relative TEXT NOT NULL,config_hash TEXT NOT NULL,env_allowlist TEXT NOT NULL CHECK(json_valid(env_allowlist)),timeout_ms INTEGER NOT NULL,output_limit INTEGER NOT NULL,"
                               "risk TEXT NOT NULL,plan_hash TEXT NOT NULL,payload BLOB NOT NULL,FOREIGN KEY(project_id,snapshot_id) REFERENCES change_snapshot(project_id,id) ON DELETE CASCADE)")
            for table in ('change_snapshot', 'change_file', 'command_plan'):
                connection.execute(f"CREATE TRIGGER immutable_{table} BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT,'IMMUTABLE_INPUT'); END")
        if version(connection) < 4:
            from .review_schema import create
            create(connection)
        if version(connection) < 5:
            from .proposal_schema import create as create_proposals
            create_proposals(connection)
        if version(connection) < 6:
            from .validation_schema import create as create_validation
            create_validation(connection)
        if version(connection) < 7:
            from .notification_schema import create as create_notifications
            create_notifications(connection)
        if version(connection) < 8:
            from .remote_schema import create as create_remote
            create_remote(connection)
        connection.execute("ALTER TABLE schema_meta RENAME TO schema_meta_previous")
        connection.execute("CREATE TABLE schema_meta (version INTEGER PRIMARY KEY CHECK(version=8), applied_at TEXT NOT NULL, app_version TEXT NOT NULL)")
        connection.execute("INSERT INTO schema_meta VALUES (?,?,?)", (SCHEMA_VERSION, datetime.now(timezone.utc).isoformat(), "0.8.0"))
        connection.execute("DROP TABLE schema_meta_previous")
        if connection.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise ValueError('MIGRATION_INTEGRITY_FAILED')
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute('PRAGMA foreign_keys=ON')


def _projects(connection: sqlite3.Connection) -> None:
        connection.execute("CREATE TABLE local_project (id TEXT PRIMARY KEY,canonical_root TEXT NOT NULL UNIQUE,"
                           "root_identity TEXT NOT NULL,git_dir TEXT NOT NULL,common_dir TEXT NOT NULL,"
                           "metadata_grant TEXT NOT NULL CHECK(json_valid(metadata_grant)),display_name TEXT NOT NULL,"
                           "version INTEGER NOT NULL CHECK(version>=1))")
        connection.execute("CREATE TABLE project_setting (project_id TEXT NOT NULL REFERENCES local_project(id) ON DELETE CASCADE,"
                           "key TEXT NOT NULL CHECK(key='recentLimit'),value TEXT NOT NULL CHECK(json_valid(value)),"
                           "version INTEGER NOT NULL CHECK(version>=1),PRIMARY KEY(project_id,key))")
        connection.execute("CREATE TABLE local_operation (project_scope TEXT NOT NULL,operation TEXT NOT NULL,"
                           "idempotency_key TEXT NOT NULL,body_hash TEXT NOT NULL,result TEXT NOT NULL CHECK(json_valid(result)),"
                           "PRIMARY KEY(project_scope,operation,idempotency_key))")
