"""Immutable refactor materialization and versioned native approvals; no model calls here."""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, Generator

from . import events
from .patches import apply_patch_bytes, writable_path
from .review import Reviews
from .scoped_files import ScopeError
from .snapshots import fingerprint, image_hash, index_hash
from .write_files import LockedFile, write_scope
from .git_service import LocalGit


class Proposals:
    def __init__(self, reviews: Reviews):
        self.reviews = reviews
        self.snapshots = reviews.snapshots
        self.projects = self.snapshots.projects
        self.db = self.snapshots.db
        self.busy: set[str] = set()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute('UPDATE approval SET revoked_at=? WHERE consumed_at IS NULL AND revoked_at IS NULL', (time.time(),))
            for project, proposal in self.db.execute("SELECT project_id,id FROM proposal WHERE status IN ('applying','partial')").fetchall():
                self.transition(project, proposal, 'recovery_required')
            self.db.execute("UPDATE apply_journal SET state='recovery_required' WHERE state IN ('prepared','original_moved','replaced')")
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def encrypt(self, project: str, resource: str, value: Any) -> bytes:
        return self.snapshots.cipher.encrypt(json.dumps(value, ensure_ascii=False).encode(),
            f'GitVizWorkspace/project/{project}/proposal/{resource}/schema-5'.encode())

    def decrypt(self, project: str, resource: str, value: bytes) -> Any:
        return json.loads(self.snapshots.cipher.decrypt(value,
            f'GitVizWorkspace/project/{project}/proposal/{resource}/schema-5'.encode()))

    def row(self, project: str, proposal: str) -> tuple[Any, ...]:
        row = self.db.execute('SELECT snapshot_id,review_run_id,previous_proposal_id,version,status,payload,patch_hash,preconditions_hash FROM proposal WHERE project_id=? AND id=?', (project, proposal)).fetchone()
        if row is None:
            raise ScopeError('RESOURCE_NOT_FOUND')
        return row

    def body(self, project: str, proposal: str) -> dict[str, Any]:
        row = self.row(project, proposal)
        value = self.decrypt(project, proposal, row[5])
        changes = [{k: v for k, v in change.items() if k not in ('beforeBase64', 'afterBase64')} for change in value['changes']]
        if fingerprint(changes) != row[6] or fingerprint(value['conditions']) != row[7]:
            raise ScopeError('PRECONDITION_FAILED')
        return value

    def transition(self, project: str, proposal: str, status: str) -> None:
        self.db.execute('UPDATE proposal SET status=?,version=version+1 WHERE project_id=? AND id=?', (status, project, proposal))
        row = self.row(project, proposal)
        self.db.execute('INSERT INTO proposal_event VALUES(?,?,?,?,?,?)', (str(uuid.uuid4()), project, proposal, row[3], status, time.time()))
        events.record(self.db, project=project, source_type='proposal', source_id=proposal, revision=row[3], status=status)

    def recovery_required(self, project: str) -> bool:
        return bool(self.db.execute("SELECT 1 FROM proposal WHERE project_id=? AND status IN ('applying','partial','recovery_required')", (project,)).fetchone())

    def list_recovery(self, project: str) -> dict[str, Any]:
        rows = self.db.execute("SELECT id,version FROM proposal WHERE project_id=? AND status='recovery_required' ORDER BY created_at,id LIMIT 51", (project,)).fetchall()
        return {'projectId': project, 'proposals': [{'proposalId': row[0], 'version': row[1], 'status': 'recovery_required'} for row in rows[:50]], 'truncated': len(rows) > 50}

    @contextmanager
    def locked(self, project: str, body: dict[str, Any]) -> Generator[tuple[dict[str, str], list[LockedFile], dict[str, Any]], None, None]:
        grant = self.projects.grant(project)
        paths = [writable_path(change['path']) for change in body['changes']]
        with ExitStack() as stack:
            stack.enter_context(write_scope(grant, paths))
            files = [stack.enter_context(LockedFile(Path(grant['root']).joinpath(*path.split('/')))) for path in paths]
            conditions = {'rootIdentity': grant['rootIdentity'], 'head': (self.projects.git or LocalGit()).head(grant),
                'indexHash': index_hash(grant), 'files': [{'path': path, 'identity': file.file_identity,
                    'hash': file.digest(), 'securityHash': fingerprint(file.security)} for path, file in zip(paths, files)]}
            yield grant, files, conditions

    def create(self, project: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
        self.reviews.jobs.drain()
        def action() -> dict[str, Any]:
            run = payload['refactorRunId']
            prior = self.db.execute('SELECT id FROM proposal WHERE project_id=? AND review_run_id=?', (project, run)).fetchone()
            if prior:
                return {'proposalId': prior[0]}
            row = self.db.execute("SELECT snapshot_id,consent_id,result FROM review_run WHERE project_id=? AND id=? AND kind='refactor' AND status='succeeded'", (project, run)).fetchone()
            if row is None:
                raise ScopeError('PRECONDITION_FAILED')
            snapshot = self.snapshots.get(project, row[0])
            output = self.reviews.jobs.decrypt(project, run, row[2])
            consent = self.db.execute('SELECT payload FROM transfer_consent WHERE project_id=? AND id=?', (project, row[1])).fetchone()
            manifest = self.reviews.jobs.decrypt(project, row[1], consent[0])
            sources = {file['fileId']: file for file in manifest['files']}
            changes: list[dict[str, Any]] = []
            for change in output['changes']:
                source = sources[change['fileId']]
                before = source['source'].encode()
                after = apply_patch_bytes(change['path'], before, change['patch'])
                if image_hash(before) != change['preimageHash'] or image_hash(after) != change['targetHash']:
                    raise ScopeError('PRECONDITION_FAILED')
                changes.append({**change, 'beforeBase64': base64.b64encode(before).decode(), 'afterBase64': base64.b64encode(after).decode()})
            body: dict[str, Any] = {**output, 'changes': changes, 'indexHash': snapshot['indexHash'], 'head': snapshot['head'],
                'limitations': [*output['limitations'], 'two-stage-publication-has-a-missing-path-window', 'effects-are-unverified-predictions']}
            with self.locked(project, body) as (_, files, conditions):
                if (conditions['indexHash'] != snapshot['indexHash'] or conditions['head'] != snapshot['head']
                        or any(file.digest() != change['preimageHash'] for file, change in zip(files, changes))):
                    raise ScopeError('PRECONDITION_FAILED')
                body['conditions'] = conditions
            if len(json.dumps(body, ensure_ascii=False).encode()) > 150000:
                raise ScopeError('OUTPUT_LIMIT')
            proposal = str(uuid.uuid4())
            patch_hash = fingerprint(output['changes'])
            previous = manifest.get('revisionOf')
            if previous is not None and (self.row(project, previous)[0] != row[0] or self.row(project, previous)[4] != 'superseded'):
                raise ScopeError('PRECONDITION_FAILED')
            self.db.execute('INSERT INTO proposal VALUES(?,?,?,?,?,1,\'pending\',?,?,?,?)',
                (proposal, project, row[0], run, previous, self.encrypt(project, proposal, body), patch_hash, fingerprint(body['conditions']), time.time()))
            self.db.execute('INSERT INTO proposal_event VALUES(?,?,?,?,?,?)', (str(uuid.uuid4()), project, proposal, 1, 'pending', time.time()))
            return {'proposalId': proposal}
        result = self.projects.mutate(project, 'proposals.create', key, payload, action)
        return self.get(project, result['proposalId'])

    def get(self, project: str, proposal: str) -> dict[str, Any]:
        row = self.row(project, proposal)
        body = self.body(project, proposal)
        return {'projectId': project, 'proposalId': proposal, 'snapshotId': row[0], 'refactorRunId': row[1],
            'previousProposalId': row[2], 'version': row[3], 'status': row[4], 'patchHash': row[6], 'preconditionsHash': row[7],
            'purpose': body['purpose'], 'expectedEffects': body['expectedEffects'], 'risks': body['risks'],
            'validationSuggestions': body['validationSuggestions'], 'limitations': body['limitations'],
            'changes': [{k: value for k, value in change.items() if k not in ('beforeBase64', 'afterBase64')} for change in body['changes']],
            'journal': [{'journalId': item[0], 'path': item[1], 'state': item[2]} for item in self.db.execute(
                'SELECT id,relative_path,state FROM apply_journal WHERE project_id=? AND proposal_id=? ORDER BY relative_path', (project, proposal))]}

    def reject(self, project: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
        def action() -> dict[str, Any]:
            row = self.row(project, payload['proposalId'])
            if row[3] != payload['expectedVersion'] or row[4] not in ('pending', 'approved'):
                raise ScopeError('CONFLICT')
            self.db.execute('UPDATE approval SET revoked_at=? WHERE project_id=? AND proposal_id=? AND consumed_at IS NULL', (time.time(), project, payload['proposalId']))
            self.transition(project, payload['proposalId'], 'rejected')
            return {'proposalId': payload['proposalId']}
        result = self.projects.mutate(project, 'proposals.reject', key, payload, action)
        return self.get(project, result['proposalId'])

    def approve(self, project: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
        def action() -> dict[str, Any]:
            proposal = payload['proposalId']
            row = self.row(project, proposal)
            if (row[3] != payload['expectedVersion'] or row[4] not in ('pending', 'approved')
                    or row[6] != payload['patchHash'] or row[7] != payload['preconditionsHash']):
                raise ScopeError('PRECONDITION_FAILED')
            with self.locked(project, self.body(project, proposal)) as (_, _, conditions):
                if fingerprint(conditions) != row[7]:
                    raise ScopeError('PRECONDITION_FAILED')
            self.db.execute('UPDATE approval SET revoked_at=? WHERE project_id=? AND proposal_id=? AND consumed_at IS NULL', (time.time(), project, proposal))
            approval = str(uuid.uuid4())
            now = time.time()
            self.db.execute('INSERT INTO approval VALUES(?,?,?,\'proposal\',?,?,?,?,?,NULL,NULL)',
                (approval, project, proposal, row[6], row[7], hashlib.sha256(payload['capability'].encode()).hexdigest(), now, now + 300))
            self.transition(project, proposal, 'approved')
            return {'approvalId': approval}
        result = self.projects.mutate(project, 'proposals.approve', key, {k: v for k, v in payload.items() if k != 'capability'}, action)
        return {'projectId': project, 'proposalId': payload['proposalId'], 'approvalId': result['approvalId']}

    def request_revision(self, project: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
        self.reviews.jobs.drain()
        def action() -> dict[str, Any]:
            row = self.row(project, payload['proposalId'])
            if row[3] != payload['expectedVersion'] or row[4] not in ('pending', 'approved', 'rejected', 'superseded'):
                raise ScopeError('CONFLICT')
            consent = self.db.execute('SELECT c.id,c.payload FROM transfer_consent c JOIN review_run r ON r.consent_id=c.id WHERE r.id=? AND c.project_id=?', (row[1], project)).fetchone()
            original = self.reviews.jobs.decrypt(project, consent[0], consent[1])
            purpose = original['purpose'] + '\n재요청: ' + payload['instruction']
            if not payload['instruction'].strip() or len(purpose) > 1000:
                raise ScopeError('INPUT_TOO_LARGE')
            value = self.reviews.preview(project, {'snapshotId': row[0], 'kind': 'refactor', 'purpose': purpose,
                'profileId': original['profile']['id'], 'generation': 1}, drain=False)
            value['revisionOf'] = payload['proposalId']
            value['previewHash'] = fingerprint({k: v for k, v in value.items() if k != 'previewHash'})
            self.reviews.previews[value['previewId']] = (value['expiresAt'], value)
            self.db.execute('UPDATE approval SET revoked_at=? WHERE project_id=? AND proposal_id=? AND consumed_at IS NULL', (time.time(), project, payload['proposalId']))
            self.transition(project, payload['proposalId'], 'superseded')
            return {'previewId': value['previewId']}
        result = self.projects.mutate(project, 'proposals.requestRevision', key, payload, action)
        saved = self.reviews.previews.get(result['previewId'])
        if saved is None or saved[0] <= time.time():
            raise ScopeError('CONSENT_EXPIRED')
        return json.loads(json.dumps(saved[1]))

    def consume(self, project: str, proposal: str, approval: str, capability: str, target_type: str, target: str, conditions: str) -> None:
        row = self.db.execute('SELECT target_type,target_hash,preconditions_hash,capability_hash,expires_at,consumed_at,revoked_at FROM approval WHERE project_id=? AND proposal_id=? AND id=?', (project, proposal, approval)).fetchone()
        if (row is None or row[0] != target_type or row[1] != target or row[2] != conditions
                or not secrets.compare_digest(row[3], hashlib.sha256(capability.encode()).hexdigest())):
            raise ScopeError('APPROVAL_REQUIRED')
        if row[4] <= time.time() or row[5] is not None or row[6] is not None:
            raise ScopeError('APPROVAL_EXPIRED')
        self.db.execute('UPDATE approval SET consumed_at=? WHERE id=?', (time.time(), approval))
