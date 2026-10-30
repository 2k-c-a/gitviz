from __future__ import annotations

import subprocess
import sys
import time
import json
from pathlib import Path

from app.workspace.apply import Application
from app.workspace.projects import Projects
from app.workspace.proposals import Proposals
from app.workspace.review import Reviews
from app.workspace.scoped_files import ScopeError
from app.workspace.snapshots import Snapshots
from app.workspace.store import LocalStore
from tests.workspace.test_apply import ApplyFixture, uid


class ApplyKillTests(ApplyFixture):
    def test_actual_kill_durable_publish_boundaries_and_explicit_recovery(self) -> None:
        phases = ['accepted', 'pending_created_before_record', 'pending_flushed', 'original_moved_before_record',
            'original_moved', 'target_published_before_record', 'target_verified', 'original_cleanup', 'completed']
        evidence = Path(__file__).resolve().parents[2] / '.validation/p06'
        evidence.mkdir(parents=True, exist_ok=True)
        results: list[dict[str, object]] = []
        for phase in phases:
            with self.subTest(phase=phase):
                proposal = self.pending()
                project, proposal_id = self.project_id, proposal['proposalId']
                transport = self.reviews.jobs.transport
                self.reviews.jobs.close()
                self.store.close()
                signal = self.root / ('checkpoint-' + phase)
                key = uid()
                command = [getattr(sys, '_base_executable', sys.executable), '-X', 'utf8', '-B',
                    str(Path(__file__).with_name('fixture_apply_kill.py')), '--data', str(self.root / 'data'),
                    '--project', project, '--proposal', proposal_id, '--phase', phase, '--signal', str(signal), '--operation-key', key]
                child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    deadline = time.monotonic() + 15
                    while not signal.exists() and child.poll() is None and time.monotonic() < deadline:
                        time.sleep(.01)
                finally:
                    if child.poll() is None:
                        child.kill()
                    _, diagnostics = child.communicate(timeout=5)
                    (evidence / ('kill-' + phase + '.stderr.log')).write_bytes(diagnostics[:65536])
                after_kill = self.hashes()
                self.store = LocalStore(self.root / 'data', lambda _: None)
                self.store.open()
                assert self.store.connection is not None
                self.db = self.store.connection
                self.projects = Projects(self.db)
                self.snapshots = Snapshots(self.projects, self.cipher)
                self.reviews = Reviews(self.snapshots, transport)
                self.proposals = Proposals(self.reviews)
                self.application = Application(self.proposals)
                self.assertTrue(signal.exists(), 'fixture checkpoint missing: ' + phase)
                self.assertEqual(self.hashes(), after_kill, 'startup wrote source automatically')
                state = self.proposals.get(project, proposal_id)
                results.append({'phase': phase, 'exitCode': child.returncode, 'statusAfterRestart': state['status'], 'startupSourceUnchanged': self.hashes() == after_kill})
                (evidence / 'apply-kill-results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
                approval_id = self.db.execute('SELECT id FROM approval WHERE proposal_id=? AND target_type=\'proposal\' ORDER BY approved_at DESC LIMIT 1', (proposal_id,)).fetchone()[0]
                replay = {'proposalId': proposal_id, 'approvalId': approval_id, 'capability': 'e' * 64}
                self.application.apply(project, replay, key)
                self.assertEqual(self.hashes(), after_kill, 'same-key replay wrote source')
                if phase == 'completed':
                    self.assertEqual(state['status'], 'applied')
                    (self.repo / 'app.py').write_bytes(b'x = 1\r\nprint(x)\r\n')
                    continue
                self.assertEqual(state['status'], 'recovery_required')
                if phase == 'pending_created_before_record':
                    # Ownership was not durably recorded: the product must refuse
                    # deletion. The fixture explicitly simulates human inspection
                    # and removal of its own known empty file before retrying.
                    with self.assertRaises(ScopeError):
                        self.application.recovery_plan(project, proposal_id)
                    data = self.application.journal_data(project, proposal_id)[0][1]
                    path = (self.repo / data['path']).parent / data['newName']
                    self.assertEqual(path.read_bytes(), b'')
                    path.unlink()
                plan = self.application.recovery_plan(project, proposal_id)
                approved = self.application.approve_recovery(project, {'proposalId': proposal_id,
                    'recoveryHash': plan['recoveryHash'], 'capability': 'f' * 64}, uid())
                restored = self.application.recover(project, {'proposalId': proposal_id, 'approvalId': approved['approvalId'],
                    'recoveryHash': plan['recoveryHash'], 'capability': 'f' * 64}, uid())
                self.assertEqual(restored['status'], 'restored')
                self.assertEqual((self.repo / 'app.py').read_bytes(), b'x = 1\r\nprint(x)\r\n')
                self.assertEqual(self.db.execute('PRAGMA foreign_key_check').fetchall(), [])
