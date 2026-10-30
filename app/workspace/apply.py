"""Approved two-stage publication and explicit conservative recovery. No automatic rollback."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
import time
import uuid
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Callable

from .patches import RESERVED, writable_path
from .proposals import Proposals
from .scoped_files import ScopeError
from .snapshots import fingerprint, index_hash, image_hash
from .write_files import LockedFile, write_scope


class Application:
    def __init__(self, proposals: Proposals):
        self.proposals = proposals
        self.db = proposals.db

    def save(self, project: str, journal: str, data: dict[str, Any], state: str) -> None:
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute('UPDATE apply_journal SET backup=?,state=?,metadata=? WHERE project_id=? AND id=?',
                (self.proposals.encrypt(project, journal, data), state,
                 json.dumps({k: v for k, v in data.items() if k not in ('beforeBase64', 'afterBase64', 'security')}), project, journal))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def prior(self, project: str, operation: str, payload: dict[str, Any], key: str) -> bool:
        if self.db.execute('SELECT 1 FROM local_operation WHERE project_scope=? AND operation=? AND idempotency_key=?', (project, operation, key)).fetchone():
            self.proposals.projects.mutate(project, operation, key,
                {k: v for k, v in payload.items() if k != 'capability'}, lambda: {})
            return True
        return False

    def failed(self, project: str, proposal: str) -> None:
        try:
            self.db.execute('BEGIN IMMEDIATE')
            self.proposals.transition(project, proposal, 'recovery_required')
            self.db.commit()
        except sqlite3.Error:
            self.db.rollback()  # Durable applying intent is reconciled on next startup.

    def apply(self, project: str, payload: dict[str, Any], key: str,
              checkpoint: Callable[[str], None] = lambda _: None) -> dict[str, Any]:
        proposal = payload['proposalId']
        if self.prior(project, 'proposals.apply', payload, key):
            return self.proposals.get(project, proposal)
        if project in self.proposals.busy or self.proposals.recovery_required(project):
            raise ScopeError('RECOVERY_REQUIRED')
        body = self.proposals.body(project, proposal)
        self.proposals.busy.add(project)
        accepted = False
        try:
            with self.proposals.locked(project, body) as (_, originals, conditions), ExitStack() as pending:
                row = self.proposals.row(project, proposal)
                if row[4] != 'approved' or fingerprint(conditions) != row[7]:
                    raise ScopeError('PRECONDITION_FAILED')
                journals: list[tuple[str, dict[str, Any]]] = []
                def accept() -> dict[str, Any]:
                    self.proposals.consume(project, proposal, payload['approvalId'], payload['capability'], 'proposal', row[6], row[7])
                    for original, change in zip(originals, body['changes']):
                        journal = str(uuid.uuid4())
                        names = {role + 'Name': RESERVED + uuid.uuid4().hex + '.' + role for role in ('old', 'new', 'restore', 'remove')}
                        data = {**names, 'path': change['path'], 'beforeBase64': change['beforeBase64'], 'afterBase64': change['afterBase64'],
                            'preimageHash': change['preimageHash'], 'targetHash': change['targetHash'], 'identity': original.file_identity,
                            'newIdentity': None, 'restoreIdentity': None, 'newFlushed': False, 'restoreFlushed': False,
                            'security': original.security, 'attributes': original.attributes}
                        self.db.execute('INSERT INTO apply_journal VALUES(?,?,?,?,?,?,?,\'prepared\',?)',
                            (journal, project, proposal, change['path'], change['preimageHash'], change['targetHash'],
                             self.proposals.encrypt(project, journal, data), json.dumps(names)))
                        journals.append((journal, data))
                    self.proposals.transition(project, proposal, 'applying')
                    return {'proposalId': proposal}
                self.proposals.projects.mutate(project, 'proposals.apply', key,
                    {k: v for k, v in payload.items() if k != 'capability'}, accept)
                accepted = True
                checkpoint('accepted')
                targets: list[LockedFile] = []
                for original, (journal, data) in zip(originals, journals):
                    target = pending.enter_context(LockedFile(original.path.parent / data['newName'], create=True,
                        security=original.security, attributes=original.attributes))
                    checkpoint('pending_created_before_record')
                    data['newIdentity'] = target.file_identity
                    self.save(project, journal, data, 'prepared')
                    target.write(base64.b64decode(data['afterBase64'], validate=True))
                    if target.digest() != data['targetHash'] or target.security != original.security:
                        raise ScopeError('PRECONDITION_FAILED')
                    data['newFlushed'] = True
                    self.save(project, journal, data, 'prepared')
                    targets.append(target)
                    checkpoint('pending_flushed')
                for original, target, (journal, data) in zip(originals, targets, journals):
                    original_path = original.path
                    original.rename(original_path.parent / data['oldName'])
                    checkpoint('original_moved_before_record')
                    self.save(project, journal, data, 'original_moved')
                    checkpoint('original_moved')
                    target.rename(original_path)
                    checkpoint('target_published_before_record')
                    self.save(project, journal, data, 'replaced')
                    if target.digest() != data['targetHash']:
                        raise ScopeError('PRECONDITION_FAILED')
                    self.save(project, journal, data, 'verified')
                    checkpoint('target_verified')
                for original in originals:
                    original.delete_owned()
                    original.close()
                    checkpoint('original_cleanup')
                self.db.execute('BEGIN IMMEDIATE')
                self.proposals.transition(project, proposal, 'applied')
                self.db.commit()
                checkpoint('completed')
        except Exception:
            if accepted:
                self.failed(project, proposal)
                raise ScopeError('RECOVERY_REQUIRED') from None
            raise
        finally:
            self.proposals.busy.discard(project)
        return self.proposals.get(project, proposal)

    def journal_data(self, project: str, proposal: str) -> list[tuple[str, dict[str, Any]]]:
        result: list[tuple[str, dict[str, Any]]] = []
        for journal, blob in self.db.execute('SELECT id,backup FROM apply_journal WHERE project_id=? AND proposal_id=? ORDER BY relative_path', (project, proposal)):
            data = self.proposals.decrypt(project, journal, blob)
            writable_path(data['path'])
            for role in ('old', 'new', 'restore', 'remove'):
                if not re.fullmatch(r'\.gitviz-workspace-[0-9a-f]{32}\.' + role, data[role + 'Name']):
                    raise ScopeError('RECOVERY_REQUIRED')
            if (image_hash(base64.b64decode(data['beforeBase64'], validate=True)) != data['preimageHash']
                    or image_hash(base64.b64decode(data['afterBase64'], validate=True)) != data['targetHash']):
                raise ScopeError('RECOVERY_REQUIRED')
            result.append((journal, data))
        return result

    def inspect_roles(self, root: Path, data: dict[str, Any], stack: ExitStack) -> dict[str, LockedFile]:
        path = root.joinpath(*data['path'].split('/'))
        files: dict[str, LockedFile] = {}
        for role in ('target', 'old', 'new', 'restore', 'remove'):
            current = path if role == 'target' else path.parent / data[role + 'Name']
            if not current.exists():
                continue
            file = stack.enter_context(LockedFile(current))
            digest = file.digest()
            if file.security != data['security']:
                raise ScopeError('RECOVERY_CONFLICT')
            if role == 'target':
                okay = ((file.file_identity in (data['identity'], data['restoreIdentity']) and digest == data['preimageHash'])
                    or (file.file_identity == data['newIdentity'] and digest == data['targetHash']))
            else:
                expected = data['identity'] if role == 'old' else data['restoreIdentity'] if role == 'restore' else data['newIdentity']
                permitted: set[str] = {data['preimageHash']} if role in ('old', 'restore') else {data['targetHash']}
                if (role == 'restore' and not data['restoreFlushed']) or (role == 'new' and not data['newFlushed']):
                    permitted.add(hashlib.sha256(b'').hexdigest())
                okay = file.file_identity == expected and digest in permitted
            if not okay:
                raise ScopeError('RECOVERY_CONFLICT')
            files[role] = file
        if 'target' not in files and not any(role in files and files[role].digest() == data['preimageHash'] for role in ('old', 'restore')):
            raise ScopeError('RECOVERY_CONFLICT')
        return files

    def recovery_plan(self, project: str, proposal: str) -> dict[str, Any]:
        row = self.proposals.row(project, proposal)
        if row[4] != 'recovery_required':
            raise ScopeError('PRECONDITION_FAILED')
        records = self.journal_data(project, proposal)
        grant = self.proposals.projects.grant(project)
        with write_scope(grant, [data['path'] for _, data in records]), ExitStack() as stack:
            files: list[dict[str, Any]] = []
            for journal, data in records:
                roles = self.inspect_roles(Path(grant['root']), data, stack)
                files.append({'journalId': journal, 'path': data['path'], 'expectedCurrentHashes': {
                    role: {'identity': file.file_identity, 'hash': file.digest()} for role, file in roles.items()},
                    'restoreHash': data['preimageHash']})
            plan = {'projectId': project, 'proposalId': proposal, 'version': row[3], 'files': files,
                'indexHash': index_hash(grant), 'rootIdentity': grant['rootIdentity']}
            return {**plan, 'recoveryHash': fingerprint(plan)}

    def approve_recovery(self, project: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
        plan = self.recovery_plan(project, payload['proposalId'])
        if plan['recoveryHash'] != payload['recoveryHash']:
            raise ScopeError('PRECONDITION_FAILED')
        def action() -> dict[str, Any]:
            approval, now = str(uuid.uuid4()), time.time()
            self.db.execute('INSERT INTO approval VALUES(?,?,?,\'recovery\',?,?,?,?,?,NULL,NULL)',
                (approval, project, payload['proposalId'], plan['recoveryHash'], plan['recoveryHash'],
                 hashlib.sha256(payload['capability'].encode()).hexdigest(), now, now + 300))
            return {'approvalId': approval}
        result = self.proposals.projects.mutate(project, 'proposals.approveRecovery', key,
            {k: v for k, v in payload.items() if k != 'capability'}, action)
        return {'projectId': project, 'proposalId': payload['proposalId'], **result}

    def recover(self, project: str, payload: dict[str, Any], key: str,
                checkpoint: Callable[[str], None] = lambda _: None) -> dict[str, Any]:
        proposal = payload['proposalId']
        if self.prior(project, 'proposals.recover', payload, key):
            return self.proposals.get(project, proposal)
        if project in self.proposals.busy:
            raise ScopeError('BUSY')
        plan = self.recovery_plan(project, proposal)
        if plan['recoveryHash'] != payload['recoveryHash']:
            raise ScopeError('PRECONDITION_FAILED')
        records = self.journal_data(project, proposal)
        grant = self.proposals.projects.grant(project)
        self.proposals.busy.add(project)
        accepted = False
        try:
            with write_scope(grant, [data['path'] for _, data in records]), ExitStack() as stack:
                role_sets = [self.inspect_roles(Path(grant['root']), data, stack) for _, data in records]
                current_files = [{'journalId': journal, 'path': data['path'], 'expectedCurrentHashes': {
                    role: {'identity': file.file_identity, 'hash': file.digest()} for role, file in roles.items()}, 'restoreHash': data['preimageHash']}
                    for (journal, data), roles in zip(records, role_sets)]
                if fingerprint({**{k: v for k, v in plan.items() if k != 'recoveryHash'}, 'files': current_files,
                        'indexHash': index_hash(grant)}) != plan['recoveryHash']:
                    raise ScopeError('PRECONDITION_FAILED')
                def accept() -> dict[str, Any]:
                    self.proposals.consume(project, proposal, payload['approvalId'], payload['capability'], 'recovery', plan['recoveryHash'], plan['recoveryHash'])
                    self.proposals.transition(project, proposal, 'applying')
                    return {'proposalId': proposal}
                self.proposals.projects.mutate(project, 'proposals.recover', key,
                    {k: v for k, v in payload.items() if k != 'capability'}, accept)
                accepted = True
                checkpoint('recovery_accepted')
                for (journal, data), roles in zip(records, role_sets):
                    original_path = Path(grant['root']).joinpath(*data['path'].split('/'))
                    current = roles.get('target')
                    if current is None or current.digest() != data['preimageHash']:
                        source = next((roles[role] for role in ('old', 'restore') if role in roles and roles[role].digest() == data['preimageHash']), None)
                        if source is None:
                            source = stack.enter_context(LockedFile(original_path.parent / data['restoreName'], create=True,
                                security=data['security'], attributes=data['attributes']))
                            data['restoreIdentity'] = source.file_identity
                            self.save(project, journal, data, 'recovery_required')
                            source.write(base64.b64decode(data['beforeBase64'], validate=True))
                            data['restoreFlushed'] = True
                            self.save(project, journal, data, 'recovery_required')
                            roles['restore'] = source
                        if current:
                            current.rename(original_path.parent / data['removeName'])
                            checkpoint('recovery_target_moved')
                        source.rename(original_path)
                        checkpoint('recovery_original_published')
                        if source.digest() != data['preimageHash']:
                            raise ScopeError('RECOVERY_CONFLICT')
                    for file in roles.values():
                        if file.path.name.startswith(RESERVED):
                            file.delete_owned()
                            file.close()
                    self.save(project, journal, data, 'restored')
                    checkpoint('recovery_file_restored')
                self.db.execute('BEGIN IMMEDIATE')
                self.proposals.transition(project, proposal, 'restored')
                self.db.commit()
        except Exception:
            if accepted:
                self.failed(project, proposal)
                raise ScopeError('RECOVERY_REQUIRED') from None
            raise
        finally:
            self.proposals.busy.discard(project)
        return self.proposals.get(project, proposal)
