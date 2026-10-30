"""Immutable, encrypted local inputs; no remote revision IDs or source writes."""
from __future__ import annotations

import base64
import difflib
import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .adapters import ai_core
from .crypto import PayloadCipher
from .git_service import LocalGit
from .projects import Projects
from .scoped_files import ScopeError, pinned, read_scoped, relative_path


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def image_hash(value: bytes | None) -> str | None:
    return hashlib.sha256(value).hexdigest() if value is not None else None


def index_hash(grant: dict[str, str]) -> str:
    directory = Path(grant['gitDir'])
    files = [directory / 'index', *sorted(directory.glob('sharedindex.*'))]
    result: list[tuple[str, str | None]] = []
    for file in files:
        try:
            from .scoped_files import identity
            identity(file)
            digest = hashlib.sha256()
            size = 0
            with file.open('rb') as stream:
                while chunk := stream.read(65536):
                    size += len(chunk)
                    if size > 16 * 1024 * 1024:
                        raise ScopeError('OUTPUT_LIMIT')
                    digest.update(chunk)
            result.append((file.name, digest.hexdigest()))
        except FileNotFoundError:
            result.append((file.name, None))
    return fingerprint(result)


class Snapshots:
    def __init__(self, projects: Projects, cipher: PayloadCipher):
        self.projects = projects
        self.db = projects.connection
        self.cipher = cipher

    def get(self, project_id: str, snapshot_id: str) -> dict[str, Any]:
        row = self.db.execute('SELECT manifest,expires_at FROM change_snapshot WHERE project_id=? AND id=?', (project_id, snapshot_id)).fetchone()
        if row is None:
            raise ScopeError('RESOURCE_NOT_FOUND')
        if row[1] is not None and row[1] <= time.time():
            raise ScopeError('INPUT_EXPIRED')
        return json.loads(row[0])

    @staticmethod
    def aad(project_id: str, snapshot_id: str, file_id: str) -> bytes:
        return f'GitVizWorkspace/project/{project_id}/snapshot/{snapshot_id}/file/{file_id}/schema-3'.encode()

    def capture(self, project_id: str, payload: dict[str, Any], key: str,
                checkpoint: Callable[[], None] | None = None) -> dict[str, Any]:
        def action() -> dict[str, Any]:
            deadline = time.monotonic() + 45
            grant = self.projects.grant(project_id)
            git = self.projects.git or LocalGit()
            with pinned(grant):
                before_state = git.status(grant, 1)
                inventory = git.inventory(grant, before_state)
                before_index = index_hash(grant)
                by_key = {(item['facet'], item['path']): item for item in inventory}
                selections = sorted(payload['items'], key=lambda item: (item['facet'], item['path']))
                keys = [(item['facet'], relative_path(item['path'])) for item in selections]
                if len(set(keys)) != len(keys):
                    raise ScopeError('INVALID_REQUEST')
                snapshot_id = str(uuid.uuid4())
                files: list[dict[str, Any]] = []
                stored: list[tuple[Any, ...]] = []
                source_checks: list[tuple[str, str | None]] = []
                capture_bytes = 0
                for facet, path in keys:
                    if time.monotonic() > deadline:
                        raise ScopeError('TIMED_OUT')
                    if facet == 'untracked' and not payload['includeUntracked']:
                        raise ScopeError('INVALID_REQUEST')
                    item = by_key.get((facet, path))
                    if item is None and facet != 'selected':
                        raise ScopeError('CAPTURE_CHANGED')
                    item = item or {'oldMode': '100644', 'newMode': '100644', 'oldOid': '', 'newOid': ''}
                    old_path = item.get('oldPath')
                    if old_path:
                        relative_path(old_path)
                    file_id = str(uuid.uuid4())
                    reason = ai_core.exclusion(path, None, None, old_path, (item['oldMode'], item['newMode']))
                    before = after = None
                    if reason is None:
                        try:
                            before = git.blob(grant, item['oldOid'], before_state['objectFormat']) if facet in ('staged', 'unstaged') else None
                            after = git.blob(grant, item['newOid'], before_state['objectFormat']) if facet == 'staged' else read_scoped(grant, path)
                            if facet == 'selected':
                                if after is None:
                                    raise ScopeError('RESOURCE_NOT_FOUND')
                                before = after
                            if facet == 'untracked' and after is None:
                                raise ScopeError('CAPTURE_CHANGED')
                            if facet != 'staged':
                                source_checks.append((path, image_hash(after)))
                            reason = ai_core.exclusion(path, before, after, old_path, (item['oldMode'], item['newMode']))
                        except ScopeError as error:
                            if error.code != 'INPUT_TOO_LARGE':
                                raise
                            reason = 'too_large'
                    record: dict[str, Any] = {'fileId': file_id, 'facet': facet, 'path': path, 'oldPath': old_path,
                        'state': 'excluded' if reason else 'read', 'exclusionReason': reason,
                        'beforeHash': image_hash(before), 'afterHash': image_hash(after)}
                    encrypted = None
                    line_map: list[dict[str, Any]] = []
                    if reason is None:
                        capture_bytes += len(before or b'') + len(after or b'')
                        if capture_bytes > 2000000:
                            raise ScopeError('INPUT_TOO_LARGE')
                        left = ai_core.difftext.split_lines((before or b'').decode('utf-8'))
                        right = ai_core.difftext.split_lines((after or b'').decode('utf-8'))
                        # Compare LF-separated Git lines, preserving original bytes separately.
                        diff = '\n'.join(difflib.unified_diff(left, right, fromfile='before', tofile='after', n=25, lineterm='')) + '\n'
                        lines = [line.model_dump(mode='json') for line in ai_core.difftext.diff_lines(diff)]
                        if facet == 'selected':
                            lines = [{'kind': 'context', 'left_line': i, 'right_line': i, 'text': text.removesuffix('\r')} for i, text in enumerate(right, 1)]
                        line_map = [{k: v for k, v in line.items() if k != 'text'} for line in lines]
                        encrypted = self.cipher.encrypt(json.dumps({'before': base64.b64encode(before).decode() if before is not None else None,
                            'after': base64.b64encode(after).decode() if after is not None else None, 'diff': diff, 'lines': lines}, ensure_ascii=False).encode(), self.aad(project_id, snapshot_id, file_id))
                    files.append(record)
                    stored.append((file_id, project_id, snapshot_id, path, old_path, facet, record['beforeHash'], record['afterHash'],
                                   encrypted, self.cipher.key_version if encrypted else None, json.dumps(line_map), record['state'], reason))
                if checkpoint:
                    checkpoint()
                for path, expected in source_checks:
                    if image_hash(read_scoped(grant, path)) != expected:
                        raise ScopeError('CAPTURE_CHANGED')
                after_state = git.status(grant, 1)
                if before_state != after_state or before_index != index_hash(grant) or inventory != git.inventory(grant, after_state):
                    raise ScopeError('CAPTURE_CHANGED')
                if time.monotonic() > deadline:
                    raise ScopeError('TIMED_OUT')
                stable_files = [{k: v for k, v in item.items() if k != 'fileId'} for item in files]
                stable = {'head': before_state['head'], 'objectFormat': before_state['objectFormat'], 'indexHash': before_index,
                          'inventoryHash': fingerprint(inventory), 'files': stable_files, 'policyVersion': ai_core.POLICY_VERSION}
                result = {'projectId': project_id, 'snapshotId': snapshot_id, **{k: stable[k] for k in ('head', 'objectFormat', 'indexHash', 'inventoryHash', 'policyVersion')},
                          'inputHash': fingerprint(stable), 'capturedAt': datetime.now(timezone.utc).isoformat(), 'expiresAt': None,
                          'coverage': {'changed': len(inventory), 'selected': len(files), 'read': sum(f['state'] == 'read' for f in files),
                                       'excluded': sum(f['state'] != 'read' for f in files)}, 'files': files,
                          'limitations': [*before_state['limitations'], 'content-secret-detection-is-heuristic', 'retention-policy-pending']}
                self.db.execute('INSERT INTO change_snapshot VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (snapshot_id, project_id, stable['head'], stable['objectFormat'], before_index, result['inputHash'],
                     json.dumps(keys), result['capturedAt'], json.dumps(result['coverage']), None, json.dumps(result)))
                self.db.executemany('INSERT INTO change_file VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', stored)
                return result
        return self.projects.mutate(project_id, 'changes.capture', key, payload, action)

    def preview(self, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not payload['startLine'] <= payload['endLine'] < payload['startLine'] + 200:
            raise ScopeError('INVALID_REQUEST')
        self.get(project_id, payload['snapshotId'])
        row = self.db.execute('SELECT payload,content_state FROM change_file WHERE project_id=? AND snapshot_id=? AND id=?',
                             (project_id, payload['snapshotId'], payload['fileId'])).fetchone()
        if row is None:
            raise ScopeError('RESOURCE_NOT_FOUND')
        if row[1] != 'read' or row[0] is None:
            raise ScopeError('ACCESS_DENIED')
        body = json.loads(self.cipher.decrypt(row[0], self.aad(project_id, payload['snapshotId'], payload['fileId'])))
        key = 'left_line' if payload['side'] == 'LEFT' else 'right_line'
        lines = [{'line': item[key], 'text': item['text'], 'kind': item['kind']} for item in body['lines']
                 if item.get(key) is not None and payload['startLine'] <= item[key] <= payload['endLine']]
        return {'projectId': project_id, 'snapshotId': payload['snapshotId'], 'fileId': payload['fileId'], 'side': payload['side'], 'lines': lines}
