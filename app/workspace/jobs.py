"""Single-writer durable attempts. Worker threads perform transport only, never SQL."""
from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from typing import Any

from . import events
from .review_models import validate_output
from .refactor_models import validate_refactor
from .review_transport import Reply, Transport, TransportFailure, request_body
from .scoped_files import ScopeError
from .snapshots import Snapshots, image_hash


Completion = tuple[str, dict[str, Any], Reply | None, str | None, bool]


class Jobs:
    def __init__(self, snapshots: Snapshots, transport: Transport):
        self.snapshots = snapshots
        self.db = snapshots.db
        self.transport = transport
        self.active: tuple[str, threading.Event] | None = None
        self.completed: queue.Queue[Completion] = queue.Queue(maxsize=1)
        self.pending_completion: Completion | None = None
        self.recover()

    @staticmethod
    def aad(project: str, resource: str) -> bytes:
        return f'GitVizWorkspace/project/{project}/review/{resource}/schema-4'.encode()

    def encrypt(self, project: str, resource: str, value: Any) -> bytes:
        return self.snapshots.cipher.encrypt(json.dumps(value, ensure_ascii=False).encode(), self.aad(project, resource))

    def decrypt(self, project: str, resource: str, value: bytes) -> Any:
        return json.loads(self.snapshots.cipher.decrypt(value, self.aad(project, resource)))

    def recover(self) -> None:
        # Durable intent precedes transport. A crash in that gap is conservatively uncertain.
        now = time.time()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            affected = self.db.execute("SELECT id FROM review_run WHERE status IN ('running','cancel_requested','queued')").fetchall()
            self.db.execute("UPDATE review_run SET status='uncertain',outcome_uncertain=1,error_code='INTERRUPTED_UNCERTAIN',version=version+1,updated_at=? WHERE status IN ('running','cancel_requested')", (now,))
            self.db.execute("UPDATE review_attempt SET status='uncertain',finished_at=? WHERE status='running'", (now,))
            self.db.execute("UPDATE local_usage_ledger SET outcome_uncertain=1 WHERE run_id IN (SELECT id FROM review_run WHERE status='uncertain')")
            self.db.execute("UPDATE review_run SET status='cancelled',error_code='INTERRUPTED_BEFORE_SEND',version=version+1,updated_at=? WHERE status='queued'", (now,))
            self.db.execute("UPDATE local_usage_ledger SET actual_cost='0',settled_at=? WHERE settled_at IS NULL AND run_id IN (SELECT id FROM review_run WHERE status='cancelled' AND attempt_count=0)", (now,))
            for (run,) in affected:
                self.event(run, None)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def launch(self, project: str, run: str, manifest: dict[str, Any]) -> None:
        self.drain()
        if self.active is not None:
            raise ScopeError('BUSY')
        cancelled = threading.Event()
        now = time.time()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            changed = self.db.execute("UPDATE review_run SET status='running',attempt_count=1,version=version+1,updated_at=? WHERE id=? AND project_id=? AND status='queued'", (now, run, project))
            if changed.rowcount != 1:
                self.db.rollback()
                return
            self.db.execute('INSERT INTO review_attempt VALUES(?,?,1,\'running\',NULL,NULL,?,?,NULL)', (str(uuid.uuid4()), run, manifest['maxEstimatedCost'], now))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        self.active = (run, cancelled)

        def worker() -> None:
            reply: Reply | None = None
            error: str | None = None
            uncertain = False
            output: dict[str, Any] = {}
            try:
                if image_hash(request_body(manifest['profile'], manifest['system'], manifest['user'])) != manifest['requestHash']:
                    raise ScopeError('PRECONDITION_FAILED')
                reply = self.transport(manifest['profile'], manifest['system'], manifest['user'], cancelled)
                output = (validate_refactor(reply.raw, manifest['files']) if manifest['kind'] == 'refactor'
                          else validate_output(reply.raw, manifest['files']))
            except TransportFailure as failure:
                error, uncertain = failure.code, failure.uncertain
            except ScopeError as failure:
                error = failure.code  # Complete response received, never retry automatically.
            except Exception:
                error, uncertain = 'PROVIDER_ERROR', True
            self.completed.put((run, output, reply, error, uncertain))

        thread = threading.Thread(target=worker, name='workspace-review-transport', daemon=True)
        try:
            thread.start()
        except RuntimeError:
            self.active = None
            self.recover()
            raise ScopeError('PROVIDER_UNAVAILABLE') from None

    def drain(self) -> None:
        if self.pending_completion is None:
            try:
                self.pending_completion = self.completed.get_nowait()
            except queue.Empty:
                return
        run, output, reply, error, uncertain = self.pending_completion
        row = self.db.execute('SELECT project_id,snapshot_id,status FROM review_run WHERE id=?', (run,)).fetchone()
        if row is None:
            raise ScopeError('RECOVERY_REQUIRED')
        project, snapshot, prior_status = row
        status = 'uncertain' if uncertain else 'failed' if error else 'succeeded'
        if prior_status == 'cancel_requested' and not uncertain:
            status, error = 'cancelled', 'CANCELLED_RESPONSE_DISCARDED'
        now = time.time()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            result = self.encrypt(project, run, output) if status == 'succeeded' else None
            self.db.execute('UPDATE review_run SET status=?,outcome_uncertain=?,result=?,error_code=?,version=version+1,updated_at=? WHERE id=?',
                (status, int(uncertain), result, error, now, run))
            self.db.execute('UPDATE review_attempt SET status=?,provider_request_id=?,usage=?,finished_at=? WHERE run_id=?',
                (status, reply.request_id if reply else None, json.dumps(reply.usage) if reply and reply.usage else None, now, run))
            self.db.execute('UPDATE local_usage_ledger SET outcome_uncertain=?,actual_cost=?,settled_at=? WHERE run_id=?',
                (int(uncertain), None if uncertain else '0', None if uncertain else now, run))
            if status == 'succeeded' and 'findings' in output:
                for ordinal, finding in enumerate(output['findings']):
                    finding_id = str(uuid.uuid4())
                    self.db.execute('INSERT INTO review_finding VALUES(?,?,?,?,?,?,?)',
                        (finding_id, project, run, snapshot, finding['evidence'][0]['fileId'], ordinal, self.encrypt(project, finding_id, finding)))
                    self.db.execute('INSERT INTO review_feedback VALUES(?,\'unverified\',?,1,?)',
                        (finding_id, self.encrypt(project, finding_id + '/feedback', ''), now))
            self.event(run, output if status == 'succeeded' else None)
            self.db.commit()
        except Exception:
            self.db.rollback()
            # Retain pending_completion until commit, including lock/disk failures before BEGIN.
            raise
        self.pending_completion = None
        self.active = None

    def event(self, run: str, output: dict[str, Any] | None) -> None:
        """Record the terminal transition just written in the caller's transaction."""
        row = self.db.execute('SELECT project_id,status,version,kind,error_code,updated_at FROM review_run WHERE id=?', (run,)).fetchone()
        events.record(self.db, project=row[0], source_type='review', source_id=run, revision=row[2], status=row[1],
                      summary=events.review_summary(row[3], output), cause=row[4], occurred_at=row[5])

    def get(self, project: str, run: str) -> dict[str, Any]:
        self.drain()
        row = self.db.execute('SELECT snapshot_id,status,attempt_count,budget_reserved,outcome_uncertain,error_code,version,created_at,kind FROM review_run WHERE project_id=? AND id=?', (project, run)).fetchone()
        if row is None:
            raise ScopeError('RESOURCE_NOT_FOUND')
        result: dict[str, Any] = {'projectId': project, 'runId': run, 'snapshotId': row[0], 'status': row[1],
            'attemptCount': row[2], 'budgetReserved': row[3], 'outcomeUncertain': bool(row[4]), 'errorCode': row[5],
            'version': row[6], 'createdAt': row[7], 'kind': row[8], 'refactor': None, 'findings': [], 'limitations': [
                'model-inference-not-executed', 'zero-findings-is-not-defect-free', 'retention-policy-pending',
                'pc-model-provider-fee-zero-system-resource-cost-not-zero']}
        encrypted = self.db.execute('SELECT result FROM review_run WHERE id=?', (run,)).fetchone()[0]
        if encrypted is not None:
            output = self.decrypt(project, run, encrypted)
            if row[8] == 'refactor':
                result['refactor'] = output
            result['limitations'] += output['limitations']
        for finding_id, payload, verdict, note, version in self.db.execute(
            'SELECT f.id,f.payload,b.verdict,b.note,b.version FROM review_finding f JOIN review_feedback b ON b.finding_id=f.id WHERE f.project_id=? AND f.review_run_id=? ORDER BY f.ordinal', (project, run)):
            finding = self.decrypt(project, finding_id, payload)
            finding.update({'findingId': finding_id, 'feedback': {'verdict': verdict,
                'note': self.decrypt(project, finding_id + '/feedback', note), 'version': version}})
            result['findings'].append(finding)
        return result

    def cancel(self, project: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
        self.drain()
        run = payload['runId']
        def action() -> dict[str, Any]:
            row = self.db.execute('SELECT version,status FROM review_run WHERE project_id=? AND id=?', (project, run)).fetchone()
            if row is None:
                raise ScopeError('RESOURCE_NOT_FOUND')
            if row[0] != payload['expectedVersion']:
                raise ScopeError('CONFLICT')
            if row[1] in ('running', 'queued'):
                self.db.execute('UPDATE review_run SET status=?,version=version+1,updated_at=? WHERE id=?',
                    ('cancel_requested' if row[1] == 'running' else 'cancelled', time.time(), run))
            return {'runId': run}
        self.snapshots.projects.mutate(project, 'reviews.cancel', key, payload, action)
        if self.active and self.active[0] == run:
            self.active[1].set()
        return self.get(project, run)

    def close(self) -> None:
        if self.active:
            self.active[1].set()
        self.drain()
        self.recover()
