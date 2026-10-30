"""Bounded declarative manifest inspection. Nothing is imported or executed."""
from __future__ import annotations

import json
import os
import re
import sys
import time
import tomllib
import uuid
from pathlib import Path
from typing import Any, Callable, cast

from .adapters import ai_core
from .scoped_files import ScopeError, identity, pinned, pinned_paths, read_scoped, relative_path
from .snapshots import Snapshots, fingerprint, image_hash
from .contracts import decode, ProtocolError
from .windows import secure_path

CONFIGS = ('package.json', 'package-lock.json', 'npm-shrinkwrap.json', 'yarn.lock', 'pnpm-lock.yaml',
           'pyproject.toml', 'requirements.txt', 'setup.cfg', 'setup.py', 'pytest.ini', 'tox.ini',
           'ruff.toml', '.ruff.toml', 'poetry.lock', 'uv.lock', '.npmrc', '.yarnrc.yml')
LOCKS = {'package-lock.json': 'npm', 'npm-shrinkwrap.json': 'npm', 'yarn.lock': 'yarn', 'pnpm-lock.yaml': 'pnpm'}


def check_script_modes(scripts: dict[str, str]) -> None:
    # Conservative known-mode rejection, not a sandbox for trusted project code.
    forbidden = re.compile(r'(?i)(?:--(?:fix(?:-only)?|write)(?:\b|=)|\b(?:npm|pnpm|yarn)(?:\.cmd|\.exe)?\s+(?:i|install|ci|add|update|upgrade)\b|\bpip(?:3)?(?:\.exe)?\s+install\b|\bruff\s+format\b)')
    if any(forbidden.search(script) for script in scripts.values()):
        raise ScopeError('COMMAND_NOT_ALLOWED')


def runner_environment(executable: str, directory: Path) -> dict[str, str]:
    environment = {name: os.environ[name] for name in ('SystemRoot', 'WINDIR', 'TEMP', 'TMP') if name in os.environ}
    environment['PATH'] = str(Path(executable).parent) + os.pathsep + str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32')
    environment.update({'npm_config_userconfig': str(directory / 'runner-empty-user.npmrc'),
        'npm_config_globalconfig': str(directory / 'runner-empty-global.npmrc'),
        'npm_config_cache': str(directory / 'runner-npm-cache'), 'npm_config_logs_max': '0',
        'npm_config_update_notifier': 'false', 'npm_config_audit': 'false', 'npm_config_fund': 'false',
        'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1'})
    return environment


def runtime_paths(executable: str, argv: list[str], environment: dict[str, str]) -> list[Path]:
    paths = [Path(executable), *[Path(arg) for arg in argv[:1] if Path(arg).is_absolute()]]
    configuration = Path(executable).parent.parent / 'pyvenv.cfg'
    if configuration.is_file():
        paths.append(configuration)
    paths.extend(Path(environment[key]) for key in ('npm_config_userconfig', 'npm_config_globalconfig'))
    return paths


def installed_tool(kind: str, root: Path) -> tuple[str, list[str]] | None:
    if kind == 'npm':
        directory = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'nodejs'
        executable, cli = directory / 'node.exe', directory / 'node_modules/npm/bin/npm-cli.js'
        if executable.is_file() and cli.is_file() and not executable.is_relative_to(root):
            identity(executable)
            identity(cli)
            return str(executable), [str(cli)]
    if kind == 'python':
        candidates = [Path(os.environ.get('LOCALAPPDATA', 'C:/missing')) / 'Programs/Python' / f'Python{version}' / 'python.exe' for version in ('314', '313', '312')]
        if not getattr(sys, 'frozen', False):
            candidates.append(Path(sys.executable))
        for path in candidates:
            if path.is_absolute() and path.is_file() and not path.resolve().is_relative_to(root):
                identity(path)
                return str(path), []
    return None


class Detection:
    def __init__(self, snapshots: Snapshots, resolver: Callable[[str, Path], tuple[str, list[str]] | None] = installed_tool):
        self.snapshots = snapshots
        self.projects = snapshots.projects
        self.resolver = resolver
        self.candidates: dict[str, dict[str, Any]] = {}
        database = self.projects.connection.execute('PRAGMA database_list').fetchone()[2]
        self.directory = Path(database).parent if database else None

    def environment(self, executable: str) -> dict[str, str]:
        if self.directory is None:
            raise ScopeError('STORAGE_UNAVAILABLE')
        environment = runner_environment(executable, self.directory)
        for key in ('npm_config_userconfig', 'npm_config_globalconfig'):
            path = Path(environment[key])
            try:
                with path.open('xb') as stream:
                    secure_path(path)
                    stream.flush()
                    os.fsync(stream.fileno())
            except FileExistsError:
                pass
            identity(path)
            if path.stat().st_size != 0 or path.stat().st_nlink != 1:
                raise ScopeError('PRECONDITION_FAILED')
            secure_path(path)
        return environment

    def configs(self, grant: dict[str, str], cwd: str) -> tuple[dict[str, str], dict[str, str | None]]:
        if cwd != '.':
            relative_path(cwd)
        directory = Path(grant['root']) if cwd == '.' else Path(grant['root']).joinpath(*cwd.split('/'))
        identity(directory)
        if not directory.is_dir():
            raise ScopeError('PATH_OUTSIDE_SCOPE')
        texts: dict[str, str] = {}
        hashes: dict[str, str | None] = {}
        for name in CONFIGS:
            path = name if cwd == '.' else cwd + '/' + name
            # These files can contain credentials; their presence blocks planning.
            if name in ('.npmrc', '.yarnrc.yml'):
                candidate = directory / name
                hashes[name] = identity(candidate) if candidate.exists() else None
                continue
            raw = read_scoped(grant, path)
            hashes[name] = image_hash(raw)
            if raw is not None:
                try:
                    text = raw.decode('utf-8-sig', 'strict')
                except UnicodeError:
                    raise ScopeError('INVALID_REQUEST') from None
                reason = ai_core.policy.content_exclusion(text, byte_count=len(raw))
                if reason and reason.value == 'secret_suspected':
                    raise ScopeError('ACCESS_DENIED')
                texts[name] = text
        return texts, hashes

    def detect(self, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        grant = self.projects.grant(project_id)
        cwd = payload['cwdRelative']
        paths = [name if cwd == '.' else cwd + '/' + name for name in CONFIGS]
        with pinned_paths(grant, paths):
            texts, hashes = self.configs(grant, cwd)
            evidence: list[dict[str, str]] = []
            proposals: list[dict[str, Any]] = []
            conflicts: list[str] = []
            managers = sorted({manager for name, manager in LOCKS.items() if name in texts})
            chosen = payload['packageManager']
            if len(managers) > 1 and chosen is None:
                conflicts.append('multiple-lockfiles-select-package-manager')
            if chosen is not None and chosen not in managers:
                raise ScopeError('INVALID_REQUEST')
            manager = chosen or (managers[0] if managers else 'npm')
            if hashes['.npmrc'] or hashes['.yarnrc.yml']:
                conflicts.append('credential-config-present-planning-blocked')
            if 'package.json' in texts:
                try:
                    package_value: object = decode(texts['package.json'].encode('utf8'))
                    if not isinstance(package_value, dict):
                        raise ValueError()
                    package = cast(dict[str, Any], package_value)
                    scripts_value: object = package.get('scripts', {})
                    if not isinstance(scripts_value, dict):
                        raise ValueError()
                    if any(not isinstance(k, str) or not isinstance(v, str) or len(v) > 4096 for k, v in cast(dict[object, object], scripts_value).items()):
                        raise ValueError()
                    scripts = cast(dict[str, str], scripts_value)
                except (ValueError, ProtocolError):
                    raise ScopeError('INVALID_REQUEST') from None
                evidence.append({'language': 'javascript/typescript', 'path': 'package.json', 'confidence': 'manifest'})
                dependencies: set[str] = set()
                for section in ('dependencies', 'devDependencies', 'peerDependencies'):
                    raw_dependencies: object = package.get(section, {})
                    if not isinstance(raw_dependencies, dict):
                        raise ScopeError('INVALID_REQUEST')
                    dependencies.update(str(name).lower() for name in cast(dict[str, Any], raw_dependencies))
                for name in ('react', 'vue', 'svelte', 'next', 'typescript'):
                    if name in dependencies:
                        evidence.append({'language': 'typescript' if name == 'typescript' else 'javascript', 'framework': name,
                                         'path': 'package.json', 'confidence': 'manifest'})
                for name in ('test', 'lint', 'typecheck'):
                    if name not in scripts:
                        continue
                    hooks = {key: scripts[key] for key in ('pre' + name, name, 'post' + name) if key in scripts}
                    proposals.append({'name': name, 'tool': manager, 'argv': ['run', name], 'scripts': hooks, 'evidence': 'package.json'})
            if any(name in texts for name in ('pyproject.toml', 'requirements.txt', 'setup.py', 'setup.cfg', 'pytest.ini')):
                evidence.append({'language': 'python', 'path': next(name for name in ('pyproject.toml', 'requirements.txt', 'setup.py', 'setup.cfg', 'pytest.ini') if name in texts), 'confidence': 'manifest'})
                parsed: dict[str, Any] = {}
                if 'pyproject.toml' in texts:
                    try:
                        parsed = tomllib.loads(texts['pyproject.toml'])
                    except (ValueError, RecursionError):
                        raise ScopeError('INVALID_REQUEST') from None
                tool = parsed.get('tool', {})
                if not isinstance(tool, dict):
                    raise ScopeError('INVALID_REQUEST')
                # Only existing declared configuration/dependencies suggest a tool.
                dependencies = {name.lower().replace('_', '-') for name in re.findall(r'(?m)^\s*([A-Za-z0-9_-]+)', texts.get('requirements.txt', ''))}
                project: object = parsed.get('project', {})
                if isinstance(project, dict):
                    project_data = cast(dict[str, Any], project)
                    declared = project_data.get('dependencies', [])
                    if isinstance(declared, list):
                        for entry in cast(list[object], declared):
                            if isinstance(entry, str) and (match := re.match(r'([A-Za-z0-9_-]+)', entry)):
                                dependencies.add(match.group(1).lower().replace('_', '-'))
                for name in ('django', 'flask', 'fastapi', 'pytest'):
                    if name in dependencies:
                        evidence.append({'language': 'python', 'framework': name, 'path': 'pyproject.toml' if 'pyproject.toml' in texts else 'requirements.txt', 'confidence': 'manifest'})
                if 'pytest.ini' in texts or 'pytest' in tool or 'pytest' in dependencies:
                    proposals.append({'name': 'pytest', 'tool': 'python', 'argv': ['-m', 'pytest'], 'scripts': {}, 'evidence': 'python-config'})
                if any(name in texts for name in ('ruff.toml', '.ruff.toml')) or 'ruff' in tool or 'ruff' in dependencies:
                    proposals.append({'name': 'ruff', 'tool': 'python', 'argv': ['-m', 'ruff', 'check', '--no-fix', '.'], 'scripts': {}, 'evidence': 'python-config'})
                if 'unittest' in tool:
                    proposals.append({'name': 'unittest', 'tool': 'python', 'argv': ['-m', 'unittest', 'discover'], 'scripts': {}, 'evidence': 'pyproject.toml'})
            for row in evidence:
                row.setdefault('framework', '')
            if hashes != self.configs(grant, cwd)[1]:
                raise ScopeError('CAPTURE_CHANGED')
            self.candidates = {key: value for key, value in self.candidates.items() if value['expires'] > time.monotonic()}
            items: list[dict[str, Any]] = []
            if not conflicts:
                for proposal in proposals:
                    if len(self.candidates) >= 32:
                        raise ScopeError('BUSY')
                    candidate_id = str(uuid.uuid4())
                    runtime = self.resolver(proposal['tool'], Path(grant['root']))
                    candidate = {**proposal, 'projectId': project_id, 'cwdRelative': cwd, 'configHashes': hashes,
                                 'runtime': runtime, 'expires': time.monotonic() + 300}
                    self.candidates[candidate_id] = candidate
                    items.append({'candidateId': candidate_id, 'name': proposal['name'], 'packageManager': proposal['tool'],
                                  'available': runtime is not None, 'scripts': proposal['scripts']})
            return {'projectId': project_id, 'cwdRelative': cwd, 'evidence': evidence, 'packageManagers': managers,
                    'conflicts': conflicts, 'configHash': fingerprint(hashes), 'candidates': items,
                    'limitations': ['manifest-only-no-import-or-execution', 'other-languages-not-supported',
                                    'monorepo-cwd-must-be-selected', 'no-dependency-installation', 'execution-requires-native-trust-and-plan-approval', 'dependencies-are-declarations-only']}

    def plan(self, project_id: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
        def action() -> dict[str, Any]:
            self.snapshots.get(project_id, payload['snapshotId'])
            candidate = self.candidates.get(payload['candidateId'])
            if candidate is None or candidate['projectId'] != project_id:
                raise ScopeError('RESOURCE_NOT_FOUND')
            if candidate['expires'] <= time.monotonic():
                raise ScopeError('INPUT_EXPIRED')
            grant = self.projects.grant(project_id)
            with pinned(grant):
                if self.configs(grant, candidate['cwdRelative'])[1] != candidate['configHashes']:
                    raise ScopeError('CAPTURE_CHANGED')
                check_script_modes(candidate['scripts'])
                runtime = self.resolver(candidate['tool'], Path(grant['root']))
                if runtime is None or runtime != candidate['runtime']:
                    raise ScopeError('COMMAND_NOT_ALLOWED')
                executable, prefix = runtime
                executable_path = Path(executable)
                if not executable_path.is_absolute() or executable_path.resolve().is_relative_to(Path(grant['root'])):
                    raise ScopeError('COMMAND_NOT_ALLOWED')
                executable_identity = identity(executable_path)
                environment = self.environment(executable)
                stable = {'projectId': project_id, 'snapshotId': payload['snapshotId'], 'executable': executable,
                    'executableIdentity': executable_identity, 'argv': [*prefix, *candidate['argv']], 'cwdRelative': candidate['cwdRelative'],
                    'configHash': fingerprint(candidate['configHashes']), 'scripts': candidate['scripts'],
                    'envAllowlist': list(environment), 'environment': environment, 'timeoutMs': 120000, 'outputLimit': 1048576,
                    'risk': 'same-user-code-and-network-access-not-sandboxed', 'executionAllowed': False}
                plan_id = str(uuid.uuid4())
                result = {**stable, 'planId': plan_id, 'planHash': fingerprint(stable)}
                encrypted = self.snapshots.cipher.encrypt(json.dumps({'plan': result, 'configHashes': candidate['configHashes']}).encode(),
                    f'GitVizWorkspace/project/{project_id}/plan/{plan_id}/schema-3'.encode())
                self.projects.connection.execute('INSERT INTO command_plan VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (plan_id, project_id, payload['snapshotId'], executable, json.dumps(stable['argv']), stable['cwdRelative'],
                     stable['configHash'], json.dumps(stable['envAllowlist']), stable['timeoutMs'], stable['outputLimit'], stable['risk'], result['planHash'], encrypted))
                # The generic idempotency ledger contains only an opaque reference,
                # never the script/config source protected by the plan envelope.
                return {'planId': plan_id}
        reference = self.projects.mutate(project_id, 'validation.plan', key, payload, action)
        plan_id = reference['planId']
        row = self.projects.connection.execute('SELECT payload FROM command_plan WHERE project_id=? AND id=?', (project_id, plan_id)).fetchone()
        if row is None:
            raise ScopeError('RESOURCE_NOT_FOUND')
        envelope = self.snapshots.cipher.decrypt(row[0], f'GitVizWorkspace/project/{project_id}/plan/{plan_id}/schema-3'.encode())
        result: dict[str, Any] = json.loads(envelope)['plan']
        return result
