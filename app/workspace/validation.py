"""Native-consent validation jobs, single SQL writer and bounded worker output."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import queue
import secrets
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .detection import Detection, runner_environment, runtime_paths
from .diagnostics import extract
from .git_service import LocalGit
from .runner import execute, runtime_pins
from .scoped_files import ScopeError, identity, pinned, read_scoped
from .snapshots import fingerprint, image_hash, index_hash


def dependency_fingerprint(root: Path, executable: Path, cwd: str) -> str | None:
    """Installed metadata profile, not attestation of all dependency code bytes."""
    directories = [root / 'node_modules', root / '.venv', root / 'venv',
        executable.parent / 'Lib/site-packages', executable.parent.parent / 'Lib/site-packages',
        executable.parent / 'node_modules']
    if cwd != '.':
        directories.append(root.joinpath(*cwd.split('/')) / 'node_modules')
    configuration = executable.parent.parent / 'pyvenv.cfg'
    if configuration.is_file():
        # A venv's base runtime must have explicit environment configuration before
        # equivalence can be claimed. It remains runnable with native trust.
        return None
    metadata_names = {'package.json', 'METADATA', 'RECORD', 'PKG-INFO', 'installed-files.txt', 'pyvenv.cfg', '.package-lock.json'}
    result: list[tuple[str, str]] = []
    size = visited = 0
    seen: set[str] = set()
    deadline = time.monotonic() + 3
    try:
        for directory in directories:
            if not directory.exists() or str(directory).casefold() in seen:
                continue
            seen.add(str(directory).casefold())
            identity(directory)
            for current, children, files in os.walk(directory, followlinks=False):
                children[:] = sorted(name for name in children if name not in ('__pycache__', '.cache', '.git'))
                for child in children:
                    identity(Path(current) / child)
                for name in sorted(files):
                    visited += 1
                    if visited > 100000 or time.monotonic() > deadline:
                        return None
                    if name not in metadata_names and not name.endswith('.pth'):
                        continue
                    path = Path(current) / name
                    identity(path)
                    size += path.stat().st_size
                    if len(result) >= 10000 or size > 100_000_000:
                        return None
                    result.append((str(path), hashlib.sha256(path.read_bytes()).hexdigest()))
    except (OSError, ScopeError):
        return None
    return fingerprint(result)


def unselected_fingerprint(root: Path, selected: set[str]) -> str | None:
    result: list[tuple[str, str]] = []
    total = 0
    deadline = time.monotonic() + 10
    try:
        for current, children, files in os.walk(root, followlinks=False):
            children[:] = sorted(name for name in children if name not in ('.git', 'node_modules', '.venv', 'venv'))
            for child in children:
                identity(Path(current) / child)
            for name in sorted(files):
                path = Path(current) / name
                relative = path.relative_to(root).as_posix()
                if relative in selected:
                    continue
                identity(path)
                total += path.stat().st_size
                if len(result) >= 20000 or total > 100_000_000 or time.monotonic() > deadline:
                    return None
                result.append((relative, hashlib.sha256(path.read_bytes()).hexdigest()))
    except (OSError, ScopeError):
        return None
    return fingerprint(result)


class Validation:
    def __init__(self, detection: Detection):
        self.detection = detection
        self.snapshots = detection.snapshots
        self.projects = detection.projects
        self.db = self.projects.connection
        self.active: tuple[str, threading.Event] | None = None
        self.completed: queue.Queue[tuple[str, str, dict[str, Any]]] = queue.Queue(maxsize=1)
        self.pending: tuple[str, str, dict[str, Any]] | None = None
        self.db.execute('UPDATE validation_approval SET consumed=1 WHERE consumed=0')
        self.db.execute("UPDATE validation_run SET status='interrupted' WHERE status='running'")

    def aad(self, project: str, resource: str) -> bytes:
        return f'GitVizWorkspace/project/{project}/validation/{resource}/schema-6'.encode()

    def encrypt(self, project: str, resource: str, value: Any) -> bytes:
        return self.snapshots.cipher.encrypt(json.dumps(value, ensure_ascii=False).encode(), self.aad(project, resource))

    def decrypt(self, project: str, resource: str, value: bytes) -> Any:
        return json.loads(self.snapshots.cipher.decrypt(value, self.aad(project, resource)))

    def plan(self, project: str, plan_id: str) -> dict[str, Any]:
        self.projects.grant(project)
        row = self.db.execute('SELECT payload FROM command_plan WHERE project_id=? AND id=?', (project, plan_id)).fetchone()
        if row is None:
            raise ScopeError('RESOURCE_NOT_FOUND')
        return json.loads(self.snapshots.cipher.decrypt(row[0], f'GitVizWorkspace/project/{project}/plan/{plan_id}/schema-3'.encode()))

    def locate(self, project: str, payload: dict[str, Any]) -> dict[str, Any]:
        snapshot = self.snapshots.get(project, payload['snapshotId'])
        matches = [file for file in snapshot['files'] if file['fileId'] == payload['fileId'] and file['state'] == 'read']
        if len(matches) != 1:
            raise ScopeError('RESOURCE_NOT_FOUND')
        file: dict[str, Any] = matches[0]
        grant = self.projects.grant(project)
        with pinned(grant):
            raw = read_scoped(grant, file['path'])
            if raw is None or image_hash(raw) != file['afterHash']:
                raise ScopeError('PRECONDITION_FAILED')
            lines = raw.decode('utf-8-sig').splitlines() or ['']
            if payload['line'] > len(lines):
                raise ScopeError('INVALID_REQUEST')
            encoded = lines[int(payload['line']) - 1].encode('utf-16-le')
            offset = (int(payload['column']) - 1) * 2
            if offset > len(encoded):
                raise ScopeError('INVALID_REQUEST')
            try:
                encoded[:offset].decode('utf-16-le')
            except UnicodeError:
                raise ScopeError('INVALID_REQUEST') from None
            return {'projectId': project, 'snapshotId': snapshot['snapshotId'], 'fileId': file['fileId'],
                'root': grant['root'], 'absolutePath': str(Path(grant['root']).joinpath(*file['path'].split('/'))),
                'sourceHash': file['afterHash'], 'line': payload['line'], 'column': payload['column']}

    def conditions(self, project: str, envelope: dict[str, Any], *, grant: dict[str, str] | None = None,
                   snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        plan = envelope['plan']
        grant = self.projects.grant(project) if grant is None else grant
        snapshot = self.snapshots.get(project, plan['snapshotId']) if snapshot is None else snapshot
        with pinned(grant):
            if self.detection.configs(grant, plan['cwdRelative'])[1] != envelope['configHashes']:
                raise ScopeError('PRECONDITION_FAILED')
            if index_hash(grant) != snapshot['indexHash']:
                raise ScopeError('PRECONDITION_FAILED')
            if (self.projects.git or LocalGit()).status(grant, 1)['head'] != snapshot['head']:
                raise ScopeError('PRECONDITION_FAILED')
            for file in snapshot['files']:
                if file['state'] != 'read':
                    raise ScopeError('PATCH_SCOPE_UNSUPPORTED')
                if image_hash(read_scoped(grant, file['path'])) != file['afterHash']:
                    raise ScopeError('PRECONDITION_FAILED')
            executable = Path(plan['executable'])
            if identity(executable) != plan['executableIdentity']:
                raise ScopeError('PRECONDITION_FAILED')
            if self.detection.directory is None:
                raise ScopeError('STORAGE_UNAVAILABLE')
            environment = runner_environment(str(executable), self.detection.directory)
            if plan.get('environment') != environment or plan['envAllowlist'] != list(environment):
                raise ScopeError('COMMAND_NOT_ALLOWED')
            runtime: dict[str, str] = {}
            # Include a fixed package manager CLI, never arbitrary script paths.
            for path in runtime_paths(str(executable), plan['argv'], environment):
                identity(path)
                if path.resolve().is_relative_to(Path(grant['root'])) or path.stat().st_size > 100_000_000:
                    raise ScopeError('COMMAND_NOT_ALLOWED')
                if path.name.startswith('runner-empty-') and (path.stat().st_size != 0 or path.stat().st_nlink != 1):
                    raise ScopeError('PRECONDITION_FAILED')
                runtime[fingerprint(str(path))] = hashlib.sha256(path.read_bytes()).hexdigest()
            return {'rootIdentity': grant['rootIdentity'], 'inputHash': snapshot['inputHash'],
                'environmentProfile': 'runtime-config-lock-installed-metadata-v1', 'runtime': runtime, 'environmentHash': fingerprint(environment), 'platform': platform.platform(),
                'dependencyFingerprint': dependency_fingerprint(Path(grant['root']), executable, plan['cwdRelative']),
                'unselectedFingerprint': unselected_fingerprint(Path(grant['root']), {file['path'] for file in snapshot['files']}),
                'configHash': plan['configHash'], 'planHash': plan['planHash']}

    def prepare(self, project: str, payload: dict[str, Any], *, drain: bool = True) -> dict[str, Any]:
        if drain:
            self.drain()
        envelope = self.plan(project, payload['planId'])
        conditions = self.conditions(project, envelope)
        return {'projectId': project, 'plan': envelope['plan'], 'confirmationHash': fingerprint(conditions),
            'root': self.projects.grant(project)['root'], 'projectName': self.projects.get(project)['displayName'],
            'risk': 'same-user-code-and-network-access-not-sandboxed', 'trustRequired': True}

    def approve(self, project: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
        self.drain()
        def action() -> dict[str, Any]:
            confirmation = self.prepare(project, payload, drain=False)
            if confirmation['confirmationHash'] != payload['confirmationHash'] or payload['trusted'] is not True:
                raise ScopeError('PRECONDITION_FAILED')
            approval = str(uuid.uuid4())
            self.db.execute('INSERT INTO validation_approval VALUES(?,?,?,?,?,?,0)', (approval, project, payload['planId'],
                payload['confirmationHash'], fingerprint(payload['capability']), time.time() + 300))
            return {'projectId': project, 'validationApprovalId': approval}
        return self.projects.mutate(project, 'validation.approve', key, payload, action)

    def start(self, project: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
        self.drain()
        launch: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        def action() -> dict[str, Any]:
            if self.active is not None:
                raise ScopeError('BUSY')
            envelope = self.plan(project, payload['planId'])
            conditions = self.conditions(project, envelope)
            approval = self.db.execute('SELECT confirmation_hash,capability_hash,expires_at,consumed FROM validation_approval WHERE id=? AND project_id=? AND plan_id=?',
                (payload['approvalId'], project, payload['planId'])).fetchone()
            if approval is None or approval[3] or approval[2] <= time.time() or not secrets.compare_digest(approval[1], fingerprint(payload['capability'])):
                raise ScopeError('APPROVAL_REQUIRED')
            if not secrets.compare_digest(approval[0], fingerprint(conditions)):
                raise ScopeError('PRECONDITION_FAILED')
            self.db.execute('UPDATE validation_approval SET consumed=1 WHERE id=?', (payload['approvalId'],))
            run = str(uuid.uuid4())
            record = {'runId': run, 'projectId': project, 'planId': payload['planId'], 'snapshotId': envelope['plan']['snapshotId'],
                'conditions': conditions, 'startedAt': time.time(), 'finishedAt': None, 'exitCode': None, 'output': [], 'truncated': False,
                'diagnostics': [], 'limitations': ['selected-input-only', 'runtime-config-lock-installed-metadata-profile-only', 'dependency-code-bytes-not-attested', 'test-identities-not-parsed', 'diagnostic-links-limited-to-first-5'], 'status': 'running'}
            self.db.execute('INSERT INTO validation_run VALUES(?,?,?,?,?)', (run, project, payload['planId'], 'running', self.encrypt(project, run, record)))
            launch.append((run, envelope, conditions))
            return {'runId': run}
        reference = self.projects.mutate(project, 'validation.start', key, payload, action)
        if launch:
            run, envelope, conditions = launch[0]
            cancelled = threading.Event()
            self.active = run, cancelled
            grant = self.projects.grant(project)
            plan = envelope['plan']
            snapshot = self.snapshots.get(project, plan['snapshotId'])
            cwd = str(Path(grant['root']) if plan['cwdRelative'] == '.' else Path(grant['root']).joinpath(*plan['cwdRelative'].split('/')))
            environment = dict(plan['environment'])
            def before_resume() -> None:
                if self.conditions(project, envelope, grant=grant, snapshot=snapshot) != conditions:
                    raise ScopeError('PRECONDITION_FAILED')
            def worker() -> None:
                try:
                    paths = runtime_paths(plan['executable'], plan['argv'], environment)
                    with runtime_pins(paths, conditions['runtime']):
                        result: dict[str, Any] = execute(plan, cwd, environment, cancelled, before_resume)
                except ScopeError:
                    result = {'status': 'precondition_failed', 'exitCode': None, 'output': [], 'truncated': False, 'finishedAt': time.time()}
                except Exception:  # noqa: BLE001 -- worker failures must yield a terminal safe classification
                    result = {'status': 'launch_failed', 'exitCode': None, 'output': [], 'truncated': False, 'finishedAt': time.time()}
                self.completed.put((project, run, result))
            threading.Thread(target=worker, daemon=True).start()
        return self.get(project, {'runId': reference['runId'], 'offset': 0, 'limit': 10})

    def drain(self) -> None:
        if self.pending is None:
            try:
                self.pending = self.completed.get_nowait()
            except queue.Empty:
                return
        project, run, result = self.pending
        row = self.db.execute('SELECT payload FROM validation_run WHERE id=? AND project_id=?', (run, project)).fetchone()
        record = self.decrypt(project, run, row[0])
        record.update(result)
        try:
            snapshot = self.snapshots.get(project, record['snapshotId'])
            record['diagnostics'] = extract(record['output'], snapshot, self.projects.grant(project)['root'])
        except (ScopeError, OSError):
            record['diagnostics'] = []
            record['limitations'] = [*record['limitations'], 'diagnostic-input-unavailable']
        try:
            record['inputChanged'] = self.conditions(project, self.plan(project, record['planId'])) != record['conditions']
        except (ScopeError, OSError):
            record['inputChanged'] = None
            record['limitations'] = [*record['limitations'], 'input-condition-unavailable']
        self.db.execute('UPDATE validation_run SET status=?,payload=? WHERE id=? AND project_id=?',
            (record['status'], self.encrypt(project, run, record), run, project))
        self.active = None
        self.pending = None

    def get(self, project: str, payload: dict[str, Any], *, drain: bool = True) -> dict[str, Any]:
        if drain:
            self.drain()
        self.projects.grant(project)
        row = self.db.execute('SELECT status,payload FROM validation_run WHERE id=? AND project_id=?', (payload['runId'], project)).fetchone()
        if row is None:
            raise ScopeError('RESOURCE_NOT_FOUND')
        result = self.decrypt(project, payload['runId'], row[1])
        result['status'] = row[0]
        if row[0] == 'interrupted':
            result['exitCode'] = None
            result['limitations'] = [*result['limitations'], 'interrupted-no-automatic-rerun', 'in-flight-output-may-be-unavailable']
        output = result['output']
        offset, limit = payload['offset'], payload['limit']
        result['output'] = output[offset:offset + limit]
        result['nextOffset'] = offset + limit if offset + limit < len(output) else None
        return result

    def cancel(self, project: str, payload: dict[str, Any]) -> dict[str, Any]:
        result = self.get(project, {**payload, 'offset': 0, 'limit': 10})
        if self.active is not None and self.active[0] == payload['runId']:
            self.active[1].set()
        return result

    def close(self) -> None:
        if self.active is not None:
            self.active[1].set()
