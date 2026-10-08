"""S13 팀 테스트: 역할 권한, 팀 간 격리, 초대·탈퇴·저장소 권한 상실의 즉시 반영, 개인 자료 자동 공유 금지, 공유 해제,
보고서(원본 접근 권한·권한 축소 후 차단·생성 중 변경·문제 집계 규칙), 개인 결과 캐시와 팀 자료의 분리.
실제 PostgreSQL을 쓰고 모델·외부 호출은 없다(저장소 접근 확인은 시험 대역으로 바꿔 끼운다)."""
import json
import os
import sys
import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai_test_support as support  # noqa: E402

support.require_test_database()
sys.path.insert(0, str(support.ROOT))

import ai_input_support as S  # noqa: E402
import test_ai_handover as HO  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402
from starlette.middleware.sessions import SessionMiddleware  # noqa: E402

from app.api.ai import teams as teams_api  # noqa: E402
from app.api.ai.dependencies import ai_error_response, ai_validation_error_response  # noqa: E402
from app.routers import auth as auth_router  # noqa: E402
from app.schemas.ai import ERROR_HTTP_STATUS, ErrorCode, HandoverItemIn, HandoverPreparationPut, HandoverTargetCreate, HandoverTargetPatch  # noqa: E402
from app.schemas.ai_team import InviteRequest, ReportCreate, ShareCreate, TeamCreate  # noqa: E402
from app.services.ai import handover_service, markdown_safety, team_access, team_report_service as reports, team_service as svc  # noqa: E402
from app.services.ai.access import AIError  # noqa: E402

ORIGIN = 'http://localhost:8000'
FORBIDDEN = ('오류 없음', '문제 없음', '이상 없음', '버그 없음', '안전합니다')


def setUpModule():
    S.migrate()


class TeamCase(HO.Case):
    """팀·구성원·저장소 연결 helper. 저장소 접근 확인(GitHub)은 denied 집합으로 제어한다."""

    def setUp(self):
        super().setUp()
        self.denied = set()                  # (user, repo) 접근 불가
        self.unavailable = set()             # 접근 확인 장애

        def authorize(db, user_id, repo_id, action, policy):
            if (user_id, repo_id) in self.unavailable:
                raise AIError(ErrorCode.ACCESS_CHECK_UNAVAILABLE, 'down')
            if (user_id, repo_id) in self.denied:
                raise AIError(ErrorCode.RESOURCE_NOT_FOUND, 'gone')
            return None, {'private': False}
        self._access = patch.object(team_access, 'authorize_ai', side_effect=authorize)
        self._access.start()

    def tearDown(self):
        self._access.stop()
        self.db.rollback()
        with S.Session() as db:
            db.execute(text('DELETE FROM teams'))
            db.execute(text("DELETE FROM audit_events WHERE action LIKE 'team.%'"))
            db.commit()
        super().tearDown()

    # ── 데이터 ──
    def person(self, login):
        uid = self.make_user()
        self.db.execute(text('UPDATE users SET github_login = :l WHERE id = :u'), {'l': login, 'u': uid})
        self.db.commit()
        return uid

    def team(self, admin, name='팀 A'):
        return svc.create_team(self.db, admin, TeamCreate(name=name))['team_id']

    def join(self, tid, admin, user, login, role='member'):
        svc.invite(self.db, admin, tid, InviteRequest(github_login=login, role=role))
        svc.accept_invitation(self.db, user, tid)

    def link(self, tid, admin, repo):
        svc.add_repository(self.db, admin, tid, repo)

    def standard(self):
        """관리자 ann, 멤버 bo, 조회자 cy가 있는 팀 하나와 연결된 저장소 하나."""
        self.ann, self.bo, self.cy = self.person('ann'), self.person('bo'), self.person('cy')
        self.repo = self.make_repo()
        self.tid = self.team(self.ann)
        self.join(self.tid, self.ann, self.bo, 'bo', 'member')
        self.join(self.tid, self.ann, self.cy, 'cy', 'viewer')
        self.link(self.tid, self.ann, self.repo)
        return self.tid

    def review(self, owner, repo, titles=('a', 'b'), pr=7):
        return self.result_for(owner, repo, 'review', [('main.py', t) for t in titles], pr_number=pr)

    def share(self, user, tid, rid, kind='review_result'):
        return svc.create_share(self.db, user, tid, ShareCreate(resource_type=kind, resource_id=rid))[0]

    def code(self, fn, *a, **kw):
        with self.assertRaises(AIError) as ctx:
            fn(*a, **kw)
        self.db.rollback()
        return ctx.exception

    def epoch(self, tid):
        return self.scalar('SELECT visibility_epoch FROM teams WHERE id = :t', t=tid)

    def blocked(self, user, tid, report_id):
        exc = self.code(reports.get_report, self.db, user, tid, report_id)
        self.assertEqual(exc.code, ErrorCode.REPORT_BLOCKED)
        return exc.details['reasons']


class Roles(TeamCase):
    def test_creator_is_admin_and_non_members_cannot_even_see_the_team(self):
        ann, stranger = self.person('ann'), self.person('zed')
        tid = self.team(ann)
        view = svc.get_team(self.db, ann, tid)
        self.assertEqual((view['role'], view['status'], view['member_count'], view['visibility_epoch']), ('admin', 'active', 1, 1))
        calls = [svc.get_team, svc.list_members, svc.list_repositories, svc.list_shares, reports.list_reports]
        for fn in calls:
            self.assertEqual(self.code(fn, self.db, stranger, tid).code, ErrorCode.RESOURCE_NOT_FOUND, fn.__name__)
        self.assertEqual(self.code(svc.rename_team, self.db, stranger, tid, 'x').code, ErrorCode.RESOURCE_NOT_FOUND)
        self.assertEqual(self.code(svc.delete_team, self.db, stranger, tid).code, ErrorCode.RESOURCE_NOT_FOUND)
        self.assertEqual(self.code(svc.invite, self.db, stranger, tid, InviteRequest(github_login='ann')).code, ErrorCode.RESOURCE_NOT_FOUND)
        self.assertEqual(self.code(reports.generate, self.db, stranger, tid, ReportCreate(repository_ids=[1])).code, ErrorCode.RESOURCE_NOT_FOUND)
        self.assertEqual(svc.list_teams(self.db, stranger), [])
        self.assertEqual(self.code(svc.get_team, self.db, ann, uuid4()).code, ErrorCode.RESOURCE_NOT_FOUND)

    def test_permission_matrix(self):
        tid = self.standard()
        extra_repo = self.make_repo()
        denied = lambda fn, user, *a: self.assertEqual(self.code(fn, self.db, user, tid, *a).code, ErrorCode.PERMISSION_DENIED, fn.__name__)   # noqa: E731
        for role_user in (self.bo, self.cy):                                                       # 관리 동작은 관리자만
            denied(svc.rename_team, role_user, 'x')
            denied(svc.delete_team, role_user)
            denied(svc.invite, role_user, InviteRequest(github_login='zed'))
            denied(svc.change_role, role_user, self.cy, 'member')
            denied(svc.remove_member, role_user, self.ann)                                          # 남을 제거하는 것은 관리자만(본인 탈퇴는 누구나 가능)
            denied(svc.add_repository, role_user, extra_repo)
            denied(svc.remove_repository, role_user, self.repo)
        for user in (self.ann, self.bo, self.cy):                                                  # 보기는 모두
            self.assertEqual(len(svc.list_members(self.db, user, tid)) >= 3, True)
            self.assertEqual(svc.get_team(self.db, user, tid)['name'], '팀 A')
            self.assertEqual(len(svc.list_repositories(self.db, user, tid)), 1)
            reports.list_reports(self.db, user, tid)
        rid = self.review(self.cy, self.repo)
        denied(svc.create_share, self.cy, ShareCreate(resource_type='review_result', resource_id=rid))     # 조회자는 공유할 수 없다
        denied(reports.generate, self.cy, ReportCreate(repository_ids=[self.repo]))                       # 조회자는 생성할 수 없다
        mine = self.review(self.bo, self.repo)
        self.assertEqual(self.share(self.bo, tid, mine)['status'], 'active')                              # 멤버는 자기 자료를 공유한다
        self.assertEqual(reports.generate(self.db, self.bo, tid, ReportCreate(repository_ids=[self.repo]))['team_id'], tid)
        anns = self.review(self.ann, self.repo)
        share_ann = self.share(self.ann, tid, anns)
        exc = self.code(svc.delete_share, self.db, self.bo, tid, share_ann['share_id'])                    # 남의 공유는 관리자만 해제한다
        self.assertEqual(exc.code, ErrorCode.PERMISSION_DENIED)
        svc.delete_share(self.db, self.ann, tid, self.share(self.bo, tid, mine)['share_id'])               # 관리자는 남의 공유도 해제한다
        report = reports.generate(self.db, self.bo, tid, ReportCreate(repository_ids=[self.repo]))
        denied(reports.delete_report, self.cy, report['report_id'])
        self.assertEqual(self.code(reports.delete_report, self.db, self.bo, tid, self.team_report_by(self.ann, tid)).code, ErrorCode.PERMISSION_DENIED)   # 멤버는 남이 만든 보고서를 못 지운다
        reports.delete_report(self.db, self.bo, tid, report['report_id'])                                  # 자기가 만든 것은 지운다
        renamed = svc.rename_team(self.db, self.ann, tid, '  새   이름 ')
        self.assertEqual(renamed['name'], '새 이름')

    def team_report_by(self, user, tid):
        return reports.generate(self.db, user, tid, ReportCreate(repository_ids=[self.repo]))['report_id']

    def test_invitations_need_acceptance_and_do_not_reveal_who_exists(self):
        ann, bo = self.person('ann'), self.person('bo')
        tid = self.team(ann)
        member = svc.invite(self.db, ann, tid, InviteRequest(github_login='@BO', role='viewer'))
        self.assertEqual((member['status'], member['role'], member['github_login']), ('invited', 'viewer', 'bo'))
        self.assertEqual(self.code(svc.get_team, self.db, bo, tid).code, ErrorCode.RESOURCE_NOT_FOUND)           # 수락하기 전에는 팀 내용을 볼 수 없다
        self.assertEqual(self.code(svc.list_members, self.db, bo, tid).code, ErrorCode.RESOURCE_NOT_FOUND)
        self.assertEqual([(t['status'], t['member_count']) for t in svc.list_teams(self.db, bo)], [('invited', 0)])
        self.assertEqual([m['status'] for m in svc.list_members(self.db, ann, tid)].count('invited'), 1)        # 초대 중인 사람은 관리자에게만 보인다
        accepted = svc.accept_invitation(self.db, bo, tid)
        self.assertEqual((accepted['status'], accepted['role'], accepted['member_count']), ('active', 'viewer', 2))
        self.assertEqual(self.code(svc.accept_invitation, self.db, bo, tid).code, ErrorCode.RESOURCE_NOT_FOUND)  # 이미 수락했다
        self.assertEqual(self.code(svc.invite, self.db, ann, tid, InviteRequest(github_login='bo')).code, ErrorCode.VERSION_CONFLICT)
        for bad in ('nobody', 'ANN-zzz'):                                                                         # 없는 사람과 모호한 사람은 같은 응답이다
            self.assertEqual(self.code(svc.invite, self.db, ann, tid, InviteRequest(github_login=bad)).code, ErrorCode.RESOURCE_NOT_FOUND)
        self.person('dup')
        self.person('DUP')
        exc = self.code(svc.invite, self.db, ann, tid, InviteRequest(github_login='dup'))
        self.assertEqual(exc.code, ErrorCode.RESOURCE_NOT_FOUND)
        self.assertEqual(self.code(svc.invite, self.db, ann, tid, InviteRequest(github_login='ann')).code, ErrorCode.INVALID_SELECTION)
        cy = self.person('cy')
        svc.invite(self.db, ann, tid, InviteRequest(github_login='cy'))
        svc.remove_member(self.db, cy, tid, cy)                                                                   # 초대 거절
        self.assertEqual(svc.list_teams(self.db, cy), [])
        with patch.object(svc, 'MAX_MEMBERS', 2):
            self.person('eve')
            self.assertEqual(self.code(svc.invite, self.db, ann, tid, InviteRequest(github_login='eve')).code, ErrorCode.USER_LIMIT)

    def test_a_team_always_keeps_one_admin(self):
        ann, bo = self.person('ann'), self.person('bo')
        tid = self.team(ann)
        self.join(tid, ann, bo, 'bo', 'member')
        for fn, args in ((svc.change_role, (ann, 'member')), (svc.remove_member, (ann,))):
            exc = self.code(fn, self.db, ann, tid, *args)
            self.assertEqual((exc.code, exc.details['reason']), (ErrorCode.VERSION_CONFLICT, 'LAST_ADMIN'), fn.__name__)
        svc.change_role(self.db, ann, tid, bo, 'admin')
        svc.change_role(self.db, bo, tid, ann, 'member')                                                          # 다른 관리자가 있으면 강등할 수 있다
        self.assertEqual(self.code(svc.remove_member, self.db, bo, tid, bo).code, ErrorCode.VERSION_CONFLICT)    # 이제 bo가 마지막 관리자
        svc.remove_member(self.db, ann, tid, ann)                                                                 # 일반 구성원은 탈퇴할 수 있다
        self.assertEqual(self.code(svc.get_team, self.db, ann, tid).code, ErrorCode.RESOURCE_NOT_FOUND)           # 탈퇴는 즉시 반영된다

    def test_team_limits_and_deletion_do_not_touch_personal_data(self):
        ann = self.person('ann')
        with patch.object(svc, 'MAX_TEAMS_PER_USER', 1):
            tid = self.team(ann)
            self.assertEqual(self.code(svc.create_team, self.db, ann, TeamCreate(name='둘째')).code, ErrorCode.USER_LIMIT)
        repo = self.make_repo()
        self.link(tid, ann, repo)
        rid = self.review(ann, repo)
        self.share(ann, tid, rid)
        svc.delete_team(self.db, ann, tid)
        self.assertEqual(self.scalar('SELECT count(*) FROM team_resource_shares') + self.scalar('SELECT count(*) FROM team_members'), 0)
        self.assertEqual(self.scalar('SELECT count(*) FROM ai_results WHERE id = :r', r=rid), 1)               # 개인 결과는 그대로
        self.assertEqual(self.code(svc.create_team, self.db, ann, TeamCreate(name=' ')).code, ErrorCode.INVALID_SELECTION)


class Sharing(TeamCase):
    def test_nothing_is_shared_automatically(self):
        tid = self.standard()
        rid = self.review(self.bo, self.repo)
        target = handover_service.create_target(self.db, self.bo, HandoverTargetCreate(repository_id=self.repo, name='대상', selected_paths=['a.py']))
        self.assertEqual(svc.list_shares(self.db, self.ann, tid), [])                                          # 팀 생성·가입·저장소 연결만으로는 아무것도 공유되지 않는다
        owner_only = self.code(svc.share_content, self.db, self.ann, tid, uuid4())
        self.assertEqual(owner_only.code, ErrorCode.RESOURCE_NOT_FOUND)
        self.assertEqual(self.scalar('SELECT count(*) FROM team_resource_shares'), 0)
        data = reports.generate(self.db, self.ann, tid, ReportCreate(repository_ids=[self.repo]))
        self.assertEqual((data['payload']['totals'].get('results', 0), data['payload']['totals'].get('handovers', 0)), (0, 0))   # 보고서에도 개인 자료는 없다
        self.assertIn(reports.T['NOTHING_SHARED'], data['markdown'])
        self.assertEqual(self.scalar('SELECT count(*) FROM ai_findings WHERE result_id = :r', r=rid), 2)
        self.assertEqual(target['name'], '대상')

    def test_only_my_own_review_results_and_targets_in_a_linked_repository_can_be_shared(self):
        tid = self.standard()
        other_repo = self.make_repo()
        theirs = self.review(self.ann, self.repo)
        self.assertEqual(self.code(svc.create_share, self.db, self.bo, tid, ShareCreate(resource_type='review_result', resource_id=theirs)).code, ErrorCode.RESOURCE_NOT_FOUND)
        summary = self.result_for(self.bo, self.repo, 'summary', [], pr_number=9, payload={'overview': 'x', 'file_changes': []})
        self.assertEqual(self.code(svc.create_share, self.db, self.bo, tid, ShareCreate(resource_type='review_result', resource_id=summary)).code, ErrorCode.INVALID_SELECTION)
        unlinked = self.review(self.bo, other_repo)
        self.assertEqual(self.code(svc.create_share, self.db, self.bo, tid, ShareCreate(resource_type='review_result', resource_id=unlinked)).code, ErrorCode.INVALID_SELECTION)
        mine = self.review(self.bo, self.repo)
        self.denied.add((self.bo, self.repo))                                                                # 공유하는 사람이 저장소 접근을 잃었다
        self.assertEqual(self.code(svc.create_share, self.db, self.bo, tid, ShareCreate(resource_type='review_result', resource_id=mine)).code, ErrorCode.RESOURCE_NOT_FOUND)
        self.denied.clear()
        self.bypass("UPDATE ai_results SET expires_at = now() - interval '1 minute' WHERE id = :r", r=mine)
        self.assertEqual(self.code(svc.create_share, self.db, self.bo, tid, ShareCreate(resource_type='review_result', resource_id=mine)).code, ErrorCode.RESULT_EXPIRED)
        foreign_target = handover_service.create_target(self.db, self.ann, HandoverTargetCreate(repository_id=self.repo, name='남의 대상', selected_paths=['a.py']))
        self.assertEqual(self.code(svc.create_share, self.db, self.bo, tid, ShareCreate(resource_type='handover_target', resource_id=foreign_target['target_id'])).code,
                         ErrorCode.RESOURCE_NOT_FOUND)
        fresh = self.review(self.bo, self.repo, pr=11)
        view, created = svc.create_share(self.db, self.bo, tid, ShareCreate(resource_type='review_result', resource_id=fresh))
        again, created2 = svc.create_share(self.db, self.bo, tid, ShareCreate(resource_type='review_result', resource_id=fresh))
        self.assertEqual((created, created2, again['share_id']), (True, False, view['share_id']))              # 같은 자료를 다시 공유해도 하나다

    def test_teams_are_isolated_from_each_other(self):
        tid = self.standard()
        zed = self.person('zed')
        other = self.team(zed, '팀 B')
        self.link(other, zed, self.repo)
        rid = self.review(self.bo, self.repo)
        share = self.share(self.bo, tid, rid)
        self.assertEqual(svc.list_shares(self.db, zed, other), [])                                           # 다른 팀에서는 보이지 않는다
        self.assertEqual(self.code(svc.share_content, self.db, zed, other, share['share_id']).code, ErrorCode.RESOURCE_NOT_FOUND)
        self.assertEqual(self.code(svc.share_content, self.db, zed, tid, share['share_id']).code, ErrorCode.RESOURCE_NOT_FOUND)    # 구성원이 아닌 팀에서도
        self.assertEqual(self.code(svc.delete_share, self.db, zed, other, share['share_id']).code, ErrorCode.RESOURCE_NOT_FOUND)
        self.assertEqual(self.code(svc.delete_share, self.db, self.ann, tid, uuid4()).code, ErrorCode.RESOURCE_NOT_FOUND)
        # 같은 자료를 두 팀에 따로 공유할 수 있고, 한쪽 해제는 다른 쪽에 영향이 없다
        self.join(other, zed, self.bo, 'bo', 'member')
        second = self.share(self.bo, other, rid)
        svc.delete_share(self.db, self.bo, tid, share['share_id'])
        self.assertEqual([s['share_id'] for s in svc.list_shares(self.db, zed, other)], [second['share_id']])
        self.assertEqual(self.epoch(other), 1)                                                               # 다른 팀의 세대는 올라가지 않는다
        self.assertEqual(self.epoch(tid), 2)

    def test_shared_review_content_is_the_reviewable_summary_without_code_quotes_or_notes(self):
        tid = self.standard()
        rid = self.review(self.bo, self.repo)
        self.bypass("""UPDATE ai_findings SET detail = detail || CAST(:d AS jsonb) WHERE result_id = :r""", r=rid,
                    d=json.dumps({'condition': '조건', 'impact': '영향', 'verification': '확인 방법', 'quote': 'SECRET = "abc123"', 'file_id': 'x', 'model_limitations': ['x']}))
        self.feedback(rid, self.bo, 'valid', ordinal=1)
        self.db.execute(text("UPDATE finding_feedback SET note = '내 비밀 메모'"))
        self.db.commit()
        share = self.share(self.bo, tid, rid)
        content = svc.share_content(self.db, self.cy, tid, share['share_id'])['content']                      # 조회자도 공유된 내용은 볼 수 있다
        dumped = json.dumps(content, ensure_ascii=False)
        self.assertNotIn('abc123', dumped)
        self.assertNotIn('내 비밀 메모', dumped)
        self.assertNotIn('file_id', dumped)
        f = content['findings']
        self.assertEqual([(x['ordinal'], x['verdict']) for x in f], [(1, 'valid'), (2, 'unverified')])         # 판정은 작성자의 것, 없으면 미검증
        self.assertEqual((f[0]['condition'], f[0]['impact'], f[0]['location']['path']), ('조건', '영향', 'main.py'))
        self.assertIn('작성자의 판단', content['note'])

    def test_history_numbers_come_along_but_not_names(self):
        tid = self.standard()
        rid = self.review(self.bo, self.repo)
        sid, _ = self.snapshot(self.repo)
        from app.services.ai import history_context
        history_context.build_history_evidence(self.db, rid, sid)
        self.db.commit()
        share = self.share(self.bo, tid, rid)
        history = svc.share_content(self.db, self.ann, tid, share['share_id'])['content']['history']
        self.assertEqual(history[0]['snapshot_id'], sid)
        main = history[0]['paths'][0]
        self.assertEqual((main['path'], main['change_freq'], main['bugfix_count'], main['contributor_count']), ('main.py', 12, 4, 3))
        self.assertNotIn('ann', json.dumps(history))                                                            # 기여자 이름(top_shares)은 싣지 않는다

    def test_shared_handover_target_shows_statuses_only_and_goes_outdated_when_changed(self):
        tid = self.standard()
        target = handover_service.create_target(self.db, self.bo, HandoverTargetCreate(repository_id=self.repo, name='로그인 인계', selected_paths=['a.py'],
                                                                                      purpose='개인 목적 메모', giver_name='김인계', receiver_name='이인수'))
        hid = target['target_id']
        handover_service.put_preparation(self.db, self.bo, hid, HandoverPreparationPut(expected_version=0, confirmed_target_version=1, items={
            'assignee': HandoverItemIn(status='ready', text='비공개 담당자 메모'), 'run_method': HandoverItemIn(status='needed', text='절차 메모', link='https://wiki.example/run')}))
        share = self.share(self.bo, tid, hid, 'handover_target')
        content = svc.share_content(self.db, self.ann, tid, share['share_id'])['content']
        dumped = json.dumps(content, ensure_ascii=False)
        for secret in ('비공개 담당자 메모', '절차 메모', 'wiki.example', '개인 목적 메모', '김인계', '이인수'):
            self.assertNotIn(secret, dumped)                                                                  # 메모·링크·인계자/인수자 메모는 공유하지 않는다
        by = {i['key']: i['status'] for i in content['items']}
        self.assertEqual((by['assignee'], by['run_method'], by['description']), ('ready', 'needed', 'unknown'))
        self.assertIn('작성자가 기록한 상태', content['note'])
        # 작성자가 바꾸면 공유는 outdated가 되어 내용이 숨겨진다(다시 공유해야 한다)
        handover_service.put_preparation(self.db, self.bo, hid, HandoverPreparationPut(expected_version=1, confirmed_target_version=1, items={
            'assignee': HandoverItemIn(status='ready', text='바뀐 담당자')}))
        self.assertEqual(svc.list_shares(self.db, self.ann, tid)[0]['status'], 'outdated')
        exc = self.code(svc.share_content, self.db, self.ann, tid, share['share_id'])
        self.assertEqual((exc.code, exc.details['reason']), (ErrorCode.INVALID_STATE, 'share_outdated'))
        view, created = svc.create_share(self.db, self.bo, tid, ShareCreate(resource_type='handover_target', resource_id=hid))
        self.assertEqual((created, view['status']), (False, 'active'))                                         # 다시 공유하면 현재 버전으로 고정된다
        handover_service.patch_target(self.db, self.bo, hid, HandoverTargetPatch(expected_version=1, name='바뀐 이름'))
        self.assertEqual(svc.list_shares(self.db, self.ann, tid)[0]['status'], 'outdated')

    def test_repository_access_is_checked_every_time_a_shared_item_is_opened(self):
        tid = self.standard()
        rid = self.review(self.bo, self.repo)
        share = self.share(self.bo, tid, rid)
        self.assertTrue(svc.share_content(self.db, self.cy, tid, share['share_id']))
        self.denied.add((self.cy, self.repo))                                                                  # 조회자가 저장소 접근을 잃었다
        self.assertEqual(self.code(svc.share_content, self.db, self.cy, tid, share['share_id']).code, ErrorCode.RESOURCE_NOT_FOUND)
        self.assertTrue(svc.share_content(self.db, self.ann, tid, share['share_id']))                          # 다른 구성원은 영향이 없다
        self.unavailable.add((self.ann, self.repo))                                                            # 접근 확인 장애: 열지 않는다(닫힘)
        self.assertEqual(self.code(svc.share_content, self.db, self.ann, tid, share['share_id']).code, ErrorCode.ACCESS_CHECK_UNAVAILABLE)

    def test_shares_follow_their_sources_and_repository_links(self):
        tid = self.standard()
        rid = self.review(self.bo, self.repo)
        share = self.share(self.bo, tid, rid)
        self.bypass("UPDATE ai_results SET expires_at = now() - interval '1 minute' WHERE id = :r", r=rid)
        self.assertEqual(svc.list_shares(self.db, self.ann, tid)[0]['status'], 'expired')
        exc = self.code(svc.share_content, self.db, self.ann, tid, share['share_id'])
        self.assertEqual((exc.code, exc.details['reason']), (ErrorCode.INVALID_STATE, 'share_expired'))
        with S.Session() as db:                                                                                 # 결과를 지우면(보관 기간 정리) 공유도 사라진다
            db.execute(text('DELETE FROM ai_results WHERE id = :r'), {'r': rid})
            db.commit()
        self.assertEqual(svc.list_shares(self.db, self.ann, tid), [])
        keep = self.share(self.bo, tid, self.review(self.bo, self.repo, pr=12))
        before = self.epoch(tid)
        svc.remove_repository(self.db, self.ann, tid, self.repo)                                                # 저장소 연결을 끊으면 그 저장소의 공유도 사라진다
        self.assertEqual(svc.list_shares(self.db, self.ann, tid), [])
        self.assertEqual(self.epoch(tid), before + 1)
        self.assertEqual(keep['team_id'], tid)

    def test_unsharing_is_limited_to_the_sharer_and_admins_and_raises_the_epoch(self):
        tid = self.standard()
        share = self.share(self.bo, tid, self.review(self.bo, self.repo))
        e0 = self.epoch(tid)
        svc.delete_share(self.db, self.bo, tid, share['share_id'])
        self.assertEqual((self.epoch(tid), svc.list_shares(self.db, self.ann, tid)), (e0 + 1, []))
        self.assertEqual(self.scalar("SELECT count(*) FROM audit_events WHERE action = 'team.unshared'"), 1)
        with patch.object(svc, 'MAX_SHARES_PER_TEAM', 1):
            self.share(self.bo, tid, self.review(self.bo, self.repo, pr=21))
            exc = self.code(svc.create_share, self.db, self.bo, tid, ShareCreate(resource_type='review_result', resource_id=self.review(self.bo, self.repo, pr=22)))
            self.assertEqual(exc.code, ErrorCode.USER_LIMIT)


class MembershipChanges(TeamCase):
    def test_leaving_or_removal_closes_that_persons_shares_and_blocks_existing_reports(self):
        tid = self.standard()
        share = self.share(self.bo, tid, self.review(self.bo, self.repo))
        report = reports.generate(self.db, self.ann, tid, ReportCreate(repository_ids=[self.repo]))
        self.assertEqual(report['payload']['totals']['results'], 1)
        e0 = self.epoch(tid)
        svc.remove_member(self.db, self.bo, tid, self.bo)                                                       # 탈퇴
        self.assertEqual(self.epoch(tid), e0 + 1)
        self.assertEqual(svc.list_shares(self.db, self.ann, tid), [])                                           # 그 사람의 공유는 모두 닫힌다
        self.assertEqual(self.code(svc.get_team, self.db, self.bo, tid).code, ErrorCode.RESOURCE_NOT_FOUND)     # 탈퇴한 사람은 즉시 팀을 볼 수 없다
        self.assertEqual(self.code(svc.share_content, self.db, self.bo, tid, share['share_id']).code, ErrorCode.RESOURCE_NOT_FOUND)
        self.assertIn('visibility_changed', self.blocked(self.ann, tid, report['report_id']))                    # 기존 보고서는 차단된다
        self.assertEqual(self.scalar('SELECT count(*) FROM ai_results'), 1)                                      # 개인 결과는 그대로
        fresh = reports.generate(self.db, self.ann, tid, ReportCreate(repository_ids=[self.repo]))               # 새 범위로 다시 생성하면 볼 수 있다
        self.assertEqual((fresh['payload']['totals'].get('results', 0), reports.get_report(self.db, self.ann, tid, fresh['report_id'])['visibility_epoch']), (0, e0 + 1))

    def test_demotion_to_viewer_closes_shares_and_removal_by_admin_does_too(self):
        tid = self.standard()
        self.share(self.bo, tid, self.review(self.bo, self.repo))
        report = reports.generate(self.db, self.ann, tid, ReportCreate(repository_ids=[self.repo]))
        svc.change_role(self.db, self.ann, tid, self.bo, 'viewer')
        self.assertEqual(svc.list_shares(self.db, self.ann, tid), [])
        self.assertIn('visibility_changed', self.blocked(self.cy, tid, report['report_id']))
        self.assertEqual(self.code(svc.create_share, self.db, self.bo, tid, ShareCreate(resource_type='review_result', resource_id=self.review(self.bo, self.repo, pr=15))).code,
                         ErrorCode.PERMISSION_DENIED)
        svc.change_role(self.db, self.ann, tid, self.bo, 'member')
        self.share(self.bo, tid, self.review(self.bo, self.repo, pr=16))
        svc.remove_member(self.db, self.ann, tid, self.bo)
        self.assertEqual(svc.list_shares(self.db, self.ann, tid), [])
        self.assertEqual(self.code(svc.remove_member, self.db, self.ann, tid, self.bo).code, ErrorCode.RESOURCE_NOT_FOUND)

    def test_upgrading_a_role_keeps_shares(self):
        tid = self.standard()
        self.share(self.bo, tid, self.review(self.bo, self.repo))
        svc.change_role(self.db, self.ann, tid, self.bo, 'admin')
        self.assertEqual(len(svc.list_shares(self.db, self.ann, tid)), 1)
        self.assertEqual(self.code(svc.change_role, self.db, self.ann, tid, uuid4(), 'member').code, ErrorCode.RESOURCE_NOT_FOUND)


class Reports(TeamCase):
    def seeded(self):
        tid = self.standard()
        r1 = self.review(self.bo, self.repo, titles=('SQL 문자열 결합', '매직 넘버', '긴 함수'))
        r2 = self.review(self.ann, self.repo, titles=('SQL 문자열 결합', '다른 지적'), pr=8)
        self.bypass("UPDATE ai_findings SET fingerprint = repeat('c', 64) WHERE title = 'SQL 문자열 결합'")    # 서로 다른 결과의 같은 지적(지문 일치)
        self.feedback(r1, self.bo, 'valid', ordinal=1)
        self.feedback(r1, self.bo, 'false_positive', ordinal=2)
        self.feedback(r2, self.ann, 'valid', ordinal=2)
        self.share(self.bo, tid, r1)
        self.share(self.ann, tid, r2)
        return tid, r1, r2

    def test_problem_tally_counts_only_human_confirmed_findings_and_repeats_are_candidates(self):
        tid, r1, r2 = self.seeded()
        report = reports.generate(self.db, self.ann, tid, ReportCreate(repository_ids=[self.repo]))
        totals = report['payload']['totals']
        self.assertEqual((totals['valid'], totals['unverified'], totals['uncertain'], totals['false_positive']), (2, 2, 0, 1))
        repo = report['payload']['repositories'][0]
        self.assertEqual(repo['problems'], {'confirmed': 2, 'pending_review': 2, 'uncertain': 0, 'excluded_false_positive': 1})   # 비검토·오탐은 문제 집계에서 제외
        self.assertEqual({p['title'] for p in repo['confirmed_problems']}, {'SQL 문자열 결합', '다른 지적'})
        md = report['markdown']
        self.assertIn(reports.T['SUMMARY_PROBLEMS'].format(valid=2, unverified=2, uncertain=0, false_positive=1), md)
        listed = md.split('**' + reports.T['VALID_HEAD'] + '**')[1].split('**')[0]
        self.assertNotIn('매직 넘버', listed)                                                                     # 오탐은 문제 목록에 없다
        self.assertNotIn('긴 함수', listed)                                                                       # 검증하지 않은 지적도 문제 목록에 없다
        self.assertEqual(len(repo['repeat_candidates']), 1)                                                     # 지문이 같은 지적은 후보로만
        cand = repo['repeat_candidates'][0]
        self.assertEqual((cand['result_count'], cand['title']), (2, 'SQL 문자열 결합'))
        self.assertIn(reports.T['CAVEAT_REPEAT'], md)
        for caveat in ('CAVEAT_AI', 'CAVEAT_PROBLEMS', 'CAVEAT_ACCESS'):
            self.assertIn(reports.T[caveat], md)
        for forbidden in FORBIDDEN:
            self.assertNotIn(forbidden, md)
        self.assertEqual(markdown_safety.validate_markdown(md), [])
        self.assertEqual(self.scalar('SELECT count(*) FROM team_reports'), 1)

    def test_manifest_pins_every_source_and_the_report_is_immutable(self):
        tid, r1, r2 = self.seeded()
        report = reports.generate(self.db, self.ann, tid, ReportCreate(repository_ids=[self.repo]))
        manifest = report['source_manifest']
        self.assertEqual((manifest['team_id'], manifest['visibility_epoch'], manifest['repository_ids']), (str(tid), self.epoch(tid), [self.repo]))
        by_resource = {s['resource_id']: s for s in manifest['shares']}
        self.assertEqual(set(by_resource), {str(r1), str(r2)})
        for s in by_resource.values():
            self.assertTrue(s['share_id'] and s['payload_hash'] and len(s['feedback_digest']) == 64 and s['shared_by'])
        self.assertEqual(report['visibility_epoch'], self.epoch(tid))
        with self.assertRaises(DBAPIError):
            with self.db.begin_nested():
                self.db.execute(text("UPDATE team_reports SET markdown = 'x'"))
        self.db.rollback()

    def test_hostile_titles_and_names_cannot_inject_markdown(self):
        tid = self.standard()
        hostile = ['<script>alert(1)</script>', '[x](javascript:alert(1))', '# 제목', '![t](http://t.example/p.png)', '`\n<img src=x onerror=alert(1)>\n`']
        rid = self.result_for(self.bo, self.repo, 'review', [('main.py', t) for t in hostile], pr_number=7)
        for n in range(1, len(hostile) + 1):
            self.feedback(rid, self.bo, 'valid', ordinal=n)
        self.share(self.bo, tid, rid)
        target = handover_service.create_target(self.db, self.bo, HandoverTargetCreate(repository_id=self.repo, name='<b>x</b> [y](javascript:z)', selected_paths=['a.py']))
        self.share(self.bo, tid, target['target_id'], 'handover_target')
        self.db.execute(text("UPDATE repositories SET name = '<i>repo</i>' WHERE id = :r"), {'r': self.repo})
        self.db.commit()
        md = reports.generate(self.db, self.ann, tid, ReportCreate(repository_ids=[self.repo]))['markdown']
        self.assertEqual(markdown_safety.validate_markdown(md), [])
        self.assertNotIn('<script', md.replace('\\<script', ''))

    def test_generator_needs_access_to_every_repository_and_only_linked_ones(self):
        tid = self.standard()
        second = self.make_repo()
        self.link(tid, self.ann, second)
        outside = self.make_repo()
        exc = self.code(reports.generate, self.db, self.ann, tid, ReportCreate(repository_ids=[self.repo, outside]))
        self.assertEqual(exc.code, ErrorCode.INVALID_SELECTION)
        self.denied.add((self.bo, second))
        exc = self.code(reports.generate, self.db, self.bo, tid, ReportCreate(repository_ids=[self.repo, second]))
        self.assertEqual(exc.code, ErrorCode.RESOURCE_NOT_FOUND)                                                # 일부 저장소에 접근할 수 없으면 만들 수 없다
        own = reports.generate(self.db, self.bo, tid, ReportCreate(repository_ids=[self.repo]))
        self.assertEqual(own['repository_ids'], [self.repo])
        self.assertEqual(self.scalar('SELECT count(*) FROM team_reports'), 1)
        with patch.object(reports, 'MAX_REPORTS_PER_TEAM', 1):
            self.assertEqual(self.code(reports.generate, self.db, self.ann, tid, ReportCreate(repository_ids=[self.repo])).code, ErrorCode.USER_LIMIT)

    def test_viewing_requires_access_to_all_source_repositories(self):
        tid, r1, r2 = self.seeded()
        second = self.make_repo()
        self.link(tid, self.ann, second)
        r3 = self.review(self.ann, second, pr=9)
        self.share(self.ann, tid, r3)
        both = reports.generate(self.db, self.ann, tid, ReportCreate(repository_ids=[self.repo, second]))
        self.assertTrue(reports.get_report(self.db, self.cy, tid, both['report_id']))                            # 모든 저장소에 접근하면 볼 수 있다
        self.denied.add((self.cy, second))
        self.assertEqual(self.blocked(self.cy, tid, both['report_id']), ['repository_access_lost'])             # 일부만 접근하면 같은 전문을 보여 주지 않는다
        self.assertTrue(reports.get_report(self.db, self.bo, tid, both['report_id']))                            # 접근하는 사람에게는 그대로
        first_only = reports.generate(self.db, self.ann, tid, ReportCreate(repository_ids=[self.repo]))          # 그 사람의 범위에 맞춰 따로 생성
        self.assertTrue(reports.get_report(self.db, self.cy, tid, first_only['report_id']))
        self.unavailable.add((self.bo, self.repo))                                                              # 접근 확인 장애는 열지 않는다
        self.assertEqual(self.blocked(self.bo, tid, first_only['report_id']), ['access_check_unavailable'])

    def test_reports_are_blocked_after_the_scope_shrinks_and_listing_marks_them(self):
        tid, r1, r2 = self.seeded()
        report = reports.generate(self.db, self.ann, tid, ReportCreate(repository_ids=[self.repo]))
        rid = report['report_id']
        self.assertTrue(reports.list_reports(self.db, self.cy, tid)[0]['visibility_current'])
        svc.delete_share(self.db, self.ann, tid, next(s['share_id'] for s in svc.list_shares(self.db, self.ann, tid) if s['shared_by'] == self.ann))   # 공유 해제
        reasons = self.blocked(self.cy, tid, rid)
        self.assertIn('visibility_changed', reasons)
        self.assertIn('share_removed', reasons)
        self.assertFalse(reports.list_reports(self.db, self.cy, tid)[0]['visibility_current'])
        exc = self.code(reports.get_report, self.db, self.ann, tid, rid)
        self.assertNotIn('markdown', json.dumps(exc.details))                                                    # 차단된 보고서의 내용은 어디에도 실리지 않는다
        self.assertEqual(self.code(reports.get_report, self.db, self.ann, tid, uuid4()).code, ErrorCode.RESOURCE_NOT_FOUND)
        self.assertEqual(self.code(reports.get_report, self.db, self.person('zed'), tid, rid).code, ErrorCode.RESOURCE_NOT_FOUND)

    def test_repository_unlink_and_outdated_sources_block_reports(self):
        tid = self.standard()
        target = handover_service.create_target(self.db, self.bo, HandoverTargetCreate(repository_id=self.repo, name='대상', selected_paths=['a.py']))
        self.share(self.bo, tid, target['target_id'], 'handover_target')
        report = reports.generate(self.db, self.ann, tid, ReportCreate(repository_ids=[self.repo]))
        self.assertEqual(report['payload']['totals']['handovers'], 1)
        handover_service.put_preparation(self.db, self.bo, target['target_id'], HandoverPreparationPut(expected_version=0, confirmed_target_version=1,
                                                                                                        items={'assignee': HandoverItemIn(status='needed')}))
        self.assertIn('share_outdated', self.blocked(self.ann, tid, report['report_id']))                        # 작성자가 바꾸면 그 공유가 들어간 보고서는 차단
        again = reports.generate(self.db, self.ann, tid, ReportCreate(repository_ids=[self.repo]))
        self.assertEqual((again['payload']['totals'].get('handovers', 0), again['payload']['excluded_shares'][0]['reason']), (0, 'outdated'))   # 새 보고서는 제외한 공유를 밝힌다
        self.assertIn(reports.T['REASON_outdated'], again['markdown'])
        svc.remove_repository(self.db, self.ann, tid, self.repo)
        self.assertIn('repository_unlinked', self.blocked(self.ann, tid, again['report_id']))

    def test_changed_verdicts_mark_the_report_stale_without_blocking_it(self):
        tid, r1, r2 = self.seeded()
        report = reports.generate(self.db, self.ann, tid, ReportCreate(repository_ids=[self.repo]))
        self.assertEqual(reports.get_report(self.db, self.cy, tid, report['report_id'])['stale_reasons'], [])
        self.feedback(r1, self.bo, 'false_positive', ordinal=1, version=2)
        view = reports.get_report(self.db, self.cy, tid, report['report_id'])
        self.assertEqual(view['stale_reasons'], ['feedback_changed'])
        self.assertEqual(view['markdown'], report['markdown'])                                                  # 만든 보고서는 그대로이고 기준이 달라졌다고만 알린다

    def test_generation_is_serialized_with_membership_changes(self):
        tid, r1, r2 = self.seeded()
        entered, release = threading.Event(), threading.Event()
        real_collect = reports._collect
        order = []

        def slow_collect(db, team_id, repository_ids):
            entered.set()
            self.assertTrue(release.wait(15))
            return real_collect(db, team_id, repository_ids)

        out = {}

        def generate():
            with S.Session() as db, patch.object(reports, '_collect', side_effect=slow_collect):
                out['report'] = reports.generate(db, self.ann, tid, ReportCreate(repository_ids=[self.repo]))
            order.append('generated')

        def remove():
            with S.Session() as db:
                svc.remove_member(db, self.ann, tid, self.bo)
            order.append('removed')
        t1 = threading.Thread(target=generate)
        t1.start()
        self.assertTrue(entered.wait(15))                                                                        # 생성이 팀 행을 잡은 상태
        t2 = threading.Thread(target=remove)
        t2.start()
        t2.join(1.5)
        self.assertTrue(t2.is_alive())                                                                           # 구성원 제거는 생성이 끝날 때까지 기다린다
        release.set()
        t1.join(20)
        t2.join(20)
        self.assertEqual(order, ['generated', 'removed'])
        self.assertEqual(out['report']['payload']['totals']['results'], 2)                                       # 생성은 변경 전 상태로 일관되게 끝났다
        self.assertIn('visibility_changed', self.blocked(self.ann, tid, out['report']['report_id']))             # 변경 뒤에는 그 보고서가 차단된다

    def test_scope_changes_between_the_access_check_and_the_lock_abort_the_generation(self):
        tid, r1, r2 = self.seeded()
        real = team_access.authorize_ai

        def check_then_change(db, user_id, repo_id, action, policy):
            result = real(db, user_id, repo_id, action, policy)
            with S.Session() as other:                                                                           # 확인과 잠금 사이에 다른 요청이 범위를 줄였다
                svc.delete_share(other, self.ann, tid, next(s['share_id'] for s in svc.list_shares(other, self.ann, tid) if s['shared_by'] == self.bo))
            return result
        with patch.object(team_access, 'authorize_ai', side_effect=check_then_change):
            exc = self.code(reports.generate, self.db, self.ann, tid, ReportCreate(repository_ids=[self.repo]))
        self.assertEqual((exc.code, exc.details['reason']), (ErrorCode.VERSION_CONFLICT, 'VISIBILITY_CHANGED'))
        self.assertEqual(self.scalar('SELECT count(*) FROM team_reports'), 0)                                    # 저장하지 않는다

    def test_reports_make_no_model_or_network_calls(self):
        tid, r1, r2 = self.seeded()
        spent = self.scalar('SELECT count(*) FROM usage_reservations') + self.scalar('SELECT count(*) FROM ai_attempts')
        with patch('requests.sessions.Session.request', side_effect=AssertionError('네트워크 호출 금지')), patch('socket.create_connection', side_effect=AssertionError('네트워크 호출 금지')):
            reports.generate(self.db, self.ann, tid, ReportCreate(repository_ids=[self.repo]))
        self.assertEqual(self.scalar('SELECT count(*) FROM usage_reservations') + self.scalar('SELECT count(*) FROM ai_attempts'), spent)   # 비용·시도 기록도 늘지 않는다

    def test_fixed_wording_is_pinned_and_free_of_confirmation_phrases(self):
        self.assertEqual(reports.text_digest(), reports.TEXT_DIGEST)
        for key, value in reports.T.items():
            for forbidden in FORBIDDEN:
                self.assertNotIn(forbidden, value, key)


class PersonalCachesStaySeparate(TeamCase):
    def test_team_sharing_does_not_change_personal_jobs_results_or_reuse(self):
        tid = self.standard()
        rid = self.review(self.bo, self.repo)
        credentials_before = self.scalar('SELECT count(*) FROM ai_credentials')
        jobs_before = self.db.execute(text('SELECT id, fingerprint, owner_user_id, status FROM ai_jobs ORDER BY id')).all()
        results_before = self.db.execute(text('SELECT id, owner_user_id, payload_hash FROM ai_results ORDER BY id')).all()
        self.share(self.bo, tid, rid)
        reports.generate(self.db, self.ann, tid, ReportCreate(repository_ids=[self.repo]))
        svc.remove_member(self.db, self.bo, tid, self.bo)
        self.assertEqual(self.db.execute(text('SELECT id, fingerprint, owner_user_id, status FROM ai_jobs ORDER BY id')).all(), jobs_before)
        self.assertEqual(self.db.execute(text('SELECT id, owner_user_id, payload_hash FROM ai_results ORDER BY id')).all(), results_before)
        for table in ('ai_jobs', 'ai_results', 'ai_findings', 'ai_inputs', 'request_idempotency', 'usage_budgets', 'usage_reservations', 'ai_credentials'):
            cols = {r.column_name for r in self.db.execute(text('SELECT column_name FROM information_schema.columns WHERE table_name = :t'), {'t': table})}
            self.assertFalse({c for c in cols if 'team' in c}, table)                                            # 개인 테이블에는 팀 주체가 없다
        self.assertEqual(self.scalar('SELECT count(*) FROM ai_credentials'), credentials_before)                 # 팀 키·예산은 만들지 않는다(시험 준비가 만든 개인 키만 있다)
        self.assertEqual(self.scalar('SELECT count(*) FROM usage_budgets'), 0)

    def test_an_owner_can_still_use_a_shared_result_personally_after_unsharing(self):
        tid = self.standard()
        rid = self.review(self.bo, self.repo)
        share = self.share(self.bo, tid, rid)
        svc.delete_share(self.db, self.bo, tid, share['share_id'])
        from app.services.ai import job_views
        from datetime import datetime, timezone
        row = job_views.load_result_row(self.db, rid, self.bo, datetime.now(timezone.utc))                      # 작성자는 계속 읽는다
        self.assertEqual((row.id, row.finding_count), (rid, 2))
        self.assertEqual(self.code(job_views.load_result_row, self.db, self.ann, rid, datetime.now(timezone.utc)).code, ErrorCode.RESOURCE_NOT_FOUND)   # 팀원이라도 공유하지 않은 개인 결과는 못 읽는다


class Api(TeamCase):
    def setUp(self):
        super().setUp()
        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key='api-test', same_site='lax')
        app.add_exception_handler(AIError, ai_error_response)
        app.add_exception_handler(RequestValidationError, ai_validation_error_response)
        for module in (auth_router, teams_api):
            app.include_router(module.router)

        @app.post('/test/login/{user_id}')
        async def login(user_id: str, request: Request):
            request.session['user_id'] = user_id
            return {}
        self.client = TestClient(app)

    def tearDown(self):
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

    def test_http_flow_status_codes_and_blocking(self):
        base = '/api/ai/v1'
        ann, bo, cy = self.person('ann'), self.person('bo'), self.person('cy')
        repo = self.make_repo()
        self.login(ann)
        created = self.client.post(f'{base}/teams', headers=self.h(), json={'name': 'API 팀'})
        self.assertEqual(created.status_code, 201, created.text)
        tid = created.json()['team_id']
        self.assertEqual(self.client.post(f'{base}/teams/{tid}/repositories', headers=self.h(), json={'repository_id': repo}).status_code, 201)
        for who, role in (('bo', 'member'), ('cy', 'viewer')):
            self.assertEqual(self.client.post(f'{base}/teams/{tid}/members', headers=self.h(), json={'github_login': who, 'role': role}).status_code, 201)
        self.login(bo)
        self.assertEqual(self.client.post(f'{base}/teams/{tid}/invitation/accept', headers=self.h()).json()['role'], 'member')
        rid = self.review(bo, repo)
        shared = self.client.post(f'{base}/teams/{tid}/shares', headers=self.h(), json={'resource_type': 'review_result', 'resource_id': str(rid)})
        self.assertEqual(shared.status_code, 201, shared.text)
        again = self.client.post(f'{base}/teams/{tid}/shares', headers=self.h(), json={'resource_type': 'review_result', 'resource_id': str(rid)})
        self.assertEqual((again.status_code, again.json()['share_id']), (200, shared.json()['share_id']))
        report = self.client.post(f'{base}/teams/{tid}/reports', headers=self.h(), json={'repository_ids': [repo]})
        self.assertEqual(report.status_code, 201, report.text)
        report_id = report.json()['report_id']
        self.login(cy)
        self.client.post(f'{base}/teams/{tid}/invitation/accept', headers=self.h())
        got = self.client.get(f'{base}/teams/{tid}/reports/{report_id}')
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got.headers['cache-control'], 'private, no-store')
        content = self.client.get(f'{base}/teams/{tid}/shares/{shared.json()["share_id"]}/content')
        self.assertEqual(content.status_code, 200)
        self.err(self.client.post(f'{base}/teams/{tid}/shares', headers=self.h(), json={'resource_type': 'review_result', 'resource_id': str(rid)}), 403, 'PERMISSION_DENIED')
        self.err(self.client.post(f'{base}/teams/{tid}/reports', headers=self.h(), json={'repository_ids': [repo]}), 403, 'PERMISSION_DENIED')
        self.denied.add((cy, repo))                                                                              # 접근을 잃으면 차단
        err = self.err(self.client.get(f'{base}/teams/{tid}/reports/{report_id}'), 403, 'REPORT_BLOCKED')
        self.assertEqual(err['details']['reasons'], ['repository_access_lost'])
        self.assertNotIn('markdown', json.dumps(err))
        self.denied.clear()
        self.login(ann)
        svc.remove_member(self.db, ann, uuid_of(tid), bo)
        blocked = self.err(self.client.get(f'{base}/teams/{tid}/reports/{report_id}'), 403, 'REPORT_BLOCKED')
        self.assertIn('visibility_changed', blocked['details']['reasons'])
        listed = self.client.get(f'{base}/teams/{tid}/reports').json()['reports']
        self.assertEqual([r['visibility_current'] for r in listed], [False])
        self.assertEqual(self.client.delete(f'{base}/teams/{tid}/reports/{report_id}', headers=self.h()).json()['status'], 'deleted')

    def test_auth_csrf_validation_isolation_and_error_table(self):
        base = '/api/ai/v1'
        ann, zed = self.person('ann'), self.person('zed')
        anon = TestClient(self.client.app)
        self.err(anon.get(f'{base}/teams'), 401, 'AUTH_REQUIRED')
        self.login(ann)
        self.err(self.client.post(f'{base}/teams', json={'name': 'x'}), 403, 'CSRF_FAILED')
        for bad in ({'name': ''}, {'name': 'x' * 81}, {'name': 'x', 'extra': 1}, {}):
            self.err(self.client.post(f'{base}/teams', headers=self.h(), json=bad), 422, 'INVALID_SELECTION')
        tid = self.client.post(f'{base}/teams', headers=self.h(), json={'name': '격리'}).json()['team_id']
        self.err(self.client.post(f'{base}/teams/{tid}/members', headers=self.h(), json={'github_login': 'x', 'role': 'owner'}), 422, 'INVALID_SELECTION')
        self.err(self.client.post(f'{base}/teams/{tid}/reports', headers=self.h(), json={'repository_ids': []}), 422, 'INVALID_SELECTION')
        self.login(zed)
        for method, path in (('get', ''), ('get', '/members'), ('get', '/shares'), ('get', '/reports'), ('get', '/repositories')):
            self.err(getattr(self.client, method)(f'{base}/teams/{tid}{path}'), 404, 'RESOURCE_NOT_FOUND')
        self.err(self.client.delete(f'{base}/teams/{tid}', headers=self.h()), 404, 'RESOURCE_NOT_FOUND')
        self.assertEqual(self.client.get(f'{base}/teams').json()['teams'], [])
        self.assertEqual((ERROR_HTTP_STATUS[ErrorCode.PERMISSION_DENIED], ERROR_HTTP_STATUS[ErrorCode.REPORT_BLOCKED]), (403, 403))


def uuid_of(value):
    from uuid import UUID
    return UUID(value)


if __name__ == '__main__':
    unittest.main(verbosity=2)
