from __future__ import annotations

import time
import uuid
from typing import Any

from app.workspace.contracts import validate
from app.workspace.impact import Impact
from app.workspace.proposals import Proposals
from app.workspace.remote_connections import RemoteConnections
from app.workspace.review import Reviews
from app.workspace.scoped_files import ScopeError
from tests.workspace.support import WorkspaceFixture


class HistoricalImpactTests(WorkspaceFixture):
    def test_encrypted_history_binding_and_immutable_snapshot_reject_cross_scope_or_changed_head(self) -> None:
        self.git('remote', 'add', 'origin', 'https://github.com/o/r')
        remote = RemoteConnections(self.projects)
        inspected = remote.inspect(self.project_id)
        remote.connect(self.project_id, {'phase': 'connect', 'accountId': '11', 'serverOrigin': 'https://fixture.invalid', 'repositoryId': 2,
            'githubId': 123, 'canonicalUrl': 'https://github.com/o/r', 'remoteName': 'origin', 'configHash': inspected['configHash'], 'expectedVersion': inspected['projectVersion']}, str(uuid.uuid4()))
        reviews = Reviews(self.snapshots)
        service = Impact(Proposals(reviews))
        try:
            snapshot = self.capture([{'facet': 'selected', 'path': 'app.py'}])
            prepared = service.prepare(self.project_id, {'snapshotId': snapshot['snapshotId'], 'proposalId': None})
            result = service.finish(self.project_id, {'sessionId': prepared['sessionId'], 'limitations': []}, str(uuid.uuid4()))
            analysis = result['analysisId']
            connection = remote.inspect(self.project_id)['connection']
            proof = {name: connection[name] for name in ('accountId', 'serverOrigin', 'repositoryId', 'githubId', 'canonicalUrl', 'connectionId', 'configHash')}
            proof.update(visibility='private', expiresAt=time.time() + 30)
            history: dict[str, Any] = {'status': 'available', 'reason': 'historical-cochange-not-a-dependency', 'repositoryId': 2, 'serverSnapshotId': 7,
                'targetSha': snapshot['head'], 'payloadSha256': 'c' * 64, 'formulaVersion': 'fixture-v1', 'windowStart': None, 'windowEnd': '2026-10-28T00:00:00Z',
                'coChangeUnit': 'pr', 'totalUnits': 10, 'excludedUnits': 1, 'totalPairs': 1, 'truncated': False, 'pairs': [{'pathA': 'app.py', 'pathB': 'related.py',
                    'support': 3, 'changesA': 5, 'changesB': 6, 'ratioAtoB': .6, 'ratioBtoA': .5, 'score': .6, 'lowSupport': True}]}
            payload = {'phase': 'attach', 'analysisId': analysis, 'proof': proof, 'historical': history}
            validate('request', {'protocolVersion': 1, 'requestId': str(uuid.uuid4()), 'operation': 'workspace.impact.historical', 'projectId': self.project_id, 'idempotencyKey': str(uuid.uuid4()), 'payload': payload})
            for changed in ({'targetSha': 'd' * 40}, {'repositoryId': 3}):
                with self.assertRaises(ScopeError):
                    service.historical(self.project_id, payload | {'historical': history | changed}, str(uuid.uuid4()))
            with self.assertRaises(ScopeError):
                service.historical(self.project_id, payload | {'proof': proof | {'accountId': 'other'}}, str(uuid.uuid4()))
            attached = service.historical(self.project_id, payload, str(uuid.uuid4()))
            validate('response', {'protocolVersion': 1, 'requestId': str(uuid.uuid4()), 'ok': True, 'result': attached})
            self.assertEqual(attached['historical'], history)
            self.assertNotIn('_historicalBinding', attached)
            context = service.historical(self.project_id, {'phase': 'inspect', 'analysisId': analysis}, str(uuid.uuid4()))
            self.assertEqual(context['binding']['accountId'], '11')
            raw = self.db.execute('SELECT payload FROM impact_analysis WHERE id=?', (analysis,)).fetchone()[0]
            self.assertNotIn(b'related.py', raw)
            with self.assertRaises(ScopeError):
                service.historical(self.project_id, payload | {'historical': history | {'serverSnapshotId': 8}}, str(uuid.uuid4()))
        finally:
            reviews.jobs.close()
