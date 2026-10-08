"""S12 인수인계 테스트: 준비 체크폼(미입력 unknown·버전 충돌), 초안 생성(기준 버전 고정), 편집(원본/편집본 분리·재생성 시 보존),
Markdown 안전 출력. AI 지적·미확인 항목이 확정 사실로 바뀌지 않는지가 핵심이다. 실제 PostgreSQL을 쓰고 모델·외부 호출은 없다."""
import json
import os
import sys
import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import UUID, uuid4

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai_test_support as support  # noqa: E402

support.require_test_database()
sys.path.insert(0, str(support.ROOT))

import ai_credential_support as C  # noqa: E402
import ai_input_support as S  # noqa: E402
import test_ai_history_context as H  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pydantic import ValidationError  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402
from starlette.middleware.sessions import SessionMiddleware  # noqa: E402

from app.api.ai import handover as handover_api  # noqa: E402
from app.api.ai import jobs as jobs_api  # noqa: E402
from app.api.ai.dependencies import ai_error_response, ai_validation_error_response  # noqa: E402
from app.routers import auth as auth_router  # noqa: E402
from app.schemas.ai import (HANDOVER_ITEM_KEYS, ErrorCode, HandoverAdoptRequest, HandoverDocumentPut, HandoverGenerateRequest, HandoverItemIn,  # noqa: E402
                            HandoverPreparationPut, HandoverTargetCreate, HandoverTargetPatch, canonical_hash)
from app.services.ai import handover_draft, handover_service as svc, markdown_safety  # noqa: E402
from app.services.ai.access import AIError  # noqa: E402

ORIGIN = 'http://localhost:8000'
FORBIDDEN = ('오류 없음', '문제 없음', '이상 없음', '버그 없음', '안전합니다')
HOSTILE = ['<script>alert(1)</script>', '[click](javascript:alert(1))', '# 가짜 제목', '![t](http://tracker.example/p.png)', '`` ` ``', '‮evil',
           '> 인용', '- 목록', '<img src=x onerror=alert(1)>', '[ref]: javascript:alert(1)', '&#106;avascript:alert(1)', '|a|b|']


HOSTILE_USER = [h for h in HOSTILE if '‮' not in h]          # 사용자가 직접 입력할 수 있는 값(방향 제어 문자는 입력 단계에서 거절된다)


def setUpModule():
    S.migrate()


def now():
    return datetime.now(timezone.utc)


def items(**kw):
    """items(assignee=('ready', '홍길동'), run_method=('needed', None, 'https://x.y'))"""
    out = {}
    for key, v in kw.items():
        status, text_, *link = v
        out[key] = HandoverItemIn(status=status, text=text_, link=link[0] if link else None)
    return out


def put_body(expected=0, target_version=1, **kw):
    return HandoverPreparationPut(expected_version=expected, confirmed_target_version=target_version, items=items(**kw))


class Case(H.HistoryCase):
    """results와 대상을 만드는 공용 helper. H.HistoryCase의 snapshot 도우미를 그대로 쓴다."""

    def setUp(self):
        super().setUp()
        self.owner = self.repo = None

    def tearDown(self):
        self.db.rollback()
        with S.Session() as db:
            db.execute(text('DELETE FROM handover_targets'))
            db.execute(text("DELETE FROM audit_events WHERE action LIKE 'handover.%'"))
            db.commit()
        super().tearDown()

    def bypass(self, sql, **params):
        with S.Session() as db:
            db.execute(text('SET LOCAL session_replication_role = replica'))
            db.execute(text(sql), params)
            db.commit()

    def result_for(self, owner, repo, kind, finding_paths=(), pr_number=7, payload=None):
        _, _, input_id, _ = self.prepared(owner, repo, None, now=now(), pr_number=pr_number)
        pinned = C.pinned_for(self.db, owner, repo, input_id, manifest_hash=self.input_row(input_id)['manifest_hash'].strip())
        job = H.job_service.create_job(self.db, owner_id=owner, repository_id=repo, input_id=input_id, kind=kind, fingerprint=canonical_hash(str(uuid4())), route='r',
                                       idempotency_key=uuid4().hex, body_hash=canonical_hash(str(uuid4())), pinned=pinned).job
        lease = H.job_service.claim_job(self.db, job.id)
        H.job_service.finish_job(self.db, lease, 'succeeded')
        result_id = self.db.execute(text("""INSERT INTO ai_results (job_id, owner_user_id, repository_id, payload, payload_hash, coverage, structure_validation, expires_at)
                                            VALUES (:j, :o, :r, CAST(:p AS jsonb), repeat('a', 64), '{}'::jsonb, 'passed', :e) RETURNING id"""),
                                   {'j': job.id, 'o': owner, 'r': repo, 'p': json.dumps(payload or {}), 'e': now() + timedelta(days=30)}).scalar()
        file_id = self.db.execute(text('SELECT id FROM ai_input_files WHERE input_id = :i LIMIT 1'), {'i': input_id}).scalar()
        for n, spec in enumerate(finding_paths, start=1):
            path, title = (spec, f'finding {n}') if isinstance(spec, str) else spec
            self.db.execute(text("""INSERT INTO ai_findings (result_id, ordinal, fingerprint, category, priority, title, detail, source_file_id, side, start_line, end_line)
                                    VALUES (:r, :n, :f, 'maintainability', :n, :t, CAST(:d AS jsonb), :s, 'RIGHT', 2, 3)"""),
                            {'r': result_id, 'n': n, 'f': canonical_hash(f'{path}{n}{uuid4()}'), 't': title, 's': file_id,
                             'd': json.dumps({'location': {'path': path, 'side': 'RIGHT', 'start_line': 2, 'end_line': 3}})})
        self.db.commit()
        return result_id

    def feedback(self, result_id, owner, verdict, ordinal=1, version=1):
        finding = self.scalar('SELECT id FROM ai_findings WHERE result_id = :r AND ordinal = :o', r=result_id, o=ordinal)
        self.db.execute(text("""INSERT INTO finding_feedback (finding_id, author_user_id, verdict, version) VALUES (:f, :a, :v, :n)
                                ON CONFLICT (finding_id, author_user_id) DO UPDATE SET verdict = :v, version = :n"""),
                        {'f': finding, 'a': owner, 'v': verdict, 'n': version})
        self.db.commit()

    def world(self, *, paths=('main.py', 'src/a.py'), findings=('main.py', 'main.py', 'other.py'), snapshot=False, summary=True, review=True):
        owner, repo = self.make_user(), self.make_repo()
        self.owner, self.repo = owner, repo
        sid = self.snapshot(repo)[0] if snapshot else None
        target = svc.create_target(self.db, owner, HandoverTargetCreate(repository_id=repo, name='로그인 기능 인계', selected_paths=list(paths), snapshot_id=sid,
                                                                      purpose='담당자 이동', giver_name='김인계', receiver_name='이인수'))
        out = {'owner': owner, 'repo': repo, 'tid': target['target_id'], 'sid': sid, 'target': target}
        if review:
            out['review'] = self.result_for(owner, repo, 'review', list(findings), pr_number=7)
        if summary:
            out['summary'] = self.result_for(owner, repo, 'summary', [], pr_number=8, payload={
                'overview': 'main.py의 b 값이 바뀝니다.', 'author_intent': 'PR 설명: 기능 추가', 'unknowns': ['호출부는 보이지 않음'],
                'file_changes': [{'path': 'main.py', 'file_id': str(uuid4()), 'summary': 'b를 20으로 변경', 'evidence_ids': []},
                                 {'path': 'zzz.py', 'file_id': str(uuid4()), 'summary': '대상 밖 파일', 'evidence_ids': []}]})
        return out

    def generate(self, w, *, tv=None, pv=None, summary=True, review=True, force=False):
        target = svc.get_target(self.db, w['owner'], w['tid'])
        return svc.generate(self.db, w['owner'], w['tid'], HandoverGenerateRequest(
            expected_target_version=tv or target['version'], expected_preparation_version=target['preparation_version'] if pv is None else pv,
            summary_result_id=w.get('summary') if summary else None, review_result_id=w.get('review') if review else None, force=force))

    def code(self, fn, *a, **kw):
        with self.assertRaises(AIError) as ctx:
            fn(*a, **kw)
        self.db.rollback()
        return ctx.exception


class Preparation(Case):
    def test_a_new_target_has_six_unknown_items_and_unset_items_are_never_needed(self):
        w = self.world(summary=False, review=False)
        prep = svc.get_preparation(self.db, w['owner'], w['tid'])
        self.assertEqual((prep['version'], prep['needs_reconfirm'], [i['key'] for i in prep['items']]), (0, False, list(HANDOVER_ITEM_KEYS)))
        self.assertEqual({i['status'] for i in prep['items']}, {'unknown'})
        saved = svc.put_preparation(self.db, w['owner'], w['tid'], put_body(assignee=('ready', '이인수'), run_method=('needed', '실행 절차 작성 예정')))
        by = {i['key']: i for i in saved['items']}
        self.assertEqual((saved['version'], by['assignee']['status'], by['run_method']['status']), (1, 'ready', 'needed'))
        for key in ('description', 'verify_method', 'joint_review', 'receiver_run'):                             # 보내지 않은 항목은 미확인이지 준비 필요가 아니다
            self.assertEqual((by[key]['status'], by[key]['text'], by[key]['link']), ('unknown', None, None))
        self.assertEqual(by['assignee']['status_label'], '준비 완료 (작성자 기록)')       # 시스템이 검증한 완료로 읽히지 않게 항상 작성자 기록임을 붙인다

    def test_ready_needs_the_completion_information_and_unknown_cannot_carry_content(self):
        w = self.world(summary=False, review=False)
        for key in HANDOVER_ITEM_KEYS:
            exc = self.code(svc.put_preparation, self.db, w['owner'], w['tid'], put_body(**{key: ('ready', None)}))
            self.assertEqual((exc.code, exc.details['item']), (ErrorCode.INVALID_SELECTION, key), key)
        ok = svc.put_preparation(self.db, w['owner'], w['tid'], put_body(description=('ready', None, 'https://wiki.example/doc')))      # 설명 또는 링크
        self.assertEqual(ok['items'][1]['link'], 'https://wiki.example/doc')
        for bad in (dict(assignee=('unknown', '이름')), dict(description=('unknown', None, 'https://x.y'))):
            exc = self.code(svc.put_preparation, self.db, w['owner'], w['tid'], put_body(expected=1, **bad))
            self.assertEqual(exc.code, ErrorCode.INVALID_SELECTION)
        self.assertEqual(svc.get_preparation(self.db, w['owner'], w['tid'])['version'], 1)                        # 거절된 입력은 버전을 만들지 않는다

    def test_links_are_stored_and_shown_only_never_followed(self):
        w = self.world(summary=False, review=False)
        for link in ('javascript:alert(1)', 'ftp://x.y/z', 'http://', 'https://a.b/c d', 'https://a.b/<x>', 'file:///etc/passwd', '//evil.com/x'):
            exc = self.code(svc.put_preparation, self.db, w['owner'], w['tid'], put_body(description=('needed', None, link)))
            self.assertEqual(exc.code, ErrorCode.INVALID_SELECTION, link)
        with patch('requests.sessions.Session.request', side_effect=AssertionError('링크를 열면 안 된다')), \
                patch('socket.create_connection', side_effect=AssertionError('링크를 열면 안 된다')):
            saved = svc.put_preparation(self.db, w['owner'], w['tid'], put_body(description=('ready', None, 'https://wiki.example/doc')))
            self.generate(w, summary=False, review=False)
        self.assertEqual(saved['items'][1]['link'], 'https://wiki.example/doc')

    def test_extra_or_malformed_input_is_rejected(self):
        w = self.world(summary=False, review=False)
        exc = self.code(svc.put_preparation, self.db, w['owner'], w['tid'], HandoverPreparationPut(
            expected_version=0, confirmed_target_version=1, items={'unknown_key': HandoverItemIn(status='needed', text='x')}))
        self.assertEqual(exc.code, ErrorCode.INVALID_SELECTION)
        exc = self.code(svc.put_preparation, self.db, w['owner'], w['tid'], put_body(assignee=('needed', 'a\x00b')))
        self.assertEqual(exc.code, ErrorCode.INVALID_SELECTION)
        with self.assertRaises(ValidationError):
            HandoverItemIn(status='done')
        with self.assertRaises(ValidationError):
            HandoverItemIn(status='needed', text='x' * 2001)

    def test_version_conflicts_and_concurrent_saves(self):
        w = self.world(summary=False, review=False)
        svc.put_preparation(self.db, w['owner'], w['tid'], put_body(assignee=('needed', None)))
        exc = self.code(svc.put_preparation, self.db, w['owner'], w['tid'], put_body(expected=0, assignee=('needed', '다른 값')))
        self.assertEqual((exc.code, exc.details['reason'], exc.details['current_version']), (ErrorCode.VERSION_CONFLICT, 'PREPARATION_VERSION', 1))
        outcomes = []

        def run(label):
            with S.Session() as db:
                try:
                    svc.put_preparation(db, w['owner'], w['tid'], put_body(expected=1, assignee=('needed', label)))
                    outcomes.append('ok')
                except AIError as e:
                    outcomes.append(e.code)
        threads = [threading.Thread(target=run, args=(f'v{i}',)) for i in range(4)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual((outcomes.count('ok'), outcomes.count(ErrorCode.VERSION_CONFLICT)), (1, 3), outcomes)    # 같은 버전을 근거로 둘 이상 성공하지 않는다
        self.assertEqual(self.scalar('SELECT max(version) FROM handover_preparation_versions'), 2)

    def test_versions_are_appended_with_author_time_and_per_item_change_marks(self):
        w = self.world(summary=False, review=False)
        v1 = svc.put_preparation(self.db, w['owner'], w['tid'], put_body(assignee=('ready', '이인수'), run_method=('needed', '작성 필요')))
        v2 = svc.put_preparation(self.db, w['owner'], w['tid'], put_body(expected=1, assignee=('ready', '이인수'), run_method=('ready', '1) 설치 2) 실행')))
        by1, by2 = {i['key']: i for i in v1['items']}, {i['key']: i for i in v2['items']}
        self.assertEqual((by2['assignee']['updated_version'], by2['run_method']['updated_version']), (1, 2))      # 바뀐 항목만 새 버전 표시
        self.assertEqual(by2['assignee']['updated_at'], by1['assignee']['updated_at'])
        versions = svc.list_preparation_versions(self.db, w['owner'], w['tid'])
        self.assertEqual([v['version'] for v in versions], [2, 1])
        self.assertEqual({i['key']: i['status'] for i in versions[1]['items']}['run_method'], 'needed')              # 이전 버전은 그대로 보존된다
        self.assertEqual(self.scalar('SELECT author_user_id FROM handover_preparation_versions WHERE version = 2'), w['owner'])
        with self.assertRaises(DBAPIError):
            with self.db.begin_nested():
                self.db.execute(text("UPDATE handover_preparation_versions SET items = '{}'::jsonb"))
        self.db.rollback()

    def test_changing_the_target_scope_requires_reconfirmation_but_memo_changes_do_not(self):
        w = self.world(summary=False, review=False)
        svc.put_preparation(self.db, w['owner'], w['tid'], put_body(assignee=('ready', '이인수')))
        memo = svc.patch_target(self.db, w['owner'], w['tid'], HandoverTargetPatch(expected_version=1, purpose='새 목적'))
        self.assertEqual((memo['version'], memo['scope_version'], memo['needs_reconfirm']), (2, 1, False))
        scope = svc.patch_target(self.db, w['owner'], w['tid'], HandoverTargetPatch(expected_version=2, selected_paths=['main.py', 'src/b.py']))
        self.assertEqual((scope['version'], scope['scope_version'], scope['needs_reconfirm']), (3, 3, True))
        prep = svc.get_preparation(self.db, w['owner'], w['tid'])
        self.assertTrue(prep['needs_reconfirm'])                                                                # 이전 준비 정보를 새 대상의 확인으로 취급하지 않는다
        self.assertEqual({i['key']: i['text'] for i in prep['items']}['assignee'], '이인수')                        # 입력은 보존된다
        stale = self.code(svc.put_preparation, self.db, w['owner'], w['tid'], put_body(expected=1, target_version=1, assignee=('ready', '이인수')))
        self.assertEqual((stale.code, stale.details['reason'], stale.details['current_target_version']), (ErrorCode.VERSION_CONFLICT, 'TARGET_CHANGED', 3))
        again = svc.put_preparation(self.db, w['owner'], w['tid'], put_body(expected=1, target_version=3, assignee=('ready', '이인수')))
        self.assertEqual((again['needs_reconfirm'], again['target_version'], again['version']), (False, 3, 2))
        self.assertFalse(svc.get_target(self.db, w['owner'], w['tid'])['needs_reconfirm'])

    def test_other_owners_cannot_see_or_change_anything(self):
        w = self.world(summary=False, review=False)
        svc.put_preparation(self.db, w['owner'], w['tid'], put_body(assignee=('needed', None)))
        stranger = self.make_user()
        calls = [(svc.get_target, ()), (svc.get_preparation, ()), (svc.list_preparation_versions, ()), (svc.get_document, ()), (svc.list_generations, ()),
                 (svc.delete_target, ()),
                 (svc.put_preparation, (put_body(expected=1),)), (svc.patch_target, (HandoverTargetPatch(expected_version=1, name='x'),)),
                 (svc.generate, (HandoverGenerateRequest(expected_target_version=1, expected_preparation_version=1),))]
        for fn, extra in calls:
            exc = self.code(fn, self.db, stranger, w['tid'], *extra)
            self.assertEqual(exc.code, ErrorCode.RESOURCE_NOT_FOUND, fn.__name__)
        self.assertEqual(svc.list_targets(self.db, stranger, w['repo']), [])
        self.assertEqual(svc.get_target(self.db, w['owner'], w['tid'])['name'], '로그인 기능 인계')


class Targets(Case):
    def test_validation_limits_and_snapshot_of_the_same_repository_only(self):
        owner, repo = self.make_user(), self.make_repo()
        for bad in (['../etc/passwd'], ['/abs/path'], ['a//b'], ['a/./b'], ['ok.py', ''], ['x' * 501], ['a\x00b.py'], ['a/../b']):
            exc = self.code(svc.create_target, self.db, owner, HandoverTargetCreate(repository_id=repo, name='n', selected_paths=bad))
            self.assertEqual(exc.code, ErrorCode.INVALID_SELECTION, bad)
        with self.assertRaises(ValidationError):
            HandoverTargetCreate(repository_id=repo, name='n', selected_paths=[])
        with self.assertRaises(ValidationError):
            HandoverTargetCreate(repository_id=repo, name='n', selected_paths=[f'f{i}.py' for i in range(51)])
        foreign = self.snapshot(self.make_repo())[0]
        exc = self.code(svc.create_target, self.db, owner, HandoverTargetCreate(repository_id=repo, name='n', selected_paths=['a.py'], snapshot_id=foreign))
        self.assertEqual(exc.code, ErrorCode.INVALID_SELECTION)
        target = svc.create_target(self.db, owner, HandoverTargetCreate(repository_id=repo, name=' 이름 ', selected_paths=['./a.py', 'a.py', 'b\\c.py']))
        self.assertEqual((target['name'], target['selected_paths'], target['version'], target['scope_version']), ('이름', ['a.py', 'b/c.py'], 1, 1))
        with patch.object(svc, 'MAX_TARGETS_PER_OWNER', 1):
            exc = self.code(svc.create_target, self.db, owner, HandoverTargetCreate(repository_id=repo, name='n2', selected_paths=['a.py']))
        self.assertEqual(exc.code, ErrorCode.USER_LIMIT)

    def test_patch_conflicts_noops_and_delete_cascade(self):
        w = self.world()
        same = svc.patch_target(self.db, w['owner'], w['tid'], HandoverTargetPatch(expected_version=1, name='로그인 기능 인계', selected_paths=['main.py', 'src/a.py']))
        self.assertEqual(same['version'], 1)                                                                    # 바뀐 것이 없으면 버전을 올리지 않는다
        exc = self.code(svc.patch_target, self.db, w['owner'], w['tid'], HandoverTargetPatch(expected_version=5, name='x'))
        self.assertEqual((exc.code, exc.details['current_version']), (ErrorCode.VERSION_CONFLICT, 1))
        snap = self.snapshot(w['repo'])[0]
        changed = svc.patch_target(self.db, w['owner'], w['tid'], HandoverTargetPatch(expected_version=1, snapshot_id=snap))
        self.assertEqual((changed['snapshot_id'], changed['version'], changed['scope_version']), (snap, 2, 2))
        cleared = svc.patch_target(self.db, w['owner'], w['tid'], HandoverTargetPatch(expected_version=2, clear_snapshot=True))
        self.assertEqual((cleared['snapshot_id'], cleared['scope_version']), (None, 3))
        self.generate(w)
        svc.delete_target(self.db, w['owner'], w['tid'])
        self.assertEqual(self.scalar('SELECT count(*) FROM handover_documents') + self.scalar('SELECT count(*) FROM handover_generations')
                         + self.scalar('SELECT count(*) FROM handover_preparation_versions'), 0)
        self.assertEqual(self.scalar('SELECT count(*) FROM ai_results WHERE id = :r', r=w['review']), 1)           # 원본 AI 결과는 건드리지 않는다


class Generation(Case):
    def test_draft_pins_versions_and_never_turns_unverified_content_into_fact(self):
        w = self.world(snapshot=True)
        svc.put_preparation(self.db, w['owner'], w['tid'], put_body(assignee=('ready', '이인수'), run_method=('needed', None)))
        out = self.generate(w)
        g, md = out['generation'], out['generation']['markdown']
        self.assertEqual((g['number'], g['target_version'], g['preparation_version'], g['template_version'], g['reused']), (1, 1, 1, 'handover-v1', False))
        s = g['sources']
        self.assertEqual((s['target']['version'], s['preparation']['version'], s['summary']['result_id'], s['review']['result_id'], s['history']['status']),
                         (1, 1, str(w['summary']), str(w['review']), 'ok'))
        self.assertTrue(all(s[k]['pr']['head_sha'] and s[k]['pr']['number'] for k in ('summary', 'review')))      # 입력·PR 기준 고정
        self.assertEqual(len(s['review']['feedback_digest']), 64)
        for caveat in ('CAVEAT_AI', 'CAVEAT_UNKNOWN', 'CAVEAT_READY', 'CAVEAT_LINK', 'CAVEAT_HISTORY'):
            self.assertIn(handover_draft.T[caveat], md)
        # 준비 상태: 입력하지 않은 항목은 미확인이고 준비 필요/완료로 바뀌지 않는다
        prep_part = md.split('## 6.')[1].split('## 7.')[0]
        lines = {l.split('** — ')[0].replace('- **', ''): l.split('** — ')[1] for l in prep_part.splitlines() if l.startswith('- **')}
        self.assertEqual(lines['인수 담당자'], '준비 완료 (작성자 기록)')
        self.assertEqual(lines['실행 방법'], '준비 필요')
        self.assertEqual({lines[k] for k in ('기능 설명', '수정 후 확인 절차', '공동 검토', '인수자 실행 확인')}, {'미확인'})
        # AI 설명·지적은 출처와 검증 상태를 함께, 대상 파일 밖 내용은 제외
        summary_part = md.split('## 4.')[1].split('## 5.')[0]
        self.assertIn(handover_draft.T['SUMMARY_UNVERIFIED'], summary_part)
        self.assertIn('b를 20으로 변경', summary_part)
        self.assertNotIn('대상 밖 파일', md)
        findings_part = md.split('## 5.')[1].split('## 6.')[0]
        self.assertIn(handover_draft.T['FINDINGS_UNVERIFIED_HEAD'], findings_part)
        self.assertEqual(findings_part.count('finding '), 2)
        self.assertIn(handover_draft.T['FINDINGS_OMITTED'].format(count=1), findings_part)                         # other.py 지적 1건은 포함하지 않았다고 밝힌다
        for forbidden in FORBIDDEN:
            self.assertNotIn(forbidden, md)
        self.assertEqual(markdown_safety.validate_markdown(md), [])

    def test_verdicts_are_attributed_to_the_user_and_false_positives_are_not_listed_as_open_findings(self):
        w = self.world()
        self.feedback(w['review'], w['owner'], 'valid', ordinal=1)
        self.feedback(w['review'], w['owner'], 'false_positive', ordinal=2)
        md = self.generate(w)['generation']['markdown']
        part = md.split('## 5.')[1].split('## 6.')[0]
        valid, false = part.split('**' + handover_draft.T['FINDINGS_VALID_HEAD'] + '**')[1], part.split('**' + handover_draft.T['FINDINGS_FALSE_HEAD'] + '**')[1]
        self.assertIn('finding 1', valid.split('**')[0])
        self.assertIn('finding 2', false)
        self.assertNotIn('**' + handover_draft.T['FINDINGS_UNVERIFIED_HEAD'] + '**', part)                          # 남은 미검증 지적이 없다
        self.assertIn('사용자의 판단이며 GitViz가 확인한 것은 아닙니다', part)
        questions = md.split('## 7.')[1].split('## 8.')[0]
        self.assertNotIn('AI 지적', questions)                                                                     # 검증이 끝난 지적은 묻지 않는다

    def test_open_findings_become_questions_and_checklist_items_not_conclusions(self):
        w = self.world()
        md = self.generate(w)['generation']['markdown']
        questions = md.split('## 7.')[1].split('## 8.')[0]
        self.assertEqual(questions.count('AI 지적'), 2)
        self.assertIn('현재 상태가 입력되지 않았습니다', questions)
        checklist = md.split('## 8.')[1].split('## 9.')[0]
        self.assertNotIn('[x]', checklist)                                                                         # 아무것도 준비 완료로 기록되지 않았다
        self.assertIn(handover_draft.T['CHECK_AI'].format(count=2), checklist)

    def test_missing_sources_say_nothing_was_confirmed_instead_of_filling_gaps(self):
        w = self.world(summary=False, review=False)
        md = self.generate(w, summary=False, review=False)['generation']['markdown']
        for key in ('NO_HISTORY_SNAPSHOT', 'SUMMARY_NONE', 'FINDINGS_NONE', 'PREP_NONE'):
            self.assertIn(handover_draft.T[key], md)
        w2 = self.world(findings=())
        md2 = self.generate(w2, summary=False)['generation']['markdown']
        self.assertIn(handover_draft.T['FINDINGS_EMPTY'], md2)
        self.assertIn('문제가 없다는 뜻이 아닙니다', md2)

    def test_history_comes_only_from_the_verified_snapshot_and_excluded_ones_carry_no_numbers(self):
        w = self.world(snapshot=True, findings=())
        _, payload = self.snapshot(w['repo'])
        md = self.generate(w, summary=False, review=False)['generation']['markdown']
        part = md.split('## 2.')[1].split('## 3.')[0]
        risk = payload['risk-scores']['files'][0]
        self.assertIn(f"변경 {risk['change_freq']}회, 변경량 {risk['churn']}, 버그 수정 {risk['bugfix_count']}회", part)       # snapshot의 저장된 값 그대로
        self.assertIn('관측 12건, 기여자 3명', part)
        self.assertIn(f"커밋 {payload['data_quality']['commit_count']}건 중 작성자 미식별 {payload['data_quality']['unidentified_commits']}건", part)
        co = md.split('## 3.')[1].split('## 4.')[0]
        self.assertIn('tests/test_main.py', co)
        self.assertIn('실제 의존 관계나 영향 전파를 뜻하지 않습니다', co)
        self.assertIn('표본이 부족합니다', part)                                                                      # 관측이 적은 파일은 한계 문구가 함께 나온다
        bad = self.world(snapshot=False, findings=())
        sid, _ = self.snapshot(bad['repo'], formula='old-formula')
        svc.patch_target(self.db, bad['owner'], bad['tid'], HandoverTargetPatch(expected_version=1, snapshot_id=sid))
        out = self.generate(bad, summary=False, review=False)
        md2 = out['generation']['markdown']
        self.assertIn('지원하지 않는 계산 버전', md2)
        part2 = md2.split('## 2.')[1].split('## 3.')[0]
        self.assertIn(handover_draft.T['HISTORY_EXCLUDED'].split('{')[0], part2)
        self.assertNotIn('변경 ', part2.replace('변경량', ''))                                                      # 검증하지 못한 snapshot의 수치는 쓰지 않는다
        self.assertEqual(out['generation']['sources']['history']['status'], 'excluded')

    def test_same_basis_reuses_and_force_makes_a_new_original_without_touching_the_edit(self):
        w = self.world()
        first = self.generate(w)
        again = self.generate(w)
        self.assertEqual((again['generation']['generation_id'], again['generation']['reused'], again['document_updated']), (first['generation']['generation_id'], True, False))
        self.assertEqual(self.scalar('SELECT count(*) FROM handover_generations'), 1)
        forced = self.generate(w, force=True)
        self.assertEqual((forced['generation']['number'], forced['generation']['reused']), (2, False))
        self.assertEqual(forced['generation']['markdown'], first['generation']['markdown'])                       # 같은 기준에서는 같은 문서가 만들어진다

    def test_generation_conflicts_on_changed_target_or_preparation(self):
        w = self.world(summary=False, review=False)
        exc = self.code(svc.generate, self.db, w['owner'], w['tid'], HandoverGenerateRequest(expected_target_version=9, expected_preparation_version=0))
        self.assertEqual((exc.code, exc.details['reason']), (ErrorCode.VERSION_CONFLICT, 'TARGET_CHANGED'))
        exc = self.code(svc.generate, self.db, w['owner'], w['tid'], HandoverGenerateRequest(expected_target_version=1, expected_preparation_version=3))
        self.assertEqual((exc.code, exc.details['reason']), (ErrorCode.VERSION_CONFLICT, 'PREPARATION_CHANGED'))
        self.assertEqual(self.scalar('SELECT count(*) FROM handover_generations'), 0)

    def test_sources_must_be_the_owners_same_repository_unexpired_results_of_the_right_kind(self):
        w = self.world()
        stranger = self.make_user()
        other_repo = self.make_repo()
        foreign_owner = self.result_for(stranger, other_repo, 'review', ['main.py'])
        req = lambda **kw: HandoverGenerateRequest(expected_target_version=1, expected_preparation_version=0, **kw)   # noqa: E731
        self.assertEqual(self.code(svc.generate, self.db, w['owner'], w['tid'], req(review_result_id=foreign_owner)).code, ErrorCode.RESOURCE_NOT_FOUND)
        own_other_repo = self.result_for(w['owner'], other_repo, 'review', ['main.py'])
        self.assertEqual(self.code(svc.generate, self.db, w['owner'], w['tid'], req(review_result_id=own_other_repo)).code, ErrorCode.INVALID_SELECTION)
        self.assertEqual(self.code(svc.generate, self.db, w['owner'], w['tid'], req(review_result_id=w['summary'])).code, ErrorCode.INVALID_SELECTION)
        self.assertEqual(self.code(svc.generate, self.db, w['owner'], w['tid'], req(summary_result_id=w['review'])).code, ErrorCode.INVALID_SELECTION)
        self.assertEqual(self.code(svc.generate, self.db, w['owner'], w['tid'], req(review_result_id=uuid4())).code, ErrorCode.RESOURCE_NOT_FOUND)
        self.bypass("UPDATE ai_results SET expires_at = now() - interval '1 minute' WHERE id = :r", r=w['review'])
        self.assertEqual(self.code(svc.generate, self.db, w['owner'], w['tid'], req(review_result_id=w['review'])).code, ErrorCode.RESULT_EXPIRED)
        self.assertEqual(self.scalar('SELECT count(*) FROM handover_generations'), 0)

    def test_feedback_prep_and_target_changes_after_generation_mark_it_stale_without_rewriting_it(self):
        w = self.world()
        out = self.generate(w)
        gid, original = out['generation']['generation_id'], out['generation']['markdown']
        view, _ = svc.get_generation(self.db, w['owner'], gid)
        self.assertEqual(view['stale_reasons'], [])
        self.feedback(w['review'], w['owner'], 'false_positive', ordinal=1)                                       # 생성 뒤 피드백이 바뀜
        view, _ = svc.get_generation(self.db, w['owner'], gid)
        self.assertEqual(view['stale_reasons'], ['feedback_changed'])
        self.assertEqual(view['markdown'], original)                                                              # 옛 원본은 그대로(기준이 달라졌다고만 표시)
        self.assertEqual(svc.get_document(self.db, w['owner'], w['tid'])['stale_reasons'], ['feedback_changed'])
        svc.put_preparation(self.db, w['owner'], w['tid'], put_body(assignee=('needed', None)))
        svc.patch_target(self.db, w['owner'], w['tid'], HandoverTargetPatch(expected_version=1, purpose='바뀐 목적'))
        view, _ = svc.get_generation(self.db, w['owner'], gid)
        self.assertEqual(sorted(view['stale_reasons']), ['feedback_changed', 'preparation_changed', 'target_changed'])
        fresh = self.generate(w)['generation']                                                                    # 새 기준으로 다시 만들면 새 피드백이 반영된다
        self.assertEqual((fresh['number'], fresh['stale_reasons']), (2, []))
        self.assertIn(handover_draft.T['FINDINGS_FALSE_HEAD'], fresh['markdown'])
        self.bypass("UPDATE ai_results SET expires_at = now() - interval '1 minute' WHERE id = :r", r=w['summary'])
        view, _ = svc.get_generation(self.db, w['owner'], gid)
        self.assertIn('source_missing', view['stale_reasons'])

    def test_generation_originals_are_immutable_and_the_audit_log_has_no_bodies(self):
        w = self.world()
        svc.put_preparation(self.db, w['owner'], w['tid'], put_body(assignee=('ready', '비밀스러운 담당자 메모')))
        self.generate(w)
        with self.assertRaises(DBAPIError):
            with self.db.begin_nested():
                self.db.execute(text("UPDATE handover_generations SET markdown = 'x'"))
        self.db.rollback()
        details = self.scalar("SELECT string_agg(details::text, ' ') FROM audit_events WHERE action LIKE 'handover.%'")
        self.assertNotIn('비밀스러운', details)
        self.assertNotIn('main.py', details)
        self.assertEqual(self.scalar("SELECT count(*) FROM audit_events WHERE action = 'handover.generated'"), 1)

    def test_generation_count_limit(self):
        w = self.world(summary=False, review=False)
        with patch.object(svc, 'MAX_GENERATIONS_PER_TARGET', 2):
            self.generate(w, summary=False, review=False)
            self.generate(w, summary=False, review=False, force=True)
            exc = self.code(svc.generate, self.db, w['owner'], w['tid'], HandoverGenerateRequest(expected_target_version=1, expected_preparation_version=0, force=True))
        self.assertEqual(exc.code, ErrorCode.USER_LIMIT)


class Editing(Case):
    def doc(self, w):
        return svc.get_document(self.db, w['owner'], w['tid'])

    def test_edits_are_separate_from_the_original_and_regeneration_keeps_them(self):
        w = self.world()
        out = self.generate(w)
        doc = self.doc(w)
        self.assertEqual((doc['version'], doc['edited'], doc['markdown']), (1, False, out['generation']['markdown']))
        edited = svc.put_document(self.db, w['owner'], doc['document_id'], HandoverDocumentPut(expected_version=1, markdown=doc['markdown'] + '\n## 내 메모\n\n직접 확인한 내용\n'))
        self.assertEqual((edited['version'], edited['edited']), (2, True))
        original = self.scalar('SELECT markdown FROM handover_generations WHERE number = 1')
        self.assertEqual(original, out['generation']['markdown'])                                                 # 생성 원본은 그대로
        self.assertIn('직접 확인한 내용', edited['markdown'])
        # 피드백이 바뀐 뒤 강제 재생성: 편집본은 덮어쓰지 않는다
        self.feedback(w['review'], w['owner'], 'false_positive', ordinal=1)
        regen = self.generate(w, force=True)
        self.assertEqual((regen['generation']['number'], regen['document_updated']), (2, False))
        kept = self.doc(w)
        self.assertEqual((kept['markdown'], kept['version'], kept['base_generation_number'], kept['newer_generation_number']), (edited['markdown'], 2, 1, 2))
        self.assertIn('feedback_changed', kept['stale_reasons'])                                                  # 편집본의 기준이 옛 것임을 표시
        # 사용자가 명시적으로 새 원본을 가져오면 문서가 바뀌고, 편집한 내용은 편집 이력에 남는다
        adopted = svc.adopt_generation(self.db, w['owner'], kept['document_id'], HandoverAdoptRequest(expected_version=2, generation_id=regen['generation']['generation_id']))
        self.assertEqual((adopted['version'], adopted['edited'], adopted['base_generation_number'], adopted['newer_generation_id']), (3, False, 2, None))
        self.assertEqual(adopted['markdown'], regen['generation']['markdown'])
        history = {r['version']: (r['kind'], r['markdown']) for r in self.db.execute(text(
            'SELECT version, kind, markdown FROM handover_document_versions ORDER BY version')).mappings()}
        self.assertEqual({v: k for v, (k, _) in history.items()}, {1: 'generated', 2: 'edited', 3: 'adopted'})
        self.assertEqual(history[2][1], edited['markdown'])

    def test_an_unedited_document_follows_new_originals(self):
        w = self.world()
        self.generate(w)
        svc.put_preparation(self.db, w['owner'], w['tid'], put_body(assignee=('ready', '이인수')))
        second = self.generate(w)
        self.assertTrue(second['document_updated'])
        doc = self.doc(w)
        self.assertEqual((doc['version'], doc['edited'], doc['base_generation_number'], doc['markdown']), (2, False, 2, second['generation']['markdown']))

    def test_document_version_conflicts_and_unchanged_saves(self):
        w = self.world()
        self.generate(w)
        doc = self.doc(w)
        exc = self.code(svc.put_document, self.db, w['owner'], doc['document_id'], HandoverDocumentPut(expected_version=7, markdown='x'))
        self.assertEqual((exc.code, exc.details['reason'], exc.details['current_version']), (ErrorCode.VERSION_CONFLICT, 'DOCUMENT_VERSION', 1))
        same = svc.put_document(self.db, w['owner'], doc['document_id'], HandoverDocumentPut(expected_version=1, markdown=doc['markdown']))
        self.assertEqual((same['version'], same['edited']), (1, False))                                          # 바뀐 것이 없으면 편집으로 치지 않는다
        svc.put_document(self.db, w['owner'], doc['document_id'], HandoverDocumentPut(expected_version=1, markdown='새 본문\r\n두 번째 줄'))
        self.assertEqual(self.doc(w)['markdown'], '새 본문\n두 번째 줄')                                           # 줄바꿈 정규화
        results = []

        def run(label):
            with S.Session() as db:
                try:
                    svc.put_document(db, w['owner'], doc['document_id'], HandoverDocumentPut(expected_version=2, markdown=f'동시 편집 {label}'))
                    results.append('ok')
                except AIError as e:
                    results.append(e.code)
        threads = [threading.Thread(target=run, args=(i,)) for i in range(4)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual((results.count('ok'), results.count(ErrorCode.VERSION_CONFLICT)), (1, 3), results)
        with patch.object(svc, 'MAX_DOCUMENT_VERSIONS', 3):
            for i in range(5):
                cur = self.doc(w)['version']
                svc.put_document(self.db, w['owner'], doc['document_id'], HandoverDocumentPut(expected_version=cur, markdown=f'버전 {i}'))
        self.assertLessEqual(self.scalar('SELECT count(*) FROM handover_document_versions'), 3)

    def test_unsafe_markdown_cannot_be_saved(self):
        w = self.world()
        self.generate(w)
        doc = self.doc(w)
        cases = {'<script>alert(1)</script>': 'raw_html', '<img src=x onerror=alert(1)>': 'raw_html', '[a](javascript:alert(1))': 'unsafe_link',
                 '[a](data:text/html;base64,AAAA)': 'unsafe_link', '[a](&#106;avascript:alert(1))': 'unsafe_link', '[r]: vbscript:x': 'unsafe_link',
                 '[a](//evil.example/x)': 'unsafe_link', '![t](https://tracker.example/p.png)': 'image', 'a\x00b': 'control_chars', '‮gnp.exe': 'hidden_or_bidi_chars',
                 'x' * 4001: 'line_too_long', '<!-- 숨은 주석 -->': 'raw_html',
                 '\\`<script>x</script>\\`': 'raw_html',                                  # 이스케이프된 백틱은 코드 span을 열지 못한다
                 '\\\\<b>x</b>': 'raw_html', '`a\\` <b>x</b> `': 'raw_html',             # 코드 span 안의 백슬래시는 이스케이프가 아니다
                 # CommonMark: 백틱 블록의 정보 문자열에 백틱이 있으면 코드 블록이 아니다
                 '```bad`info\n<img src=x onerror=alert(1)>': 'raw_html',
                 '1. 항목\n   ```\n<img src=x onerror=alert(1)>': 'raw_html',           # 목록 안의 들여쓴 블록은 컨테이너가 끝나면 닫힌다
                 ' ```\n<img src=x onerror=alert(1)>\n```': 'raw_html',                  # 들여쓴 여는 줄은 코드로 보지 않는다(애매하면 검사한다)
                 '```\nx\n```\n<b>y</b>': 'raw_html',                                   # 닫힌 뒤의 HTML
                 # 블록 구분이 인라인 해석보다 먼저다: 줄을 넘는 백틱 사이에도 HTML 블록·제목이 시작될 수 있다
                 '`\n<img src=x onerror=alert(1)>\n`': 'raw_html',
                 '`\n# 제목\n<div onclick=alert(1)>x</div>\n`': 'raw_html',
                 '`a\n\n<b>x</b>\n\n`': 'raw_html',
                 '| `a | <b>x</b>` |': 'raw_html'}                                      # 표 셀은 코드 span보다 먼저 `|`로 나뉜다
        for markdown, issue in cases.items():
            exc = self.code(svc.put_document, self.db, w['owner'], doc['document_id'], HandoverDocumentPut(expected_version=1, markdown=markdown))
            self.assertEqual(exc.code, ErrorCode.INVALID_SELECTION, markdown[:20])
            self.assertIn(issue, exc.details['issues'], markdown[:20])
        for fine in ('```\n<script>alert(1)</script> [a](javascript:x)\n```', '`<b>` 인라인 코드', '[문서](https://wiki.example/doc) [메일](mailto:a@b.c) [앵커](#top) [상대](docs/a.md)',
                     '\\<script\\> 이스케이프된 꺾쇠', '```js\n<b>x</b>\n```\n본문', '~~~ a`b\n<b>x</b>\n~~~', '````\n<b>\n```\n<i>\n````',
                     '```\n<b>\n``` x\n<i>', '한 줄 `<b>x</b>` 코드', '`a\nb` 여러 줄이어도 HTML이 없으면 안전'):                                        # 닫는 줄 뒤에 글자가 있으면 닫히지 않는다(끝까지 코드)
            self.assertEqual(markdown_safety.validate_markdown(fine), [], fine)
        self.assertEqual(self.doc(w)['version'], 1)                                                              # 거절된 내용은 저장되지 않는다


class Safety(Case):
    def test_hostile_user_and_ai_text_is_neutralized_in_the_generated_draft(self):
        titles = [(f'main.py', h) for h in HOSTILE]
        owner, repo = self.make_user(), self.make_repo()
        review = self.result_for(owner, repo, 'review', titles, pr_number=7)
        summary = self.result_for(owner, repo, 'summary', [], pr_number=8, payload={
            'overview': '\n'.join(HOSTILE), 'author_intent': HOSTILE[0], 'unknowns': HOSTILE[:3],
            'file_changes': [{'path': 'main.py', 'file_id': str(uuid4()), 'summary': '\n'.join(HOSTILE), 'evidence_ids': []}]})
        exc = self.code(svc.create_target, self.db, owner, HandoverTargetCreate(repository_id=repo, name='‮evil', selected_paths=['main.py']))
        self.assertEqual(exc.code, ErrorCode.INVALID_SELECTION)                                                  # 방향 제어 문자는 입력 단계에서 거절한다
        target = svc.create_target(self.db, owner, HandoverTargetCreate(
            repository_id=repo, name=HOSTILE_USER[0] + HOSTILE_USER[1], selected_paths=['main.py', 'a`b<c>.py'], purpose='\n'.join(HOSTILE_USER),
            giver_name=HOSTILE_USER[2], receiver_name=HOSTILE_USER[3]))
        tid = target['target_id']
        svc.put_preparation(self.db, owner, tid, HandoverPreparationPut(expected_version=0, confirmed_target_version=1, items={
            'assignee': HandoverItemIn(status='ready', text='\n'.join(HOSTILE_USER)), 'description': HandoverItemIn(status='needed', text=HOSTILE[1], link='https://wiki.example/a?x=1&y=<2>'
                                                                                                        .replace('<2>', '2')),
            'run_method': HandoverItemIn(status='needed', text='```\n<script>x</script>\n```')}))
        out = svc.generate(self.db, owner, tid, HandoverGenerateRequest(expected_target_version=1, expected_preparation_version=1, summary_result_id=summary, review_result_id=review))
        md = out['generation']['markdown']
        self.assertEqual(markdown_safety.validate_markdown(md), [])
        self.assertNotIn('<script', md.replace('\\<script', ''))
        self.assertNotIn('‮', md)
        self.assertNotRegex(md, r'(?m)^#{1,6} (?!인수인계 초안|\d\.)')                                              # 입력한 문장이 제목이 되지 않는다
        self.assertNotIn('](javascript', md.replace('\\](javascript', ''))
        self.assertNotRegex(md, r'(?<!\\)!\[')
        self.assertIn('https://wiki.example/a?x=1&y=2', md)                                                       # 링크는 그대로 보이되 코드 span 안에만 있다
        for line in md.splitlines():
            if 'https://wiki.example' in line:
                self.assertIn('`https://wiki.example', line)

    def test_export_adds_provenance_uses_a_safe_filename_and_revalidates(self):
        w = self.world()
        out = self.generate(w)
        doc = svc.get_document(self.db, w['owner'], w['tid'])
        svc.put_document(self.db, w['owner'], doc['document_id'], HandoverDocumentPut(expected_version=1, markdown=doc['markdown'] + '\n내가 추가한 문장\n'))
        filename, content = svc.export_markdown(self.db, w['owner'], doc['document_id'])
        self.assertRegex(filename, r'^handover-[0-9a-f]{8}-v2\.md$')                                              # 사용자 입력이 파일 이름에 들어가지 않는다
        self.assertTrue(content.startswith(doc['markdown'].rstrip('\n')))
        self.assertIn('내가 추가한 문장', content)
        footer = content.split('\n---\n')[-1]
        self.assertIn('AI가 만든 내용과 \'미확인\' 항목은 확정 사실이 아닙니다', footer)
        self.assertIn('생성 원본 #1 기준', footer)
        self.assertEqual(markdown_safety.validate_markdown(content), [])
        self.assertEqual(self.scalar("SELECT count(*) FROM audit_events WHERE action = 'handover.document_exported'"), 1)
        self.bypass("UPDATE handover_documents SET markdown = '<script>alert(1)</script>' WHERE id = :d", d=doc['document_id'])   # DB가 직접 오염된 경우
        exc = self.code(svc.export_markdown, self.db, w['owner'], doc['document_id'])
        self.assertEqual((exc.code, exc.details['issues']), (ErrorCode.INVALID_STATE, ['raw_html']))
        stranger = self.make_user()
        self.assertEqual(self.code(svc.export_markdown, self.db, stranger, doc['document_id']).code, ErrorCode.RESOURCE_NOT_FOUND)

    def test_markdown_helpers(self):
        for text_ in HOSTILE:
            self.assertEqual(markdown_safety.validate_markdown(markdown_safety.escape_inline(text_)), [], text_)
            self.assertEqual(markdown_safety.validate_markdown('\n'.join(markdown_safety.quote_block(text_ + '\n' + text_))), [], text_)
            self.assertEqual(markdown_safety.validate_markdown(markdown_safety.code_span(text_)), [], text_)
        self.assertEqual(markdown_safety.escape_inline('a' * 600, limit=10), 'a' * 9 + '…')
        self.assertNotIn('\n', markdown_safety.escape_inline('a\nb\r\nc'))
        self.assertEqual(markdown_safety.code_span('x`y'), '``x`y``')
        piped = markdown_safety.code_span('a|b<c>.py')                                                     # `|`가 든 값은 표 셀로 읽히지 않게 코드 span을 쓰지 않는다
        self.assertEqual((piped, markdown_safety.validate_markdown(piped)), ('a&#124;b\\<c\\>.py', []))

    def test_fixed_wording_is_pinned_and_free_of_confirmation_phrases(self):
        self.assertEqual(handover_draft.text_digest(), handover_draft.TEXT_DIGEST)                                 # 문구를 바꾸면 새 버전 파일을 만든다
        for key, value in handover_draft.T.items():
            for forbidden in FORBIDDEN:
                self.assertNotIn(forbidden, value, key)


class Api(Case):
    def setUp(self):
        super().setUp()
        self.deny = None

        def authorize(db, user_id, repo_id, action, policy):
            if self.deny:
                raise self.deny
            return None, {'private': False}
        self._patch = patch.object(jobs_api, 'authorize_ai', side_effect=authorize)
        self._patch.start()
        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key='api-test', same_site='lax')
        app.add_exception_handler(AIError, ai_error_response)
        app.add_exception_handler(RequestValidationError, ai_validation_error_response)
        for module in (auth_router, handover_api):
            app.include_router(module.router)

        @app.post('/test/login/{user_id}')
        async def login(user_id: str, request: Request):
            request.session['user_id'] = user_id
            return {}
        self.client = TestClient(app)

    def tearDown(self):
        self._patch.stop()
        self.client.close()
        super().tearDown()

    def login(self, user):
        self.client.post(f'/test/login/{user}')
        self.token = self.client.get('/api/auth/csrf').json()['token']

    def h(self):
        return {'Origin': ORIGIN, 'X-CSRF-Token': self.token}

    def err(self, r, status, code):
        self.assertEqual(r.status_code, status, r.text)
        self.assertEqual(r.json()['error']['code'], code)
        self.assertEqual(r.headers['cache-control'], 'private, no-store')
        return r.json()['error']

    def test_end_to_end_flow_and_http_contract(self):
        w = self.world()
        self.login(w['owner'])
        base = '/api/ai/v1'
        created = self.client.post(f'{base}/handover-targets', headers=self.h(), json={'repository_id': w['repo'], 'name': 'API 대상', 'selected_paths': ['main.py']})
        self.assertEqual(created.status_code, 201, created.text)
        tid = created.json()['target_id']
        self.assertIn(tid, [t['target_id'] for t in self.client.get(f'{base}/handover-targets', params={'repository_id': w['repo']}).json()['targets']])
        prep = self.client.get(f'{base}/handover-targets/{tid}/preparation').json()
        self.assertEqual((prep['version'], {i['status'] for i in prep['items']}), (0, {'unknown'}))
        saved = self.client.put(f'{base}/handover-targets/{tid}/preparation', headers=self.h(), json={
            'expected_version': 0, 'confirmed_target_version': 1, 'items': {'assignee': {'status': 'ready', 'text': '이인수'}}})
        self.assertEqual((saved.status_code, saved.json()['version']), (200, 1))
        self.err(self.client.put(f'{base}/handover-targets/{tid}/preparation', headers=self.h(), json={
            'expected_version': 0, 'confirmed_target_version': 1, 'items': {}}), 409, 'VERSION_CONFLICT')
        gen = self.client.post(f'{base}/handover-targets/{tid}/generations', headers=self.h(), json={
            'expected_target_version': 1, 'expected_preparation_version': 1, 'review_result_id': str(w['review'])})
        self.assertEqual(gen.status_code, 201, gen.text)
        body = gen.json()
        self.assertEqual((body['generation']['number'], body['document_updated']), (1, True))
        again = self.client.post(f'{base}/handover-targets/{tid}/generations', headers=self.h(), json={
            'expected_target_version': 1, 'expected_preparation_version': 1, 'review_result_id': str(w['review'])})
        self.assertEqual((again.status_code, again.json()['generation']['reused']), (200, True))
        doc = self.client.get(f'{base}/handover-targets/{tid}/document').json()
        edit = self.client.put(f'{base}/handover-documents/{doc["document_id"]}', headers=self.h(), json={'expected_version': 1, 'markdown': doc['markdown'] + '\n메모\n'})
        self.assertEqual((edit.status_code, edit.json()['version'], edit.json()['edited']), (200, 2, True))
        self.err(self.client.put(f'{base}/handover-documents/{doc["document_id"]}', headers=self.h(), json={'expected_version': 1, 'markdown': 'x'}), 409, 'VERSION_CONFLICT')
        self.err(self.client.put(f'{base}/handover-documents/{doc["document_id"]}', headers=self.h(), json={'expected_version': 2, 'markdown': '<script>1</script>'}), 422, 'INVALID_SELECTION')
        export = self.client.get(f'{base}/handover-documents/{doc["document_id"]}/export', params={'format': 'markdown'})
        self.assertEqual(export.status_code, 200)
        self.assertEqual(export.headers['content-type'], 'text/markdown; charset=utf-8')
        self.assertRegex(export.headers['content-disposition'], r'^attachment; filename="handover-[0-9a-f]{8}-v2\.md"$')
        self.assertEqual((export.headers['x-content-type-options'], export.headers['cache-control']), ('nosniff', 'private, no-store'))
        self.assertIn('메모', export.text)
        self.err(self.client.get(f'{base}/handover-documents/{doc["document_id"]}/export', params={'format': 'pdf'}), 422, 'INVALID_SELECTION')
        listed = self.client.get(f'{base}/handover-targets/{tid}/generations').json()['generations']
        self.assertEqual([g['number'] for g in listed], [1])
        got = self.client.get(f'{base}/handover-generations/{body["generation"]["generation_id"]}')
        self.assertEqual(got.status_code, 200)
        patched = self.client.patch(f'{base}/handover-targets/{tid}', headers=self.h(), json={'expected_version': 1, 'name': '바뀐 이름'})
        self.assertEqual((patched.status_code, patched.json()['version']), (200, 2))
        adopt = self.client.post(f'{base}/handover-documents/{doc["document_id"]}/adopt', headers=self.h(), json={
            'expected_version': 2, 'generation_id': body['generation']['generation_id']})
        self.assertEqual((adopt.status_code, adopt.json()['edited']), (200, False))
        deleted = self.client.delete(f'{base}/handover-targets/{tid}', headers=self.h())
        self.assertEqual((deleted.status_code, deleted.json()['status']), (200, 'deleted'))
        self.err(self.client.get(f'{base}/handover-targets/{tid}'), 404, 'RESOURCE_NOT_FOUND')

    def test_auth_csrf_ownership_validation_and_access_loss(self):
        w = self.world()
        base = '/api/ai/v1'
        tid = w['tid']
        anon = TestClient(self.client.app)
        self.err(anon.get(f'{base}/handover-targets/{tid}'), 401, 'AUTH_REQUIRED')
        self.login(w['owner'])
        self.err(self.client.post(f'{base}/handover-targets', json={'repository_id': w['repo'], 'name': 'n', 'selected_paths': ['a.py']}), 403, 'CSRF_FAILED')
        self.err(self.client.put(f'{base}/handover-targets/{tid}/preparation', json={'expected_version': 0, 'confirmed_target_version': 1, 'items': {}}), 403, 'CSRF_FAILED')
        for body in ({'repository_id': w['repo'], 'name': '', 'selected_paths': ['a.py']}, {'repository_id': w['repo'], 'name': 'n', 'selected_paths': []},
                     {'repository_id': w['repo'], 'name': 'n', 'selected_paths': ['a.py'], 'extra': 1}, {'repository_id': 0, 'name': 'n', 'selected_paths': ['a.py']}):
            self.err(self.client.post(f'{base}/handover-targets', headers=self.h(), json=body), 422, 'INVALID_SELECTION')
        doc_id = self.generate(w)['document_id']
        self.deny = AIError(ErrorCode.RESOURCE_NOT_FOUND, 'gone')                                                 # 저장소 접근 권한을 잃음
        for method, path, kw in (('get', f'/handover-targets/{tid}', {}), ('get', f'/handover-targets/{tid}/document', {}), ('get', f'/handover-documents/{doc_id}/export', {}),
                                 ('put', f'/handover-documents/{doc_id}', {'json': {'expected_version': 1, 'markdown': 'x'}, 'headers': self.h()})):
            self.err(getattr(self.client, method)(base + path, **kw), 404, 'RESOURCE_NOT_FOUND')
        self.err(self.client.get(f'{base}/handover-targets', params={'repository_id': w['repo']}), 404, 'RESOURCE_NOT_FOUND')
        self.deny = None
        self.login(self.make_user())                                                                              # 남의 자료
        self.err(self.client.get(f'{base}/handover-targets/{tid}'), 404, 'RESOURCE_NOT_FOUND')
        self.err(self.client.get(f'{base}/handover-documents/{doc_id}/export'), 404, 'RESOURCE_NOT_FOUND')
        self.err(self.client.put(f'{base}/handover-documents/{doc_id}', headers=self.h(), json={'expected_version': 1, 'markdown': 'x'}), 404, 'RESOURCE_NOT_FOUND')
        self.err(self.client.post(f'{base}/handover-targets/{tid}/generations', headers=self.h(), json={'expected_target_version': 1, 'expected_preparation_version': 0}),
                 404, 'RESOURCE_NOT_FOUND')
        self.assertEqual(self.client.get(f'{base}/handover-targets', params={'repository_id': w['repo']}).json()['targets'], [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
