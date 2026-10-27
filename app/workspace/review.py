"""Snapshot-bound preview/consent and local review API, isolated from server jobs."""
from __future__ import annotations

import base64
import json
import math
import sys
import time
import uuid
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from .adapters import ai_core
from .jobs import Jobs
from .review_transport import Transport, pc_ollama, request_body
from .refactor_models import output_schema
from .patches import MAX_FILES, writable_path, validate_encoding
from .scoped_files import ScopeError
from .snapshots import Snapshots, fingerprint, image_hash


class Reviews:
    def __init__(self, snapshots: Snapshots, transport: Transport = pc_ollama):
        self.snapshots = snapshots
        self.db = snapshots.db
        self.jobs = Jobs(snapshots, transport)
        self.previews: dict[str, tuple[float, dict[str, Any]]] = {}

    def manifest(self, project: str, payload: dict[str, Any]) -> dict[str, Any]:
        kind = payload['kind']
        if kind not in ('review', 'refactor'):
            raise ScopeError('UNSUPPORTED_KIND')
        # Local projects currently have no authenticated server-public mapping proof.
        # Neither a Git remote URL nor renderer-provided visibility can authorize cloud.
        if payload['profileId'] != 'ollama-local-v1':
            raise ScopeError('CLOUD_MAPPING_REQUIRED')
        purpose = payload['purpose']
        if ai_core.policy.content_exclusion(purpose, byte_count=len(purpose.encode())) is not None:
            raise ScopeError('CONTENT_EXCLUDED')
        snapshot = self.snapshots.get(project, payload['snapshotId'])
        files: list[dict[str, Any]] = []
        for file in snapshot['files']:
            if file['state'] != 'read':
                continue
            row = self.db.execute('SELECT payload FROM change_file WHERE project_id=? AND snapshot_id=? AND id=?',
                (project, snapshot['snapshotId'], file['fileId'])).fetchone()
            if row is None:
                raise ScopeError('RESOURCE_NOT_FOUND')
            body = json.loads(self.snapshots.cipher.decrypt(row[0], self.snapshots.aad(project, snapshot['snapshotId'], file['fileId'])))
            before = base64.b64decode(body['before'], validate=True) if body['before'] is not None else None
            after = base64.b64decode(body['after'], validate=True) if body['after'] is not None else None
            if image_hash(before) != file['beforeHash'] or image_hash(after) != file['afterHash']:
                raise ScopeError('PRECONDITION_FAILED')
            if ai_core.exclusion(file['path'], before, after, file['oldPath']):
                raise ScopeError('CONTENT_EXCLUDED')
            if kind == 'refactor':
                if after is None:
                    raise ScopeError('PATCH_SCOPE_UNSUPPORTED')
                writable_path(file['path'])
                validate_encoding(file['path'], after)
                if any(f['path'].casefold() == file['path'].casefold() for f in files):
                    raise ScopeError('AMBIGUOUS_FACETS')
                files.append({'fileId': file['fileId'], 'facet': file['facet'], 'path': file['path'],
                    'preimageHash': file['afterHash'], 'source': after.decode('utf-8')})
            elif body['lines']:
                files.append({'fileId': file['fileId'], 'facet': file['facet'], 'path': file['path'], 'lines': body['lines']})
        if not files:
            raise ScopeError('EMPTY_INPUT')
        if kind == 'refactor' and len(files) > MAX_FILES:
            raise ScopeError('PATCH_TOO_LARGE')
        base = ai_core.profile('ollama-local-v1', {})
        prompt_root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2]))
        schema_version = 'workspace-refactor-v1' if kind == 'refactor' else 'workspace-review-v1'
        system = (prompt_root / f'app/services/ai/prompts/{schema_version.replace("-", "_")}.txt').read_text(encoding='utf-8')
        profile = {'id': base.id, 'provider': base.provider, 'model': base.model,
            'endpoint': 'http://127.0.0.1:11434', 'executionLocation': 'user-pc', 'timeoutSeconds': base.timeout_seconds,
            'maxInputTokens': base.max_input_tokens, 'maxOutputTokens': base.max_output_tokens,
            'contextWindow': base.context_window, 'promptVersion': schema_version, 'schemaVersion': schema_version,
            'outputSchemaHash': fingerprint(output_schema(schema_version)), 'transportVersion': 'ollama-chat-v1'}
        profile_hash = fingerprint({'base': {k: str(v) if isinstance(v, Decimal) else v for k, v in asdict(base).items()},
            'workspace': profile, 'systemHash': fingerprint(system)})
        user = json.dumps({'purpose': purpose, 'files': files}, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
        # Reuse the same conservative prompt estimator and context guard as server core.
        from app.services.ai.token_budget import estimate_prompt_tokens, check_input_limit
        from app.services.ai.providers.base import TokenLimitExceeded
        estimated = estimate_prompt_tokens(base, system, user)
        wire = request_body(profile, system, user)
        estimated = max(estimated, math.ceil(len(wire) / base.bytes_per_token) + 50)
        try:
            check_input_limit(base, estimated)
        except TokenLimitExceeded:
            raise ScopeError('INPUT_TOO_LARGE') from None
        manifest: dict[str, Any] = {'projectId': project, 'snapshotId': snapshot['snapshotId'], 'inputHash': snapshot['inputHash'],
            'kind': kind, 'purposeHash': fingerprint(purpose), 'profile': profile, 'profileFingerprint': profile_hash,
            'destination': profile['endpoint'], 'executionLocation': 'user-pc', 'credentialRef': None, 'credentialVersion': None,
            'policyVersion': ai_core.POLICY_VERSION, 'maxEstimatedCost': '0', 'currency': 'USD', 'costIsEstimate': True,
            'estimatedInputTokens': estimated, 'coverage': snapshot['coverage'], 'excluded': [f for f in snapshot['files'] if f['state'] != 'read'],
            'system': system, 'user': user, 'files': files, 'requestHash': image_hash(wire), 'requestBytes': len(wire),
            'limitations': ['content-secret-detection-is-heuristic', 'model-inference-not-executed', 'retention-policy-pending']}
        manifest['manifestHash'] = fingerprint(manifest)
        return manifest

    def preview(self, project: str, payload: dict[str, Any], *, drain: bool = True) -> dict[str, Any]:
        if drain:
            self.jobs.drain()
        now = time.time()
        self.previews = {key: value for key, value in self.previews.items() if value[0] > now}
        if len(self.previews) >= 32:
            raise ScopeError('BUSY')
        manifest = self.manifest(project, payload)
        preview_id = str(uuid.uuid4())
        value: dict[str, Any] = {**manifest, 'previewId': preview_id, 'expiresAt': now + 300,
            'generation': payload['generation'], 'purpose': payload['purpose'], 'revisionOf': None}
        value['previewHash'] = fingerprint(value)
        self.previews[preview_id] = (value['expiresAt'], value)
        # Copy: in-process callers cannot mutate the authorized cache via a returned dict.
        return json.loads(json.dumps(value))

    def start(self, project: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
        self.jobs.drain()
        def action() -> dict[str, Any]:
            saved = self.previews.get(payload['previewId'])
            if saved is None or saved[0] <= time.time():
                raise ScopeError('CONSENT_EXPIRED')
            approved = saved[1]
            if approved['projectId'] != project:
                raise ScopeError('RESOURCE_NOT_FOUND')
            for name in ('previewHash', 'manifestHash', 'maxEstimatedCost'):
                if payload[name] != approved[name]:
                    raise ScopeError('PRECONDITION_FAILED')
            if payload['acknowledged'] is not True:
                raise ScopeError('CONSENT_REQUIRED')
            current = self.manifest(project, {'snapshotId': approved['snapshotId'], 'kind': approved['kind'],
                'purpose': approved['purpose'], 'profileId': approved['profile']['id']})
            if current['manifestHash'] != approved['manifestHash']:
                raise ScopeError('PRECONDITION_FAILED')
            identity = fingerprint({k: approved[k] for k in ('projectId', 'inputHash', 'kind', 'purposeHash', 'profileFingerprint', 'policyVersion')})
            generation = approved['generation']
            existing = self.db.execute('SELECT id,status FROM review_run WHERE fingerprint=? AND generation=?', (identity, generation)).fetchone()
            if existing:
                return {'runId': existing[0]}  # Another operation key still cannot re-charge this generation.
            previous = self.db.execute('SELECT generation,status FROM review_run WHERE fingerprint=? ORDER BY generation DESC LIMIT 1', (identity,)).fetchone()
            if generation != (previous[0] + 1 if previous else 1):
                raise ScopeError('CONFLICT')
            if previous and (previous[1] in ('queued', 'running', 'cancel_requested') or not payload['duplicateRiskAcknowledged']):
                raise ScopeError('DUPLICATE_RISK_CONFIRMATION_REQUIRED')
            if self.jobs.active is not None:
                raise ScopeError('BUSY')
            run, consent = str(uuid.uuid4()), str(uuid.uuid4())
            now = time.time()
            self.db.execute('INSERT INTO transfer_consent VALUES(?,?,?,?,?,?,?,?,?)',
                (consent, project, approved['snapshotId'], approved['manifestHash'], approved['previewHash'], approved['profileFingerprint'],
                 self.jobs.encrypt(project, consent, approved), now, approved['expiresAt']))
            self.db.execute('INSERT INTO review_run VALUES(?,?,?,?,?,?,?,\'queued\',0,?,0,NULL,NULL,1,?,?)',
                (run, project, approved['snapshotId'], consent, approved['kind'], identity, generation, approved['maxEstimatedCost'], now, now))
            # Reservation and job/operation acceptance share this exact transaction.
            self.db.execute('INSERT INTO local_usage_ledger VALUES(?,?,1,?,?,?,NULL,1,0,NULL)',
                (str(uuid.uuid4()), run, approved['profile']['provider'], time.strftime('%Y-%m-%d', time.gmtime(now)), approved['maxEstimatedCost']))
            return {'runId': run}
        result = self.snapshots.projects.mutate(project, 'reviews.start', key, payload, action)
        row = self.db.execute('SELECT status,consent_id FROM review_run WHERE id=? AND project_id=?', (result['runId'], project)).fetchone()
        if row[0] == 'queued':
            encrypted = self.db.execute('SELECT payload FROM transfer_consent WHERE id=?', (row[1],)).fetchone()[0]
            self.jobs.launch(project, result['runId'], self.jobs.decrypt(project, row[1], encrypted))
        return self.jobs.get(project, result['runId'])

    def list(self, project: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.jobs.drain()
        rows = self.db.execute('SELECT id,snapshot_id,status,created_at,version FROM review_run WHERE project_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?',
            (project, payload['limit'], payload['offset'])).fetchall()
        return {'projectId': project, 'runs': [{'runId': r[0], 'snapshotId': r[1], 'status': r[2], 'createdAt': r[3], 'version': r[4]} for r in rows]}

    def feedback(self, project: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
        def action() -> dict[str, Any]:
            row = self.db.execute('SELECT review_run_id FROM review_finding WHERE project_id=? AND id=?', (project, payload['findingId'])).fetchone()
            if row is None:
                raise ScopeError('RESOURCE_NOT_FOUND')
            if ai_core.policy.content_exclusion(payload['note'], byte_count=len(payload['note'].encode())) is not None:
                raise ScopeError('CONTENT_EXCLUDED')
            changed = self.db.execute('UPDATE review_feedback SET verdict=?,note=?,version=version+1,updated_at=? WHERE finding_id=? AND version=?',
                (payload['verdict'], self.jobs.encrypt(project, payload['findingId'] + '/feedback', payload['note']), time.time(), payload['findingId'], payload['expectedVersion']))
            if changed.rowcount != 1:
                raise ScopeError('CONFLICT')
            return {'runId': row[0]}
        result = self.snapshots.projects.mutate(project, 'reviews.feedback', key, payload, action)
        return self.jobs.get(project, result['runId'])
