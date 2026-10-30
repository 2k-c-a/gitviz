"""Main-verified identities. No HTTP, source upload or Git mutation in this module."""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, Generator
from urllib.parse import urlsplit

from . import events
from .git_service import LocalGit
from .projects import Projects
from .scoped_files import ScopeError, bounded_text, pin_path, pinned_paths


def github_url(value: str) -> str | None:
    if value.startswith('git@github.com:'):
        value = 'https://github.com/' + value[len('git@github.com:'):]
    if value.startswith('ssh://git@github.com/'):
        value = 'https://github.com/' + value[len('ssh://git@github.com/'):]
    try:
        parsed = urlsplit(value)
        path = parsed.path.rstrip('/').removesuffix('.git')
        if (parsed.scheme != 'https' or parsed.netloc.lower() != 'github.com' or parsed.query or parsed.fragment
                or not re.fullmatch(r'/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', path)
                or any(part in ('.', '..') for part in path.split('/')[1:])):
            return None
        return 'https://github.com' + path.lower()
    except ValueError:
        return None


class RemoteGit(LocalGit):
    def references(self, grant: dict[str, str]) -> list[dict[str, str]]:
        raw = self._run(grant, ['config', '--local', '--null', '--list', '--no-includes'])
        references: list[dict[str, str]] = []
        counts: dict[str, int] = {}
        for record in raw.decode('utf-8', 'strict').split('\0'):
            key, _, value = record.partition('\n')
            match = re.fullmatch(r'remote\.([A-Za-z0-9_.-]{1,80})\.url', key)
            if match:
                counts[match[1]] = counts.get(match[1], 0) + 1
            url = github_url(value)
            if match and url:
                references.append({'name': match[1], 'canonicalUrl': url})
        if len(references) > 32:
            raise ScopeError('OUTPUT_LIMIT')
        # Git may use a different first fetch URL: a secondary public URL is never proof.
        return [reference for reference in references if counts[reference['name']] == 1]


class RemoteConnections:
    def __init__(self, projects: Projects):
        self.projects = projects
        self.db = projects.connection
        self.git: RemoteGit | None = None

    def connections(self, payload: dict[str, Any]) -> dict[str, Any]:
        rows = self.db.execute('SELECT project_id FROM remote_connection WHERE active=1 AND account_id=? AND server_origin=? ORDER BY project_id LIMIT 17',
                              (payload['accountId'], payload['serverOrigin'])).fetchall()
        return {'projectIds': [row[0] for row in rows[:16]], 'hasMore': len(rows) > 16}

    @contextmanager
    def configuration(self, project: str) -> Generator[tuple[str, list[dict[str, str]]], None, None]:
        grant = self.projects.grant(project)
        with pinned_paths(grant, []), ExitStack() as stack:
            paths = [Path(grant[key]) / name for key in ('gitDir', 'commonDir') for name in ('config', 'config.worktree')]
            def digest() -> str:
                # Frame each field: distinct config/worktree-config splits cannot share a concatenated digest.
                values = [(index, path.exists(), bounded_text(path) if path.exists() else None) for index, path in enumerate(paths)]
                return hashlib.sha256(json.dumps(values, separators=(',', ':')).encode()).hexdigest()
            for path in dict.fromkeys(paths):
                if path.exists():
                    pin_path(path, stack)
            before = digest()
            if self.git is None:
                self.git = RemoteGit(self.projects.git.executable if self.projects.git else None)
            references = self.git.references(grant)
            if digest() != before:
                raise ScopeError('PRECONDITION_FAILED')
            yield before, references
            if digest() != before:
                raise ScopeError('PRECONDITION_FAILED')

    def inspect(self, project: str) -> dict[str, Any]:
        with self.configuration(project) as (config_hash, references):
            connection = self.db.execute('SELECT id,account_id,server_origin,repository_id,github_id,canonical_url,remote_name,config_hash '
                'FROM remote_connection WHERE project_id=? AND active=1', (project,)).fetchone()
            saved = None if connection is None else dict(zip(('connectionId', 'accountId', 'serverOrigin', 'repositoryId', 'githubId', 'canonicalUrl', 'remoteName', 'configHash'), connection))
            local = self.projects.get(project)
            return {'projectId': project, 'projectVersion': local['version'], 'displayName': local['displayName'],
                    'canonicalRoot': self.projects.grant(project)['root'],
                    'configHash': config_hash, 'remotes': references, 'connection': saved}

    def connect(self, project: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
        with self.configuration(project) as (config_hash, references):
            if config_hash != payload['configHash'] or {'name': payload['remoteName'], 'canonicalUrl': payload['canonicalUrl']} not in references:
                raise ScopeError('PRECONDITION_FAILED')
            origin = urlsplit(str(payload['serverOrigin']))
            if (origin.scheme != 'https' or origin.username or origin.password or origin.query or origin.fragment or origin.path
                    or not origin.hostname or origin.geturl() != payload['serverOrigin'] or any(ord(c) < 33 for c in payload['serverOrigin'])):
                raise ScopeError('INVALID_REQUEST')
            def action() -> dict[str, Any]:
                if self.projects.get(project)['version'] != payload['expectedVersion']:
                    raise ScopeError('CONFLICT')
                connection = str(uuid.uuid4())
                self.db.execute('UPDATE remote_connection SET active=0 WHERE project_id=? AND active=1', (project,))
                self.db.execute('INSERT INTO remote_connection VALUES(?,?,?,?,?,?,?,?,?,1,?)',
                    (connection, project, payload['accountId'], payload['serverOrigin'], payload['repositoryId'], payload['githubId'],
                     payload['canonicalUrl'], payload['remoteName'], config_hash, time.time()))
                self.db.execute('UPDATE local_project SET version=version+1 WHERE id=?', (project,))
                return {'projectId': project, 'connectionId': connection}
            return self.projects.mutate(project, 'projects.connectRemote', key, payload, action)

    def observe(self, project: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
        with self.configuration(project) as (config_hash, _):
            connection = self.db.execute('SELECT config_hash,repository_id FROM remote_connection WHERE project_id=? AND id=? '
                'AND account_id=? AND server_origin=? AND active=1', (project, payload['connectionId'], payload['accountId'], payload['serverOrigin'])).fetchone()
            if connection is None:
                raise ScopeError('RESOURCE_NOT_FOUND')
            if connection[0] != config_hash:
                raise ScopeError('PRECONDITION_FAILED')
            def action() -> dict[str, Any]:
                previous = self.db.execute('SELECT id,status FROM remote_analysis WHERE connection_id=? AND job_id=?',
                    (payload['connectionId'], payload['jobId'])).fetchone()
                identifier = previous[0] if previous else str(uuid.uuid4())
                status = payload['status']
                if previous and previous[1] in ('done', 'failed'):
                    if status != previous[1]:
                        raise ScopeError('CONFLICT')
                else:
                    self.db.execute('INSERT INTO remote_analysis VALUES(?,?,?,?,?,?,?) ON CONFLICT(connection_id,job_id) '
                        'DO UPDATE SET status=excluded.status,snapshot_id=excluded.snapshot_id,updated_at=excluded.updated_at',
                        (identifier, project, payload['connectionId'], payload['jobId'], status, payload['snapshotId'], time.time()))
                    if previous and status in ('done', 'failed'):
                        events.record(self.db, project=project, source_type='analysis', source_id=identifier, revision=1, status=status,
                            summary={'repositoryId': connection[1], 'jobId': payload['jobId'], 'snapshotId': payload['snapshotId']})
                return {'projectId': project, 'analysisId': identifier}
            return self.projects.mutate(project, 'remote.observeAnalysis', key, payload, action)

    def get(self, project: str, payload: dict[str, Any]) -> dict[str, Any]:
        row = self.db.execute('SELECT a.id,a.connection_id,c.repository_id,c.github_id,c.canonical_url,a.job_id,a.snapshot_id,a.status '
            'FROM remote_analysis a JOIN remote_connection c ON c.id=a.connection_id AND c.project_id=a.project_id '
            'WHERE a.project_id=? AND a.id=? AND c.account_id=? AND c.server_origin=? AND c.active=1',
            (project, payload['analysisId'], payload['accountId'], payload['serverOrigin'])).fetchone()
        if row is None:
            raise ScopeError('RESOURCE_NOT_FOUND')
        inspected = self.inspect(project)
        if inspected['connection']['configHash'] != inspected['configHash']:
            raise ScopeError('PRECONDITION_FAILED')
        return {'projectId': project, **dict(zip(('analysisId', 'connectionId', 'repositoryId', 'githubId', 'canonicalUrl', 'jobId', 'snapshotId', 'status'), row))}
