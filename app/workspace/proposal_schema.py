"""Additive schema5 proposal/journal storage; called only by the versioned migration."""
from __future__ import annotations

import sqlite3

TABLES = {'proposal', 'proposal_event', 'approval', 'apply_journal', 'impact_analysis'}


def create(connection: sqlite3.Connection) -> None:
    old = connection.execute("SELECT sql FROM sqlite_master WHERE name='review_run'").fetchone()[0]
    if "CHECK(kind='review')" in old:
        updated = old.replace('CREATE TABLE review_run', 'CREATE TABLE review_run_v5', 1).replace("CHECK(kind='review')", "CHECK(kind IN ('review','refactor'))")
        connection.execute(updated)
        connection.execute('INSERT INTO review_run_v5 SELECT * FROM review_run')
        connection.execute('DROP TABLE review_run')
        connection.execute('ALTER TABLE review_run_v5 RENAME TO review_run')
        connection.execute('CREATE INDEX ix_review_project ON review_run(project_id,created_at,id)')
    statements = [
        """CREATE TABLE proposal(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,snapshot_id TEXT NOT NULL,
        review_run_id TEXT NOT NULL UNIQUE,previous_proposal_id TEXT,version INTEGER NOT NULL CHECK(version>=1),
        status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected','superseded','applying','applied','partial','recovery_required','restored')),
        payload BLOB NOT NULL,patch_hash TEXT NOT NULL,preconditions_hash TEXT NOT NULL,created_at REAL NOT NULL,
        UNIQUE(project_id,id),FOREIGN KEY(project_id,review_run_id,snapshot_id) REFERENCES review_run(project_id,id,snapshot_id),
        FOREIGN KEY(project_id,previous_proposal_id) REFERENCES proposal(project_id,id))""",
        """CREATE TABLE proposal_event(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,proposal_id TEXT NOT NULL,
        version INTEGER NOT NULL,status TEXT NOT NULL,occurred_at REAL NOT NULL,
        FOREIGN KEY(project_id,proposal_id) REFERENCES proposal(project_id,id),UNIQUE(proposal_id,version))""",
        """CREATE TABLE approval(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,proposal_id TEXT NOT NULL,
        target_type TEXT NOT NULL CHECK(target_type IN ('proposal','recovery')),target_hash TEXT NOT NULL,
        preconditions_hash TEXT NOT NULL,capability_hash TEXT NOT NULL,approved_at REAL NOT NULL,expires_at REAL NOT NULL,
        consumed_at REAL,revoked_at REAL,FOREIGN KEY(project_id,proposal_id) REFERENCES proposal(project_id,id))""",
        """CREATE TABLE apply_journal(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,proposal_id TEXT NOT NULL,
        relative_path TEXT NOT NULL,preimage_hash TEXT NOT NULL,target_hash TEXT NOT NULL,backup BLOB NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('prepared','original_moved','replaced','verified','recovery_required','restored')),
        metadata TEXT NOT NULL CHECK(json_valid(metadata)),FOREIGN KEY(project_id,proposal_id) REFERENCES proposal(project_id,id),
        UNIQUE(proposal_id,relative_path))""",
        """CREATE TABLE impact_analysis(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,snapshot_id TEXT NOT NULL,
        input_hash TEXT NOT NULL,payload BLOB NOT NULL,created_at REAL NOT NULL,
        FOREIGN KEY(project_id,snapshot_id) REFERENCES change_snapshot(project_id,id))""",
    ]
    for sql in statements:
        connection.execute(sql)
