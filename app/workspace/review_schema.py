"""Additive local review storage; never touches the server database."""
from __future__ import annotations

import sqlite3


TABLES = {'transfer_consent', 'review_run', 'review_attempt', 'local_usage_ledger',
          'review_finding', 'review_feedback'}


def create(connection: sqlite3.Connection) -> None:
    statements = [
        """CREATE TABLE transfer_consent (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL, snapshot_id TEXT NOT NULL,
        manifest_hash TEXT NOT NULL, preview_hash TEXT NOT NULL, profile_fingerprint TEXT NOT NULL,
        payload BLOB NOT NULL, acknowledged_at REAL NOT NULL, expires_at REAL NOT NULL,
        UNIQUE(project_id,id), UNIQUE(project_id,id,snapshot_id),
        FOREIGN KEY(project_id,snapshot_id) REFERENCES change_snapshot(project_id,id))""",
        """CREATE TABLE review_run (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, consent_id TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind='review'), fingerprint TEXT NOT NULL, generation INTEGER NOT NULL CHECK(generation>=1),
        status TEXT NOT NULL CHECK(status IN ('queued','running','cancel_requested','succeeded','failed','cancelled','uncertain')),
        attempt_count INTEGER NOT NULL CHECK(attempt_count BETWEEN 0 AND 1), budget_reserved TEXT NOT NULL,
        outcome_uncertain INTEGER NOT NULL CHECK(outcome_uncertain IN (0,1)), result BLOB, error_code TEXT,
        version INTEGER NOT NULL CHECK(version>=1), created_at REAL NOT NULL, updated_at REAL NOT NULL,
        UNIQUE(project_id,id), UNIQUE(project_id,id,snapshot_id), UNIQUE(fingerprint,generation),
        FOREIGN KEY(project_id,snapshot_id) REFERENCES change_snapshot(project_id,id),
        FOREIGN KEY(project_id,consent_id,snapshot_id) REFERENCES transfer_consent(project_id,id,snapshot_id),
        CHECK((status='uncertain')=outcome_uncertain), CHECK((status='succeeded')=(result IS NOT NULL)))""",
        """CREATE TABLE review_attempt (
        id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES review_run(id),attempt_no INTEGER NOT NULL CHECK(attempt_no=1),
        status TEXT NOT NULL CHECK(status IN ('running','succeeded','failed','cancelled','uncertain')),
        provider_request_id TEXT,usage TEXT CHECK(usage IS NULL OR json_valid(usage)),reserved_cost TEXT NOT NULL,
        started_at REAL NOT NULL,finished_at REAL,UNIQUE(run_id,attempt_no))""",
        """CREATE TABLE local_usage_ledger (
        id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES review_run(id),attempt_no INTEGER NOT NULL CHECK(attempt_no=1),
        provider TEXT NOT NULL,period_start TEXT NOT NULL,reserved_cost TEXT NOT NULL,actual_cost TEXT,
        estimate INTEGER NOT NULL CHECK(estimate IN (0,1)),outcome_uncertain INTEGER NOT NULL CHECK(outcome_uncertain IN (0,1)),
        settled_at REAL,UNIQUE(run_id,attempt_no))""",
        """CREATE TABLE review_finding (
        id TEXT PRIMARY KEY,project_id TEXT NOT NULL,review_run_id TEXT NOT NULL,snapshot_id TEXT NOT NULL,
        file_id TEXT NOT NULL REFERENCES change_file(id),ordinal INTEGER NOT NULL CHECK(ordinal>=0),payload BLOB NOT NULL,
        UNIQUE(project_id,id),UNIQUE(review_run_id,ordinal),
        FOREIGN KEY(project_id,review_run_id,snapshot_id) REFERENCES review_run(project_id,id,snapshot_id))""",
        """CREATE TABLE review_feedback (
        finding_id TEXT PRIMARY KEY REFERENCES review_finding(id),
        verdict TEXT NOT NULL CHECK(verdict IN ('valid','false_positive','uncertain','unverified')),
        note BLOB NOT NULL,version INTEGER NOT NULL CHECK(version>=1),updated_at REAL NOT NULL)""",
        "CREATE INDEX ix_review_project ON review_run(project_id,created_at,id)",
        "CREATE TRIGGER immutable_transfer_consent BEFORE UPDATE ON transfer_consent BEGIN SELECT RAISE(ABORT,'IMMUTABLE_INPUT'); END",
        "CREATE TRIGGER immutable_review_finding BEFORE UPDATE ON review_finding BEGIN SELECT RAISE(ABORT,'IMMUTABLE_RESULT'); END",
        """CREATE TRIGGER review_finding_scope BEFORE INSERT ON review_finding
        WHEN NOT EXISTS(SELECT 1 FROM change_file WHERE id=NEW.file_id AND project_id=NEW.project_id AND snapshot_id=NEW.snapshot_id)
        BEGIN SELECT RAISE(ABORT,'CROSS_PROJECT'); END""",
    ]
    for statement in statements:
        connection.execute(statement)
