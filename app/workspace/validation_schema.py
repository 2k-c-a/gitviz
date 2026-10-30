"""P07 additive local schema. No server migration."""
from __future__ import annotations

import sqlite3

TABLES = {'validation_run', 'validation_approval', 'comparison'}


def create(db: sqlite3.Connection) -> None:
    db.execute('ALTER TABLE project_setting RENAME TO project_setting_v5')
    db.execute("CREATE TABLE project_setting (project_id TEXT NOT NULL REFERENCES local_project(id) ON DELETE CASCADE,key TEXT NOT NULL CHECK(key IN ('recentLimit','editorId')),value TEXT NOT NULL CHECK(json_valid(value)),version INTEGER NOT NULL CHECK(version>=1),PRIMARY KEY(project_id,key))")
    db.execute('INSERT INTO project_setting SELECT * FROM project_setting_v5')
    db.execute("INSERT INTO project_setting SELECT id,'editorId','\"vscode\"',1 FROM local_project")
    db.execute('DROP TABLE project_setting_v5')
    db.execute('CREATE UNIQUE INDEX uq_command_plan_project_id ON command_plan(project_id,id)')
    db.execute("CREATE TABLE validation_approval (id TEXT PRIMARY KEY,project_id TEXT NOT NULL,plan_id TEXT NOT NULL,confirmation_hash TEXT NOT NULL,capability_hash TEXT NOT NULL,expires_at REAL NOT NULL,consumed INTEGER NOT NULL CHECK(consumed IN (0,1)),FOREIGN KEY(project_id,plan_id) REFERENCES command_plan(project_id,id) ON DELETE CASCADE)")
    db.execute("CREATE TABLE validation_run (id TEXT PRIMARY KEY,project_id TEXT NOT NULL,plan_id TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('running','succeeded','failed','cancelled','timed_out','output_limit','interrupted','launch_failed','precondition_failed')),payload BLOB NOT NULL,FOREIGN KEY(project_id,plan_id) REFERENCES command_plan(project_id,id) ON DELETE CASCADE,UNIQUE(project_id,id))")
    db.execute("CREATE TABLE comparison (id TEXT PRIMARY KEY,project_id TEXT NOT NULL,before_snapshot_id TEXT NOT NULL,after_snapshot_id TEXT NOT NULL,proposal_id TEXT,before_run_id TEXT,after_run_id TEXT,payload BLOB NOT NULL,FOREIGN KEY(project_id,before_snapshot_id) REFERENCES change_snapshot(project_id,id),FOREIGN KEY(project_id,after_snapshot_id) REFERENCES change_snapshot(project_id,id),FOREIGN KEY(project_id,proposal_id) REFERENCES proposal(project_id,id),FOREIGN KEY(project_id,before_run_id) REFERENCES validation_run(project_id,id),FOREIGN KEY(project_id,after_run_id) REFERENCES validation_run(project_id,id),UNIQUE(project_id,id))")
