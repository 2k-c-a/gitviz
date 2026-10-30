"""Additive schema7 local event and inbox storage; called only by the versioned migration."""
from __future__ import annotations

import sqlite3

TABLES = {'workspace_event', 'notification'}

EVENT_TYPES = (
    'review.succeeded', 'review.failed', 'review.cancelled', 'review.uncertain',
    'validation.succeeded', 'validation.failed', 'validation.cancelled', 'validation.timed_out',
    'validation.output_limit', 'validation.interrupted', 'validation.launch_failed', 'validation.precondition_failed',
    'proposal.applied', 'proposal.recovery_required', 'proposal.restored',
)


def create(connection: sqlite3.Connection) -> None:
    types = ','.join(f"'{name}'" for name in EVENT_TYPES)
    connection.execute(
        "CREATE TABLE workspace_event (id TEXT PRIMARY KEY, sequence INTEGER NOT NULL UNIQUE CHECK(sequence>=1),"
        "project_id TEXT NOT NULL REFERENCES local_project(id),"
        "source_type TEXT NOT NULL CHECK(source_type IN ('review','validation','proposal')), source_id TEXT NOT NULL,"
        "source_revision INTEGER NOT NULL CHECK(source_revision>=1),"
        f"type TEXT NOT NULL CHECK(type IN ({types})),"
        "severity TEXT NOT NULL CHECK(severity IN ('info','warning','error')),"
        "cause_code TEXT NOT NULL CHECK(length(cause_code) BETWEEN 1 AND 64),"
        "occurred_at REAL NOT NULL, received_at REAL NOT NULL,"
        "target TEXT NOT NULL CHECK(json_valid(target)), summary TEXT NOT NULL CHECK(json_valid(summary)),"
        "UNIQUE(project_id,id), UNIQUE(project_id,source_type,source_id,source_revision,type))")
    connection.execute("CREATE TRIGGER immutable_workspace_event BEFORE UPDATE ON workspace_event BEGIN SELECT RAISE(ABORT,'IMMUTABLE_EVENT'); END")
    connection.execute(
        "CREATE TABLE notification (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, event_id TEXT NOT NULL UNIQUE,"
        "dedupe_key TEXT NOT NULL CHECK(length(dedupe_key)=64), count INTEGER NOT NULL CHECK(count>=1),"
        "created_at REAL NOT NULL, last_seen_at REAL NOT NULL, read_at REAL, version INTEGER NOT NULL CHECK(version>=1),"
        "FOREIGN KEY(project_id,event_id) REFERENCES workspace_event(project_id,id), UNIQUE(project_id,dedupe_key))")
    connection.execute("CREATE INDEX ix_notification_project ON notification(project_id,read_at,created_at,id)")
    connection.execute("CREATE INDEX ix_notification_created ON notification(created_at,id)")
