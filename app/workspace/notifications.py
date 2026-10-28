"""Cross-project local inbox. Reading is metadata only; clicking re-validates the target elsewhere."""
from __future__ import annotations

import base64
import hashlib
import json
import time
from typing import Any, cast

from .projects import Projects
from .scoped_files import ScopeError

TARGET_TABLES = {'review': 'review_run', 'validation': 'validation_run', 'proposal': 'proposal'}
UUID_LENGTH = 36


def _filter_hash(filters: dict[str, Any]) -> str:
    canonical: dict[str, Any] = {}
    for name, value in filters.items():
        canonical[name] = sorted(cast(list[str], value)) if isinstance(value, list) else value
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:32]


def _cursor(created: float, notification: str, filters: str) -> str:
    raw = json.dumps({'c': created, 'i': notification, 'f': filters}, separators=(',', ':')).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def _decode(cursor: str, filters: str) -> tuple[float, str]:
    try:
        decoded: Any = json.loads(base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4)).decode())
        if not isinstance(decoded, dict):
            raise ValueError
        value = cast(dict[str, Any], decoded)
        if (set(value) != {'c', 'i', 'f'} or not isinstance(value['c'], float)
                or not isinstance(value['i'], str) or len(value['i']) != UUID_LENGTH):
            raise ValueError
    except (ValueError, UnicodeError):
        raise ScopeError('INVALID_REQUEST') from None
    if value['f'] != filters:
        raise ScopeError('INVALID_REQUEST')  # A cursor is bound to the filter set that produced it.
    return value['c'], value['i']


class Notifications:
    def __init__(self, projects: Projects):
        self.projects = projects
        self.db = projects.connection

    def _item(self, row: tuple[Any, ...]) -> dict[str, Any]:
        (notification, project, name, event, source_type, source_id, revision, kind, severity, cause,
         occurred, created, last_seen, count, read_at, version, summary) = row
        table = TARGET_TABLES[source_type]
        exists = self.db.execute(f'SELECT 1 FROM {table} WHERE project_id=? AND id=?', (project, source_id)).fetchone()
        return {'notificationId': notification, 'projectId': project, 'projectName': name, 'eventId': event,
                'sourceType': source_type, 'sourceRevision': revision, 'type': kind, 'severity': severity,
                'causeCode': cause, 'occurredAt': occurred, 'createdAt': created, 'lastSeenAt': last_seen, 'count': count,
                'readAt': read_at, 'version': version, 'summary': json.loads(summary),
                'target': {'kind': source_type, 'projectId': project, 'resourceId': source_id},
                'targetState': 'available' if exists else 'unavailable'}

    SELECT = ('SELECT n.id,n.project_id,p.display_name,e.id,e.source_type,e.source_id,e.source_revision,e.type,e.severity,'
              'e.cause_code,e.occurred_at,n.created_at,n.last_seen_at,n.count,n.read_at,n.version,e.summary '
              'FROM notification n JOIN workspace_event e ON e.project_id=n.project_id AND e.id=n.event_id '
              'JOIN local_project p ON p.id=n.project_id')

    def list(self, payload: dict[str, Any]) -> dict[str, Any]:
        filters, limit = payload['filters'], payload['limit']
        digest = _filter_hash(filters)
        clauses: list[str] = []
        args: list[Any] = []
        for column, name in (('n.project_id', 'projectIds'), ('e.type', 'types'), ('e.severity', 'severities')):
            values = filters[name]
            if values is not None:
                clauses.append(f"{column} IN ({','.join('?' * len(values))})")
                args += values
        if filters['read'] == 'unread':
            clauses.append('n.read_at IS NULL')
        elif filters['read'] == 'read':
            clauses.append('n.read_at IS NOT NULL')
        if payload['cursor'] is not None:
            created, notification = _decode(payload['cursor'], digest)
            clauses.append('(n.created_at<? OR (n.created_at=? AND n.id<?))')
            args += [created, created, notification]
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ''
        rows = self.db.execute(f'{self.SELECT}{where} ORDER BY n.created_at DESC,n.id DESC LIMIT ?', (*args, limit + 1)).fetchall()
        items = [self._item(row) for row in rows[:limit]]
        unread = self.db.execute('SELECT COUNT(*) FROM notification WHERE read_at IS NULL').fetchone()[0]
        return {'items': items, 'unreadCount': unread,
                'nextCursor': _cursor(rows[limit - 1][11], rows[limit - 1][0], digest) if len(rows) > limit else None}

    def get(self, project: str, notification: str) -> dict[str, Any]:
        row = self.db.execute(f'{self.SELECT} WHERE n.project_id=? AND n.id=?', (project, notification)).fetchone()
        if row is None:
            raise ScopeError('RESOURCE_NOT_FOUND')
        return self._item(row)

    def mark_read(self, project: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
        def action() -> dict[str, Any]:
            row = self.db.execute('SELECT version FROM notification WHERE project_id=? AND id=?', (project, payload['notificationId'])).fetchone()
            if row is None:
                raise ScopeError('RESOURCE_NOT_FOUND')  # Includes another project's notification ID.
            if row[0] != payload['expectedVersion']:
                raise ScopeError('CONFLICT')
            self.db.execute('UPDATE notification SET read_at=?,version=version+1 WHERE id=?',
                (time.time() if payload['read'] else None, payload['notificationId']))
            return {'notificationId': payload['notificationId']}
        self.projects.mutate(project, 'notifications.markRead', key, payload, action)
        return self.get(project, payload['notificationId'])
