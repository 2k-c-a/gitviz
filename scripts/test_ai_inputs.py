"""S5 입력 준비 서비스 테스트: 고정 코드 취득·암호화·확정·조회·삭제·만료. AI 제공자는 호출하지 않는다.

실제 PostgreSQL과 local Git fixture를 쓴다(GitHub API와 HTTP는 막아 둔다).
"""
import dataclasses
import json
import logging
import os
import sys
import threading
import unittest
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai_test_support as support  # noqa: E402

support.require_test_database()
sys.path.insert(0, str(support.ROOT))

import ai_input_support as S  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.collectors.ai_errors import PRChanged  # noqa: E402
from app.schemas.ai import ErrorCode, InputManifest, canonical_hash  # noqa: E402
from app.services.ai import crypto, input_store, job_runner, job_service, token_budget  # noqa: E402
from app.services.ai.providers.registry import get_profile  # noqa: E402
from app.services.ai.access import AIError  # noqa: E402
from app.services.ai.crypto import decrypt_input  # noqa: E402

T0 = S.T0
PROFILE = get_profile('claude-opus-5-5-v1')


def setUpModule():
    S.migrate()


class Prepare(S.InputCase):
    def test_prepared_input_has_encrypted_files_manifest_hashes_and_validity(self):
        owner, repo, input_id, job_id = self.prepared()
        row = self.input_row(input_id)
        self.assertEqual(row['status'], 'ready')
        manifest = InputManifest(**row['manifest'])
        self.assertEqual(row['manifest_hash'].strip(), canonical_hash(manifest))
        self.assertEqual(row['manifest_hash'], canonical_hash(row['manifest']))     # 저장된 JSON으로도 같은 해시
        self.assertEqual([f.path for f in manifest.files], sorted(S.PR_PATHS))
        self.assertEqual(manifest.pr_metadata.title, 'Add feature')
        self.assertEqual(manifest.pr_metadata_hash, canonical_hash(manifest.pr_metadata))
        self.assertEqual(row['coverage']['selected_content_coverage'], 'complete')
        self.assertEqual((row['coverage']['selected_files'], row['coverage']['inventory_coverage']), (4, 'complete'))
        self.assertEqual(self.job_row(job_id)['status'], 'succeeded')
        # 24시간 실행 유효기간·7일 원문 보관
        self.assertEqual(row['valid_until'] - row['finalized_at'], timedelta(hours=24))
        self.assertEqual(row['expires_at'] - row['created_at'], timedelta(days=7))
        # 파일은 암호화되어 있고 복호화하면 해시와 일치한다
        files = self.db.execute(text('SELECT id, path, content_state, encrypted_payload, key_version, content_hash, '
                                     'byte_count FROM ai_input_files WHERE input_id = :i ORDER BY path'), {'i': input_id}).all()
        self.assertEqual(len(files), 4)
        import hashlib
        for f in files:
            raw = bytes(f.encrypted_payload)
            for marker in S.SECRET_MARKERS:
                self.assertNotIn(marker.encode(), raw)
            plain = decrypt_input(raw, f.key_version, aad=f'{input_id}:{f.id}'.encode())
            self.assertEqual(hashlib.sha256(plain).hexdigest(), f.content_hash)
            self.assertEqual(len(plain), f.byte_count)

    def test_no_model_or_network_is_called_while_acquiring_code(self):
        sys.modules.pop('app.services.ai.providers', None)
        self.prepared()           # run_prepare가 requests/HTTP 호출 시 AssertionError를 던지게 막아 둔다
        self.assertNotIn('app.services.ai.providers', sys.modules)
        self.assertEqual(self.scalar('SELECT count(*) FROM usage_reservations'), 0)
        self.assertEqual(self.scalar('SELECT count(*) FROM ai_results'), 0)
        self.assertEqual(self.scalar("SELECT sum(estimated_cost + 0) FROM ai_attempts WHERE estimated_cost IS NOT NULL") or 0, 0)

    def test_nothing_sensitive_reaches_logs_queue_jobs_or_audit(self):
        records = []
        handler = logging.Handler()
        handler.emit = records.append
        root = logging.getLogger()
        old_level = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        try:
            owner, repo, input_id, job_id = self.prepared(paths=S.PR_PATHS + ['app/config.py', 'a[1].py'])
        finally:
            root.removeHandler(handler)
            root.setLevel(old_level)
        logs = ' '.join(r.getMessage() for r in records)
        stored = ' '.join([json.dumps(dict(self.job_row(job_id)['request_options'])),
                           self.scalar("SELECT coalesce(string_agg(details::text || action || request_id, ' '), '') "
                                       "FROM audit_events"),
                           self.scalar("SELECT coalesce(string_agg(event_type, ' '), '') FROM task_outbox")])
        for marker in S.SECRET_MARKERS:
            self.assertNotIn(marker, logs)
            self.assertNotIn(marker, stored)
        # 제외된 파일(의심 비밀)은 payload 자체가 저장되지 않는다
        excluded = self.db.execute(text("SELECT path, encrypted_payload FROM ai_input_files WHERE input_id = :i "
                                        "AND content_state <> 'read'"), {'i': input_id}).all()
        self.assertIn('app/config.py', [e.path for e in excluded])
        self.assertTrue(all(e.encrypted_payload is None for e in excluded))

    def test_all_excluded_selection_is_ready_but_not_reviewable(self):
        owner, repo, input_id, job_id = self.prepared(paths=['.env', 'link.py', 'package-lock.json'])
        row = self.input_row(input_id)
        self.assertEqual((row['status'], row['coverage']['selected_content_coverage']), ('ready', 'none'))
        preview = input_store.load_preview(self.db, input_id, owner, now=T0)
        self.assertFalse(preview.reviewable)
        self.assertEqual({f.exclusion_reason.value for f in preview.files},
                         {'secret_suspected', 'link_or_submodule', 'generated_file'})
        expected = token_budget.estimate_input_tokens(PROFILE, 'review', payload_bytes=0,
                                                      description_chars=len('Add feature') + len('Adds the feature'),
                                                      file_count=0)
        self.assertEqual(preview.token_estimate.input_tokens, expected)

    def test_partial_selection_is_distinguished_from_pr_total(self):
        owner, repo, input_id, _ = self.prepared(paths=['main.py', '.env'], changed_files=18)
        coverage = self.input_row(input_id)['coverage']
        self.assertEqual((coverage['selected_files'], coverage['pr_total_files']), (2, 18))      # 선택 범위 vs PR 전체
        self.assertEqual(coverage['selected_content_coverage'], 'partial')
        self.assertIn('not_selected', coverage['limitations'])
        self.assertEqual(coverage['inventory_coverage'], 'complete')
        self.assertEqual(coverage['analysis_coverage'], 'none')

    def test_selection_failures_close_the_input_without_files(self):
        cases = {'INVALID_SELECTION': dict(paths=['not/in/pr.py'])}
        for code, kw in cases.items():
            owner, repo = self.make_user(), self.make_repo()
            result = self.submit(owner, repo, kw['paths'])
            self.assertEqual(self.run_prepare(result.job.id), 'failed')
            self.assertEqual(self.job_row(result.job.id)['error_code'], code)
            self.assertEqual(self.input_row(result.job.input_id)['status'], 'failed')
            self.assertEqual(self.scalar('SELECT count(*) FROM ai_input_files WHERE input_id = :i',
                                         i=result.job.input_id), 0)
        with S.ai_env(AI_MAX_FILES=2):
            owner, repo = self.make_user(), self.make_repo()
            result = self.submit(owner, repo, S.PR_PATHS)
            self.assertEqual(self.run_prepare(result.job.id), 'failed')
            self.assertEqual(self.job_row(result.job.id)['error_code'], 'INPUT_TOO_LARGE')
        with S.ai_env(AI_MAX_INPUT_BYTES=100):
            owner, repo = self.make_user(), self.make_repo()
            result = self.submit(owner, repo, ['src/b.py', 'main.py'])
            self.assertEqual(self.run_prepare(result.job.id), 'failed')
            self.assertEqual(self.job_row(result.job.id)['error_code'], 'INPUT_TOO_LARGE')
            self.assertEqual(self.input_row(result.job.input_id)['status'], 'failed')

    def test_pr_changed_before_or_after_acquisition_never_finalizes(self):
        for side_effect in (PRChanged('x'), [None, PRChanged('x')]):
            owner, repo = self.make_user(), self.make_repo()
            result = self.submit(owner, repo)
            self.assertEqual(self.run_prepare(result.job.id, verify=side_effect), 'failed')
            row = self.job_row(result.job.id)
            self.assertEqual((row['status'], row['error_code'], row['attempt_count']), ('failed', 'PR_CHANGED', 1))
            self.assertEqual(self.input_row(result.job.input_id)['status'], 'failed')      # ready 확정 대신 PR_CHANGED
            self.assertEqual(self.scalar('SELECT count(*) FROM ai_input_files WHERE input_id = :i',
                                         i=result.job.input_id), 0)

    def test_access_loss_is_permanent_and_check_outage_is_retried(self):
        owner, repo = self.make_user(), self.make_repo()
        result = self.submit(owner, repo)
        lost = AIError(ErrorCode.RESOURCE_NOT_FOUND, 'x')
        self.assertEqual(self.run_prepare(result.job.id, authorize=lambda *a, **k: (_ for _ in ()).throw(lost)), 'failed')
        self.assertEqual(self.job_row(result.job.id)['error_code'], 'RESOURCE_NOT_FOUND')
        self.assertEqual(self.input_row(result.job.input_id)['status'], 'failed')

        owner2, repo2 = self.make_user(), self.make_repo()
        result2 = self.submit(owner2, repo2)
        outage = AIError(ErrorCode.ACCESS_CHECK_UNAVAILABLE, 'x', retryable=True)
        self.assertEqual(self.run_prepare(result2.job.id, authorize=lambda *a, **k: (_ for _ in ()).throw(outage)), 'retry')
        self.assertEqual(self.input_row(result2.job.input_id)['status'], 'building')       # 재시도 대기 동안 유지
        self.assertEqual(self.job_row(result2.job.id)['status'], 'retry_wait')

    def test_preparation_time_limit(self):
        owner, repo = self.make_user(), self.make_repo()
        result = self.submit(owner, repo)
        with patch('app.tasks.ai_prepare.time.monotonic', side_effect=[0, 500, 500, 500, 500]):
            self.assertEqual(self.run_prepare(result.job.id), 'failed')
        self.assertEqual(self.job_row(result.job.id)['error_code'], 'PREPARE_TIMEOUT')
        self.assertEqual(self.input_row(result.job.input_id)['status'], 'failed')


class InputHash(S.InputCase):
    def expire(self, input_id):
        """보관 기간이 지난 시각으로 유지관리를 돌려 원문을 삭제하고 입력을 만료시킨다."""
        deadline = self.input_row(input_id)['expires_at']
        input_store.run_input_maintenance(self.db, now=deadline + timedelta(seconds=1))
        self.assertEqual(self.input_row(input_id)['status'], 'expired')

    def test_same_content_gives_same_hash_and_any_change_gives_a_different_one(self):
        owner, repo = self.make_user(), self.make_repo()
        a = self.prepared(owner, repo, ['main.py', 'src/b.py'])
        row_a = self.input_row(a[2])
        self.expire(a[2])
        # 선택 순서만 다른 새 준비: 같은 입력이다
        b = self.prepared(owner, repo, ['src/b.py', 'main.py'], now=T0 + timedelta(days=8))
        self.assertNotEqual(a[2], b[2])
        row_b = self.input_row(b[2])
        self.assertEqual((row_a['manifest_hash'], row_a['input_hash']), (row_b['manifest_hash'], row_b['input_hash']))
        self.assertEqual(self.job_row(a[3])['fingerprint'], self.job_row(b[3])['fingerprint'])
        self.expire(b[2])
        # 선택이 다르면 다르다
        c = self.prepared(owner, repo, ['main.py'], now=T0 + timedelta(days=16))
        self.assertNotEqual(self.input_row(c[2])['manifest_hash'], row_a['manifest_hash'])
        self.expire(c[2])
        # PR 설명이 달라지면 다른 입력(같은 SHA·같은 선택)
        d = self.prepared(owner, repo, ['main.py', 'src/b.py'], body='A different description',
                          now=T0 + timedelta(days=24))
        row_d = self.input_row(d[2])
        self.assertNotEqual(row_d['manifest_hash'], row_a['manifest_hash'])
        self.assertNotEqual(row_d['input_hash'], row_a['input_hash'])
        self.assertNotEqual(self.job_row(d[3])['fingerprint'], self.job_row(a[3])['fingerprint'])

    def test_fingerprint_reuse_only_while_the_prepared_input_is_usable(self):
        owner, repo = self.make_user(), self.make_repo()
        first = self.submit(owner, repo)
        again = self.submit(owner, repo)                            # 진행 중 준비 재사용
        self.assertEqual((again.created, again.reused, again.job.id), (False, 'fingerprint', first.job.id))
        self.assertEqual(self.run_prepare(first.job.id), 'succeeded')
        reused = self.submit(owner, repo, now=T0 + timedelta(hours=1))
        self.assertEqual((reused.created, reused.job.input_id), (False, first.job.input_id))
        stale = self.submit(owner, repo, now=T0 + timedelta(hours=25))       # 실행 유효기간(24h)이 지나면 새로 준비
        self.assertTrue(stale.created)
        self.assertNotEqual(stale.job.input_id, first.job.input_id)
        self.assertEqual(stale.job.generation, 1)

    def test_same_key_returns_the_same_input_and_other_body_conflicts(self):
        from app.services.ai import idempotency, input_store as store
        owner, repo = self.make_user(), self.make_repo()
        cap = self.captured(repo)
        args = dict(owner_id=owner, repository_id=repo, captured=cap, selected_paths=['main.py'],
                    route='POST /api/ai/v1/inputs', idempotency_key='same-key', now=T0)
        first = store.create_input_and_job(self.db, body_hash=idempotency.request_hash({'a': 1}), **args)
        again = store.create_input_and_job(self.db, body_hash=idempotency.request_hash({'a': 1}), **args)
        self.assertEqual((again.reused, again.job.input_id), ('idempotency', first.job.input_id))
        with self.assertRaises(AIError) as ctx:
            store.create_input_and_job(self.db, body_hash=idempotency.request_hash({'a': 2}), **args)
        self.assertEqual(ctx.exception.code, ErrorCode.IDEMPOTENCY_CONFLICT)
        self.assertEqual(self.scalar('SELECT count(*) FROM ai_inputs'), 1)

    def test_concurrent_identical_requests_create_one_input_job_and_revision(self):
        owner, repo = self.make_user(), self.make_repo()
        cap = self.captured(repo)
        results = []

        def run(i):
            with S.Session() as db:
                results.append(input_store.create_input_and_job(
                    db, owner_id=owner, repository_id=repo, captured=cap, selected_paths=['main.py'],
                    route='POST /api/ai/v1/inputs', idempotency_key=f'k{i}', body_hash=canonical_hash({'i': i}), now=T0))
        threads = [threading.Thread(target=run, args=(i,)) for i in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sum(r.created for r in results), 1)
        for table in ('ai_inputs', 'ai_jobs', 'pr_revisions'):
            self.assertEqual(self.scalar(f'SELECT count(*) FROM {table}'), 1, table)


class Reading(S.InputCase):
    def file_id(self, input_id, path):
        return self.scalar('SELECT id FROM ai_input_files WHERE input_id = :i AND path = :p', i=input_id, p=path)

    def read(self, owner, input_id, path, **kw):
        return input_store.read_file_content(self.db, input_id, self.file_id(input_id, path), owner,
                                             now=kw.pop('now', T0 + timedelta(hours=1)), **kw)

    def test_preview_matches_the_stored_manifest_and_is_complete(self):
        owner, repo, input_id, job_id = self.prepared(changed_files=4)
        preview = input_store.load_preview(self.db, input_id, owner, now=T0 + timedelta(hours=1))
        row = self.input_row(input_id)
        self.assertEqual((preview.status.value, preview.manifest_hash), ('ready', row['manifest_hash'].strip()))
        self.assertEqual([f.path for f in preview.files], sorted(S.PR_PATHS))
        self.assertEqual(preview.head_sha, self.fx.f1)
        self.assertEqual((preview.base_sha, preview.merge_base_sha), (self.fx.m1, self.fx.c0))
        self.assertEqual((preview.pr_title, preview.pr_body), ('Add feature', 'Adds the feature'))
        self.assertTrue(preview.execution_valid and preview.raw_available and preview.reviewable)
        self.assertEqual(preview.provider_profile, 'claude-opus-5-5-v1')
        self.assertEqual(preview.policy_version, 'code-transfer-v1')
        self.assertEqual(preview.prepare_job.status.value, 'succeeded')
        total_bytes = sum(f.byte_count for f in preview.files)
        estimate = preview.token_estimate
        expected = token_budget.estimate_input_tokens(PROFILE, 'review', payload_bytes=total_bytes,
                                                      description_chars=len('Add feature') + len('Adds the feature'),
                                                      file_count=4)
        self.assertEqual(estimate.input_tokens, expected)
        self.assertEqual((estimate.profile, estimate.limit, estimate.within_limit, estimate.max_output_tokens,
                          estimate.currency), ('claude-opus-5-5-v1', 24_000, True, 4_000, 'USD'))
        self.assertEqual(estimate.estimated_max_cost, str(token_budget.max_cost(PROFILE, expected)))   # 예약액 상한
        # 모델 호출 전에 한도를 넘는 입력임을 미리 알린다(생성 요청은 이 입력으로 시작할 수 없다)
        with patch('app.services.ai.credentials.preferred_profile',
                   return_value=dataclasses.replace(PROFILE, id='tiny-v1', max_input_tokens=100)):
            tiny = input_store.load_preview(self.db, input_id, owner, now=T0 + timedelta(hours=1)).token_estimate
        self.assertEqual((tiny.within_limit, tiny.limit, tiny.profile), (False, 100, 'tiny-v1'))
        # 같은 manifest의 hunks가 파일 미리보기에 그대로 있다
        by_path = {f.path: f for f in preview.files}
        self.assertEqual(by_path['src/b.py'].hunks[0].new_lines, 21)
        self.assertEqual(by_path['src/b.py'].change_type.value, 'renamed')
        self.assertEqual(by_path['src/b.py'].old_path, 'src/a.py')

    def test_preview_expiry_flags(self):
        owner, repo, input_id, _ = self.prepared()
        after_valid = input_store.load_preview(self.db, input_id, owner, now=T0 + timedelta(hours=30))
        self.assertEqual((after_valid.status.value, after_valid.execution_valid, after_valid.raw_available),
                         ('ready', False, True))                         # 새 실행은 불가, 원문은 아직 보관
        after_retention = input_store.load_preview(self.db, input_id, owner, now=T0 + timedelta(days=8))
        self.assertEqual((after_retention.status.value, after_retention.execution_valid), ('expired', False))

    def test_building_preview_has_no_files_yet(self):
        owner, repo = self.make_user(), self.make_repo()
        result = self.submit(owner, repo)
        preview = input_store.load_preview(self.db, result.job.input_id, owner, now=T0)
        self.assertEqual((preview.status.value, preview.files, preview.token_estimate, preview.manifest_hash),
                         ('building', [], None, None))
        self.assertEqual((preview.prepare_job.status.value, preview.prepare_job.stage.value), ('queued', 'input_preparation'))
        self.assertEqual(preview.coverage.inventory_coverage.value, 'unknown')

    def test_lines_have_real_line_numbers_and_side_filters(self):
        owner, repo, input_id, _ = self.prepared()
        old = self.fx.blob_lines(self.fx.c0, 'src/a.py')
        new = self.fx.blob_lines(self.fx.f1, 'src/b.py')
        content = self.read(owner, input_id, 'src/b.py')
        for line in content.lines:
            if line.kind.value == 'context':
                self.assertEqual((new[line.right_line - 1], old[line.left_line - 1]), (line.text, line.text))
            elif line.kind.value == 'added':
                self.assertEqual(new[line.right_line - 1], line.text)
                self.assertIsNone(line.left_line)
            else:
                self.assertEqual(old[line.left_line - 1], line.text)
                self.assertIsNone(line.right_line)
        right = self.read(owner, input_id, 'src/b.py', side='RIGHT', start_line=2, end_line=3)
        self.assertEqual([l.right_line for l in right.lines], [2, 3])
        left = self.read(owner, input_id, 'src/b.py', side='LEFT')
        self.assertTrue(all(l.left_line is not None for l in left.lines))
        self.assertNotIn('added', {l.kind.value for l in left.lines})
        # 삭제 파일은 LEFT만, 새 파일은 RIGHT만
        self.assertEqual({l.kind.value for l in self.read(owner, input_id, 'src/old_mod.py').lines}, {'removed'})
        self.assertEqual({l.kind.value for l in self.read(owner, input_id, 'src/new_mod.py').lines}, {'added'})
        for bad in (dict(path='src/old_mod.py', side='RIGHT'), dict(path='src/new_mod.py', side='LEFT')):
            with self.assertRaises(AIError) as ctx:
                self.read(owner, input_id, bad['path'], side=bad['side'])
            self.assertEqual(ctx.exception.code, ErrorCode.INVALID_SELECTION)
        for kw in (dict(start_line=1, end_line=2), dict(side='RIGHT', start_line=1), dict(side='BOTH'),
                   dict(side='RIGHT', start_line=0, end_line=1), dict(side='RIGHT', start_line=5, end_line=2)):
            with self.assertRaises(AIError) as ctx:
                self.read(owner, input_id, 'src/b.py', **kw)
            self.assertEqual(ctx.exception.code, ErrorCode.INVALID_SELECTION)

    def test_excluded_files_have_no_content_and_other_owners_see_nothing(self):
        owner, repo, input_id, _ = self.prepared(paths=['main.py', '.env'])
        excluded = self.read(owner, input_id, '.env')
        self.assertEqual((excluded.content_state.value, excluded.exclusion_reason.value, excluded.lines),
                         ('excluded', 'secret_suspected', []))
        stranger = self.make_user()
        for fn in (lambda: input_store.load_preview(self.db, input_id, stranger, now=T0),
                   lambda: input_store.read_file_content(self.db, input_id, self.file_id(input_id, 'main.py'),
                                                         stranger, now=T0),
                   lambda: input_store.delete_input(self.db, input_id, stranger, now=T0)):
            with self.assertRaises(AIError) as ctx:
                fn()
            self.assertEqual((ctx.exception.code, ctx.exception.status_code), (ErrorCode.RESOURCE_NOT_FOUND, 404))
        other_owner, other_repo, other_input, _ = self.prepared()
        with self.assertRaises(AIError) as ctx:                       # 같은 작성자라도 다른 입력의 파일 id는 404
            input_store.read_file_content(self.db, input_id, self.file_id(other_input, 'main.py'), owner, now=T0)
        self.assertEqual(ctx.exception.code, ErrorCode.RESOURCE_NOT_FOUND)

    def test_building_expired_and_deleted_inputs_are_not_readable(self):
        owner, repo = self.make_user(), self.make_repo()
        building = self.submit(owner, repo)
        with self.assertRaises(AIError) as ctx:
            input_store.read_file_content(self.db, building.job.input_id, uuid4(), owner, now=T0)
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)
        owner2, repo2, input_id, _ = self.prepared()
        fid = self.file_id(input_id, 'main.py')
        with self.assertRaises(AIError) as ctx:
            input_store.read_file_content(self.db, input_id, fid, owner2, now=T0 + timedelta(days=8))
        self.assertEqual((ctx.exception.code, ctx.exception.status_code), (ErrorCode.INPUT_EXPIRED, 410))

    def test_tampered_ciphertext_is_detected_and_audited(self):
        owner, repo, input_id, _ = self.prepared()
        fid = self.file_id(input_id, 'main.py')
        other = self.file_id(input_id, 'src/b.py')
        self.db.rollback()

        def tamper(sql, **params):
            with S.Session() as db:
                db.execute(text("SET LOCAL session_replication_role = replica"))   # 변경 방지 trigger를 우회(공격 시뮬레이션)
                db.execute(text(sql), params)
                db.commit()
        # 1) 바이트 변조 2) 다른 파일의 암호문으로 바꿔치기(aad 불일치) 3) content_hash 변경
        original = self.db.execute(text('SELECT encrypted_payload FROM ai_input_files WHERE id = :i'), {'i': fid}).scalar()
        for sql, params in (
            ("UPDATE ai_input_files SET encrypted_payload = set_byte(encrypted_payload, 20, 255 - get_byte(encrypted_payload, 20)) WHERE id = :i", dict(i=fid)),
            ("UPDATE ai_input_files SET encrypted_payload = (SELECT encrypted_payload FROM ai_input_files WHERE id = :o) WHERE id = :i", dict(i=fid, o=other)),
            ("UPDATE ai_input_files SET encrypted_payload = :p, content_hash = :h WHERE id = :i", dict(i=fid, p=bytes(original), h='0' * 64)),
        ):
            tamper("UPDATE ai_input_files SET encrypted_payload = :p WHERE id = :i", i=fid, p=bytes(original))   # 원복
            tamper(sql, **params)
            with self.assertRaises(AIError) as ctx:
                input_store.read_file_content(self.db, input_id, fid, owner, now=T0 + timedelta(hours=1))
            self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)
            self.assertNotIn('main', ctx.exception.message)
        self.assertEqual(self.scalar("SELECT count(*) FROM audit_events WHERE action = 'input.integrity_failure'"), 3)

    def test_views_are_audited_without_content(self):
        owner, repo, input_id, _ = self.prepared()
        self.read(owner, input_id, 'main.py')
        row = self.db.execute(text("SELECT action, details, object_type FROM audit_events WHERE action = 'input.file_viewed'")).one()
        self.assertEqual((row.details, row.object_type), ({}, 'input_file'))


class DeletionAndExpiry(S.InputCase):
    def make_result(self, owner, repo, input_id, kind):
        """입력을 쓰는 성공한 생성 작업과 결과·지적(파일 참조)을 만든다."""
        job = job_service.create_job(
            self.db, owner_id=owner, repository_id=repo, input_id=input_id, kind=kind, fingerprint=canonical_hash(kind),
            route='POST /api/ai/v1/jobs', idempotency_key=uuid4().hex, body_hash=canonical_hash([kind, str(uuid4())]),
            pinned=self.pinned(owner, repo, input_id), now=T0 + timedelta(minutes=1)).job
        lease = job_service.claim_job(self.db, job.id, now=T0 + timedelta(minutes=1))
        job_service.finish_job(self.db, lease, 'succeeded', now=T0 + timedelta(minutes=2))
        file_id = self.scalar("SELECT id FROM ai_input_files WHERE input_id = :i AND path = 'main.py'", i=input_id)
        result = self.db.execute(text("""INSERT INTO ai_results(job_id, owner_user_id, repository_id, payload, payload_hash,
            coverage, structure_validation, expires_at) VALUES(:j, :o, :r, '{}', :h, '{}', 'passed', '2099-01-01') RETURNING id"""),
                                 {'j': job.id, 'o': owner, 'r': repo, 'h': 'a' * 64}).scalar()
        self.db.execute(text("""INSERT INTO ai_findings(result_id, ordinal, fingerprint, category, priority, title, detail,
            source_file_id, side, start_line, end_line) VALUES(:r, 1, :h, 'c', 1, 't', '{}', :f, 'RIGHT', 1, 1)"""),
                        {'r': result, 'h': 'b' * 64, 'f': file_id})
        self.db.commit()
        return job.id, result

    def test_raw_deletion_cancels_linked_jobs_keeps_results_and_blocks_reads_and_new_runs(self):
        owner, repo, input_id, _ = self.prepared()
        done_job, result = self.make_result(owner, repo, input_id, 'summary')            # 공유 input의 기존 결과
        queued = job_service.create_job(
            self.db, owner_id=owner, repository_id=repo, input_id=input_id, kind='review', fingerprint=canonical_hash('r'),
            route='POST /api/ai/v1/jobs', idempotency_key='k-review', body_hash=canonical_hash('r'), pinned=self.pinned(owner, repo, input_id), now=T0 + timedelta(minutes=3)).job
        running = job_service.create_job(
            self.db, owner_id=owner, repository_id=repo, input_id=input_id, kind='review', fingerprint=canonical_hash('r2'),
            route='POST /api/ai/v1/jobs', idempotency_key='k-review2', body_hash=canonical_hash('r2'), pinned=self.pinned(owner, repo, input_id), now=T0 + timedelta(minutes=3)).job
        lease = job_service.claim_job(self.db, running.id, now=T0 + timedelta(minutes=3))
        fid = self.scalar("SELECT id FROM ai_input_files WHERE input_id = :i AND path = 'main.py'", i=input_id)

        self.assertEqual(input_store.delete_input(self.db, input_id, owner, request_id='req-1', now=T0 + timedelta(hours=1)), 'expired')
        self.assertEqual(self.job_row(queued.id)['status'], 'cancelled')                   # 연결 작업 취소
        self.assertEqual(self.job_row(running.id)['status'], 'cancel_requested')
        self.assertEqual(job_service.finish_job(self.db, lease, 'succeeded', now=T0 + timedelta(hours=1)), 'cancelled')
        self.assertEqual(self.job_row(done_job)['status'], 'succeeded')                    # 이미 끝난 결과는 유지
        self.assertEqual(self.scalar('SELECT count(*) FROM ai_results WHERE id = :r', r=result), 1)
        self.assertEqual(self.scalar('SELECT count(*) FROM ai_findings WHERE result_id = :r', r=result), 1)
        row = self.input_row(input_id)
        self.assertEqual(row['status'], 'expired')
        self.assertIsNotNone(row['manifest'])                                             # 입력 행·manifest는 남는다
        self.assertEqual(self.scalar('SELECT count(*) FROM ai_input_files WHERE input_id = :i', i=input_id), 4)
        self.assertEqual(self.scalar('SELECT count(*) FROM ai_input_files WHERE input_id = :i AND encrypted_payload IS NOT NULL',
                                     i=input_id), 0)                                      # 원문은 모두 삭제
        with self.assertRaises(AIError) as ctx:
            input_store.read_file_content(self.db, input_id, fid, owner, now=T0 + timedelta(hours=1))
        self.assertEqual(ctx.exception.code, ErrorCode.INPUT_EXPIRED)
        with self.assertRaises(AIError) as ctx:                                            # 재생성도 불가
            self.make_result(owner, repo, input_id, 'review')
        self.assertEqual(ctx.exception.code, ErrorCode.INPUT_EXPIRED)
        self.assertEqual(input_store.delete_input(self.db, input_id, owner, now=T0 + timedelta(hours=2)), 'expired')   # 멱등
        self.assertEqual(self.scalar("SELECT count(*) FROM audit_events WHERE action = 'input.deleted'"), 2)

    def test_deleting_a_job_never_removes_the_shared_input(self):
        owner, repo, input_id, _ = self.prepared()
        job_id, result = self.make_result(owner, repo, input_id, 'summary')
        self.make_result(owner, repo, input_id, 'review')
        self.db.execute(text("UPDATE ai_jobs SET deleted_at = now() WHERE id = :j"), {'j': job_id})
        self.db.commit()
        self.assertEqual(self.input_row(input_id)['status'], 'ready')
        self.assertEqual(self.scalar('SELECT count(*) FROM ai_input_files WHERE input_id = :i AND encrypted_payload IS NOT NULL',
                                     i=input_id), 4)

    def test_completion_then_delete_and_delete_then_completion_are_consistent(self):
        # 1) 준비 완료 후 삭제: 원문 삭제됨
        owner, repo, input_id, _ = self.prepared()
        input_store.delete_input(self.db, input_id, owner, now=T0 + timedelta(hours=1))
        self.assertEqual(self.input_row(input_id)['status'], 'expired')
        # 2) 준비 중 삭제 접수: 준비 작업은 cancelled, 입력은 failed, 파일 없음
        owner2, repo2 = self.make_user(), self.make_repo()
        result = self.submit(owner2, repo2)
        self.assertEqual(input_store.delete_input(self.db, result.job.input_id, owner2, now=T0), 'failed')
        self.assertEqual(self.job_row(result.job.id)['status'], 'cancelled')
        self.assertEqual(self.run_prepare(result.job.id), 'skipped')                      # 취소된 작업은 시작도 하지 않는다
        self.assertEqual(self.scalar('SELECT count(*) FROM ai_input_files WHERE input_id = :i', i=result.job.input_id), 0)

    def test_cancel_arriving_while_the_worker_is_acquiring_wins_over_completion(self):
        owner, repo = self.make_user(), self.make_repo()
        result = self.submit(owner, repo)
        input_id = result.job.input_id

        def collect_then_delete(*args, **kwargs):
            from app.collectors.git import ai_diff_collector
            collected = real(*args, **kwargs)
            with S.Session() as db:                                # 코드 취득 도중 사용자가 삭제
                input_store.delete_input(db, input_id, owner, now=T0)
            return collected
        from app.collectors.git import ai_diff_collector
        real = ai_diff_collector.collect_selected_code
        with patch.object(ai_diff_collector, 'collect_selected_code', side_effect=collect_then_delete):
            outcome = self.run_prepare(result.job.id)
        self.assertEqual(outcome, 'cancelled')
        self.assertEqual(self.job_row(result.job.id)['status'], 'cancelled')
        self.assertEqual(self.input_row(input_id)['status'], 'failed')
        self.assertEqual(self.scalar('SELECT count(*) FROM ai_input_files WHERE input_id = :i', i=input_id), 0)

    def test_cancelled_preparation_leaves_a_building_input_that_maintenance_closes(self):
        owner, repo = self.make_user(), self.make_repo()
        result = self.submit(owner, repo)
        job_service.request_cancel(self.db, result.job.id, owner, now=T0)
        self.assertEqual(self.input_row(result.job.input_id)['status'], 'building')
        stats = input_store.run_input_maintenance(self.db, now=T0)
        self.assertEqual(stats['failed'], 1)
        self.assertEqual(self.input_row(result.job.input_id)['status'], 'failed')
        live = self.submit(self.make_user(), self.make_repo())            # 진행 중인 준비는 건드리지 않는다
        self.assertEqual(input_store.run_input_maintenance(self.db, now=T0)['failed'], 0)
        self.assertEqual(self.input_row(live.job.input_id)['status'], 'building')

    def test_retention_deadline_overrides_references_and_cancels_pending_runs(self):
        owner, repo, input_id, _ = self.prepared()
        self.make_result(owner, repo, input_id, 'summary')
        pending = job_service.create_job(
            self.db, owner_id=owner, repository_id=repo, input_id=input_id, kind='review', fingerprint=canonical_hash('p'),
            route='POST /api/ai/v1/jobs', idempotency_key='kp', body_hash=canonical_hash('p'), pinned=self.pinned(owner, repo, input_id), now=T0 + timedelta(minutes=3)).job
        self.assertEqual(input_store.run_input_maintenance(self.db, now=T0 + timedelta(days=6))['expired'], 0)
        stats = input_store.run_input_maintenance(self.db, now=T0 + timedelta(days=8))
        self.assertEqual((stats['expired'], stats['payloads_purged'], stats['jobs_cancelled']), (1, 4, 1))
        self.assertEqual(self.input_row(input_id)['status'], 'expired')
        self.assertEqual(self.job_row(pending.id)['status'], 'cancelled')
        self.assertEqual(self.scalar('SELECT count(*) FROM ai_findings'), 1)               # 결과·지적은 별도 보관 기한
        self.assertEqual(input_store.run_input_maintenance(self.db, now=T0 + timedelta(days=9))['expired'], 0)


class Finalize(S.InputCase):
    def test_finalize_is_atomic_with_job_completion_and_only_for_building_inputs(self):
        owner, repo, input_id, _ = self.prepared()
        from app.services.ai import input_builder
        with self.assertRaises(input_store.InputNotBuilding):
            input_store.finalize_input(self.db, input_id, None, None)
        self.db.rollback()
        # 파일 저장 중 실패하면 입력·파일·작업 상태 모두 되돌려진다
        owner2, repo2 = self.make_user(), self.make_repo()
        result = self.submit(owner2, repo2)
        with patch('app.services.ai.input_store.encrypt_input', side_effect=RuntimeError('kms down')):
            self.assertEqual(self.run_prepare(result.job.id), 'retry')
        self.assertEqual(self.input_row(result.job.input_id)['status'], 'building')
        self.assertEqual(self.scalar('SELECT count(*) FROM ai_input_files WHERE input_id = :i', i=result.job.input_id), 0)
        self.assertEqual(self.job_row(result.job.id)['error_code'], 'RESULT_STORAGE_FAILED')


if __name__ == '__main__':
    unittest.main(verbosity=2)
