"""Schema8 preserves inbox history and adds account-bound remote metadata."""
import sqlite3

TABLES = {'remote_connection', 'remote_analysis'}


def create(db: sqlite3.Connection) -> None:
    original = db.execute("SELECT sql FROM sqlite_master WHERE name='workspace_event'").fetchone()[0]
    if "'review','validation','proposal'" not in original or "'proposal.restored'" not in original:
        raise ValueError('UNSUPPORTED_SCHEMA')
    expanded = original.replace('CREATE TABLE workspace_event', 'CREATE TABLE workspace_event_next', 1)
    expanded = expanded.replace("'review','validation','proposal'", "'review','validation','proposal','analysis'")
    expanded = expanded.replace("'proposal.restored'", "'proposal.restored','analysis.succeeded','analysis.failed'")
    db.execute(expanded)
    db.execute('INSERT INTO workspace_event_next SELECT * FROM workspace_event')
    db.execute('DROP TABLE workspace_event')
    db.execute('ALTER TABLE workspace_event_next RENAME TO workspace_event')
    db.execute("CREATE TRIGGER immutable_workspace_event BEFORE UPDATE ON workspace_event BEGIN SELECT RAISE(ABORT,'IMMUTABLE_EVENT'); END")
    db.execute('CREATE TABLE remote_connection(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES local_project(id),'
               'account_id TEXT NOT NULL,server_origin TEXT NOT NULL,repository_id INTEGER NOT NULL CHECK(repository_id>0),'
               'github_id INTEGER NOT NULL CHECK(github_id>0),canonical_url TEXT NOT NULL,remote_name TEXT NOT NULL,'
               'config_hash TEXT NOT NULL CHECK(length(config_hash)=64),active INTEGER NOT NULL CHECK(active IN(0,1)),'
               'created_at REAL NOT NULL,UNIQUE(project_id,id))')
    db.execute('CREATE UNIQUE INDEX ix_remote_connection_active ON remote_connection(project_id) WHERE active=1')
    db.execute('CREATE TABLE remote_analysis(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,connection_id TEXT NOT NULL,'
               'job_id INTEGER NOT NULL CHECK(job_id>0),status TEXT NOT NULL CHECK(status IN(\'queued\',\'running\',\'done\',\'failed\')),'
               'snapshot_id INTEGER CHECK(snapshot_id>0),updated_at REAL NOT NULL,'
               'FOREIGN KEY(project_id,connection_id) REFERENCES remote_connection(project_id,id),'
               'UNIQUE(connection_id,job_id),UNIQUE(project_id,id))')
