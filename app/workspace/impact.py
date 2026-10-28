"""Bounded immutable-input evidence. Syntax references are never runtime verification."""
from __future__ import annotations

import ast
import base64
import json
import time
import uuid
from typing import Any

from .proposals import Proposals
from .scoped_files import ScopeError
from .snapshots import image_hash
from .patches import validate_encoding
from .remote_connections import RemoteConnections


def python_references(file: dict[str, Any], source: bytes) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        validate_encoding(file['path'], source)
    except ScopeError:
        return [], ['python-source-encoding-unsupported']
    text = source.decode('utf-8-sig')
    try:
        tree = ast.parse(text, filename=file['path'], mode='exec')
    except (SyntaxError, RecursionError, ValueError):
        return [], ['python-parse-failed']
    offsets = [3 if source.startswith(b'\xef\xbb\xbf') else 0]
    for line in text.split('\n')[:-1]:
        offsets.append(offsets[-1] + len(line.encode()) + 1)
    nodes: list[ast.AST] = []
    for node in ast.walk(tree):
        nodes.append(node)
        if len(nodes) > 30000:
            return [], ['python-node-limit']
    definitions: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            definitions.setdefault(node.name, []).append(node)
    shadowed = {node.id for node in nodes if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)}
    shadowed |= {node.arg for node in nodes if isinstance(node, ast.arg)}
    shadowed |= {alias.asname or alias.name.split('.')[0] for node in nodes if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names}
    shadowed |= {node.name for node in nodes if isinstance(node, ast.ClassDef)}
    shadowed |= {node.name for node in nodes if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (node not in tree.body or node.decorator_list)}
    dynamic = (any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ('eval', 'exec', 'globals', 'locals') for node in nodes)
        or any(isinstance(node, ast.ImportFrom) and any(alias.name == '*' for alias in node.names) for node in nodes))
    records: list[dict[str, Any]] = []
    for node in nodes:
        reference: ast.AST | None = None
        kind = ''
        target_line: int | None = None
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            reference, kind = node, 'import'
        elif isinstance(node, ast.Call):
            reference, kind = node.func, 'call'
            if isinstance(node.func, ast.Name) and not dynamic and node.func.id not in shadowed:
                candidates = definitions.get(node.func.id, [])
                if len(candidates) == 1:
                    target_line = candidates[0].lineno
        if reference is None or not hasattr(reference, 'lineno'):
            continue
        location: Any = reference
        start = offsets[int(location.lineno) - 1] + int(location.col_offset)
        end = offsets[int(location.end_lineno) - 1] + int(location.end_col_offset)
        symbol = source[start:end].decode('utf-8')
        if len(symbol) > 256:
            continue
        records.append({'kind': 'static_reference', 'observed': True, 'fileId': file['fileId'],
            'path': file['path'], 'facet': file['facet'], 'sourceHash': file['sourceHash'], 'referenceKind': kind,
            'symbol': symbol, 'startByte': start, 'endByte': end, 'line': location.lineno,
            'column': len(source[offsets[location.lineno - 1]:start].decode('utf-8')) + 1,
            'resolved': target_line is not None, 'targetFileId': file['fileId'] if target_line else None,
            'targetLine': target_line, 'toolVersion': 'python-ast-v1'})
        if len(records) >= 500:
            break
    return records, ['syntax-only-not-runtime-callgraph', 'dynamic-dispatch-and-nested-shadowing-unresolved', *(['python-record-limit'] if len(records) >= 500 else [])]


class Impact:
    def __init__(self, proposals: Proposals):
        self.proposals = proposals
        self.snapshots = proposals.snapshots
        self.db = proposals.db
        self.sessions: dict[str, dict[str, Any]] = {}

    def prepare(self, project: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.sessions = {key: value for key, value in self.sessions.items() if value['expires'] > time.monotonic()}
        if len(self.sessions) >= 4:
            raise ScopeError('BUSY')
        if payload.get('historySnapshotRef') is not None:
            raise ScopeError('HISTORY_MAPPING_REQUIRED')
        snapshot = self.snapshots.get(project, payload['snapshotId'])
        files: dict[str, dict[str, Any]] = {}
        records: list[dict[str, Any]] = []
        limitations: set[str] = set()
        for item in snapshot['files']:
            if item['state'] != 'read' or item['afterHash'] is None:
                limitations.add('excluded-or-deleted-file-not-parsed')
                continue
            row = self.db.execute('SELECT payload FROM change_file WHERE project_id=? AND snapshot_id=? AND id=?',
                (project, snapshot['snapshotId'], item['fileId'])).fetchone()
            body = json.loads(self.snapshots.cipher.decrypt(row[0], self.snapshots.aad(project, snapshot['snapshotId'], item['fileId'])))
            source = base64.b64decode(body['after'], validate=True)
            if image_hash(source) != item['afterHash']:
                raise ScopeError('PRECONDITION_FAILED')
            language = 'python' if item['path'].endswith('.py') else 'typescript' if item['path'].endswith(('.js', '.jsx', '.mjs', '.cjs', '.ts', '.tsx', '.mts', '.cts')) else 'unsupported'
            descriptor = {'fileId': item['fileId'], 'path': item['path'], 'facet': item['facet'],
                'sourceHash': item['afterHash'], 'byteCount': len(source), 'language': language}
            files[item['fileId']] = {**descriptor, 'source': source}
            if language == 'python':
                found, limits = python_references(descriptor, source)
                remaining = max(0, 1500 - len(records))
                records.extend(found[:remaining])
                if len(found) > remaining:
                    limitations.add('record-limit')
                limitations.update(limits)
            elif language == 'unsupported':
                limitations.add('unsupported-language-symbol-analysis')
        proposal = payload.get('proposalId')
        inferences: list[dict[str, Any]] = []
        if proposal is not None:
            row = self.proposals.row(project, proposal)
            if row[0] != snapshot['snapshotId']:
                raise ScopeError('PRECONDITION_FAILED')
            body = self.proposals.body(project, proposal)
            inferences = [{'kind': 'ai_inference', 'observed': False, 'proposalId': proposal, 'text': value}
                for value in body['expectedEffects']]
        session = str(uuid.uuid4())
        self.sessions[session] = {'project': project, 'snapshot': snapshot, 'files': files, 'records': records,
            'limitations': limitations, 'inferences': inferences, 'expires': time.monotonic() + 90}
        return {'projectId': project, 'sessionId': session, 'snapshotId': snapshot['snapshotId'],
            'files': [{k: v for k, v in file.items() if k != 'source'} for file in files.values()]}

    def session(self, project: str, session: str) -> dict[str, Any]:
        value = self.sessions.get(session)
        if value is None or value['expires'] <= time.monotonic() or value['project'] != project:
            raise ScopeError('RESOURCE_NOT_FOUND')
        return value

    def read(self, project: str, payload: dict[str, Any]) -> dict[str, Any]:
        session = self.session(project, payload['sessionId'])
        file = session['files'].get(payload['fileId'])
        if file is None or file['language'] != 'typescript':
            raise ScopeError('RESOURCE_NOT_FOUND')
        offset = payload['offset']
        if not 0 <= offset <= len(file['source']):
            raise ScopeError('INVALID_REQUEST')
        return {'projectId': project, 'sessionId': payload['sessionId'], 'fileId': payload['fileId'],
            'chunkBase64': base64.b64encode(file['source'][offset:offset + 16000]).decode(), 'totalBytes': len(file['source'])}

    def release(self, project: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.session(project, payload['sessionId'])
        del self.sessions[payload['sessionId']]
        return {'projectId': project, 'releasedSessionId': payload['sessionId']}

    def append(self, project: str, payload: dict[str, Any]) -> dict[str, Any]:
        session = self.session(project, payload['sessionId'])
        for record in payload['records']:
            file = session['files'].get(record['fileId'])
            if (file is None or file['language'] != 'typescript' or record['sourceHash'] != file['sourceHash']
                    or record['path'] != file['path'] or record['facet'] != file['facet']
                    or record['kind'] != 'static_reference' or record['observed'] is not True
                    or record['toolVersion'] != 'typescript-6.0.3-v1'):
                raise ScopeError('OUTPUT_INVALID')
            start, end = record['startByte'], record['endByte']
            if not 0 <= start < end <= len(file['source']) or file['source'][start:end].decode('utf-8') != record['symbol']:
                raise ScopeError('OUTPUT_INVALID')
            target = record['targetFileId']
            if target is not None and (target not in session['files'] or session['files'][target]['facet'] != file['facet']):
                raise ScopeError('OUTPUT_INVALID')
            if len(session['records']) < 1500:
                session['records'].append(record)
            else:
                session['limitations'].add('record-limit')
        return {'projectId': project, 'accepted': True}

    def finish(self, project: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
        def action() -> dict[str, Any]:
            session = self.session(project, payload['sessionId'])
            analysis = str(uuid.uuid4())
            result = {'records': session['records'], 'inferences': session['inferences'],
                'limitations': sorted(session['limitations'] | set(payload['limitations'])),
                'historical': {'status': 'unavailable', 'reason': 'authenticated-remote-mapping-required'}}
            self.db.execute('INSERT INTO impact_analysis VALUES(?,?,?,?,?,?)',
                (analysis, project, session['snapshot']['snapshotId'], session['snapshot']['inputHash'],
                 self.proposals.encrypt(project, analysis, result), time.time()))
            return {'analysisId': analysis}
        result = self.proposals.projects.mutate(project, 'impact.finish', key, payload, action)
        return self.get(project, {'analysisId': result['analysisId'], 'offset': 0})

    def get(self, project: str, payload: dict[str, Any]) -> dict[str, Any]:
        row = self.db.execute('SELECT snapshot_id,input_hash,payload FROM impact_analysis WHERE project_id=? AND id=?', (project, payload['analysisId'])).fetchone()
        if row is None:
            raise ScopeError('RESOURCE_NOT_FOUND')
        body = self.proposals.decrypt(project, payload['analysisId'], row[2])
        start = payload['offset']
        records = body['records'][start:start + 25]
        return {'projectId': project, 'analysisId': payload['analysisId'], 'snapshotId': row[0], 'inputHash': row[1],
            **{k: v for k, v in body.items() if k not in ('records', '_historicalBinding')}, 'records': records, 'totalRecords': len(body['records']),
            'nextOffset': start + 25 if start + 25 < len(body['records']) else None}

    def historical(self, project: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
        analysis = payload['analysisId']
        row = self.db.execute('SELECT snapshot_id,payload FROM impact_analysis WHERE project_id=? AND id=?', (project, analysis)).fetchone()
        if row is None:
            raise ScopeError('RESOURCE_NOT_FOUND')
        snapshot = self.snapshots.get(project, row[0])
        body = self.proposals.decrypt(project, analysis, row[1])
        if payload['phase'] == 'inspect':
            return {'projectId': project, 'analysisId': analysis, 'head': snapshot['head'],
                    'paths': sorted({file['path'] for file in snapshot['files']}), 'binding': body.get('_historicalBinding')}
        proof, history = payload['proof'], payload['historical']
        if not time.time() < proof['expiresAt'] <= time.time() + 31:
            raise ScopeError('PRECONDITION_FAILED')
        inspected = RemoteConnections(self.snapshots.projects).inspect(project)
        connection = inspected['connection']
        fields = ('connectionId', 'accountId', 'serverOrigin', 'repositoryId', 'githubId', 'canonicalUrl', 'configHash')
        if (connection is None or inspected['configHash'] != proof['configHash'] or any(connection[name] != proof[name] for name in fields)
                or history['targetSha'] != snapshot['head'] or history['repositoryId'] != proof['repositoryId']):
            raise ScopeError('PRECONDITION_FAILED')
        paths = {file['path'] for file in snapshot['files']}
        if any(pair['pathA'] not in paths and pair['pathB'] not in paths for pair in history['pairs']):
            raise ScopeError('OUTPUT_INVALID')
        def action() -> dict[str, Any]:
            if body['historical']['status'] == 'available':
                if body['historical'] != history:
                    raise ScopeError('CONFLICT')
            else:
                body['historical'] = history
                body['_historicalBinding'] = {name: proof[name] for name in fields}
                self.db.execute('UPDATE impact_analysis SET payload=? WHERE project_id=? AND id=?',
                    (self.proposals.encrypt(project, analysis, body), project, analysis))
            return {'analysisId': analysis}
        self.snapshots.projects.mutate(project, 'impact.historical', key, payload, action)
        return self.get(project, {'analysisId': analysis, 'offset': 0})
