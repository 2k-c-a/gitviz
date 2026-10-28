"""Durable local events. Callers record inside the same transaction as the state change."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import uuid
from typing import Any

from .scoped_files import ScopeError

CAUSE = re.compile(r'^[A-Za-z0-9_.-]{1,64}$')
# status -> (event type, severity, default cause). AI inference and deterministic
# command results keep separate causes so the inbox never merges their meaning.
REVIEW = {'succeeded': ('review.succeeded', 'info', 'ai-no-findings'), 'failed': ('review.failed', 'error', 'FAILED'),
          'cancelled': ('review.cancelled', 'info', 'CANCELLED'), 'uncertain': ('review.uncertain', 'warning', 'OUTCOME_UNCERTAIN')}
VALIDATION = {'succeeded': ('validation.succeeded', 'info', 'command-exit-zero'),
              'failed': ('validation.failed', 'error', 'command-nonzero-exit'),
              'cancelled': ('validation.cancelled', 'info', 'CANCELLED'), 'timed_out': ('validation.timed_out', 'error', 'TIMED_OUT'),
              'output_limit': ('validation.output_limit', 'warning', 'OUTPUT_LIMIT'),
              'interrupted': ('validation.interrupted', 'warning', 'INTERRUPTED'),
              'launch_failed': ('validation.launch_failed', 'error', 'LAUNCH_FAILED'),
              'precondition_failed': ('validation.precondition_failed', 'warning', 'PRECONDITION_FAILED')}
PROPOSAL = {'applied': ('proposal.applied', 'info', 'APPLIED'), 'recovery_required': ('proposal.recovery_required', 'error', 'RECOVERY_REQUIRED'),
            'restored': ('proposal.restored', 'info', 'RESTORED')}
ANALYSIS = {'done': ('analysis.succeeded', 'info', 'remote-analysis-done'),
            'failed': ('analysis.failed', 'error', 'remote-analysis-failed')}
SOURCES = {'review': REVIEW, 'validation': VALIDATION, 'proposal': PROPOSAL, 'analysis': ANALYSIS}
FINDING_ORDER = ('unknown', 'low', 'medium', 'high', 'critical')


def dedupe_key(source_type: str, source_id: str, revision: int, kind: str) -> str:
    return hashlib.sha256(f'{source_type}\0{source_id}\0{revision}\0{kind}'.encode()).hexdigest()


def review_summary(kind: str, output: dict[str, Any] | None) -> dict[str, Any]:
    findings = list((output or {}).get('findings') or [])
    highest = None
    for finding in findings:
        level = finding.get('severity')
        if level in FINDING_ORDER and (highest is None or FINDING_ORDER.index(level) > FINDING_ORDER.index(highest)):
            highest = level
    return {'kind': 'refactor' if kind == 'refactor' else 'review', 'findings': len(findings), 'highestFindingSeverity': highest}


def record(db: sqlite3.Connection, *, project: str, source_type: str, source_id: str, revision: int, status: str,
           summary: dict[str, Any] | None = None, cause: str | None = None, occurred_at: float | None = None) -> bool:
    """Insert or deduplicate one terminal event. Returns False for statuses that are not inbox events."""
    if not db.in_transaction:
        raise sqlite3.ProgrammingError('EVENT_OUTSIDE_TRANSACTION')
    mapping = SOURCES[source_type].get(status)
    if mapping is None:
        return False
    kind, severity, default_cause = mapping
    summary = dict(summary or {})
    if kind == 'review.succeeded':
        if summary.get('kind') == 'refactor':
            default_cause = 'ai-refactor-ready'
        elif summary.get('findings'):
            severity, default_cause = 'warning', 'ai-findings'
    code = cause if cause and CAUSE.match(cause) else default_cause
    now = time.time()
    key = dedupe_key(source_type, source_id, revision, kind)
    existing = db.execute('SELECT id FROM notification WHERE project_id=? AND dedupe_key=?', (project, key)).fetchone()
    if existing is not None:
        db.execute('UPDATE notification SET count=count+1,last_seen_at=?,version=version+1 WHERE id=?', (now, existing[0]))
        return True
    event = str(uuid.uuid4())
    sequence = db.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM workspace_event').fetchone()[0]
    target = json.dumps({'kind': source_type, 'resourceId': source_id}, separators=(',', ':'))
    db.execute('INSERT INTO workspace_event VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (event, sequence, project, source_type, source_id, revision, kind, severity, code, occurred_at or now, now, target,
         json.dumps(summary, separators=(',', ':'))))
    db.execute('INSERT INTO notification VALUES(?,?,?,?,1,?,?,NULL,1)', (str(uuid.uuid4()), project, event, key, now, now))
    return True


def read(db: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    """Bounded catch-up read for the main-process event channel. The inbox list stays the source of truth."""
    after, limit = payload['afterSequence'], payload['limit']
    projects = payload['projectIds']
    last = db.execute('SELECT COALESCE(MAX(sequence),0) FROM workspace_event').fetchone()[0]
    if after > last:
        return {'events': [], 'lastSequence': last, 'reset': True, 'hasMore': False}
    sql = 'SELECT sequence,id,project_id,type,source_id,source_revision FROM workspace_event WHERE sequence>?'
    args: list[Any] = [after]
    if projects is not None:
        if not projects:
            raise ScopeError('INVALID_REQUEST')
        sql += f" AND project_id IN ({','.join('?' * len(projects))})"
        args += projects
    rows = db.execute(sql + ' ORDER BY sequence LIMIT ?', (*args, limit + 1)).fetchall()
    return {'events': [{'sequence': row[0], 'eventId': row[1], 'projectId': row[2], 'type': row[3], 'resourceId': row[4],
                        'sourceRevision': row[5]} for row in rows[:limit]],
            'lastSequence': last, 'reset': False, 'hasMore': len(rows) > limit}
