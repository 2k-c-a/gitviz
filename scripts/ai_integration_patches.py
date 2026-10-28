"""통합 검증(verify_ai_mvp_integration.py)에서만 쓰는 대역. 운영 코드는 바꾸지 않고, 별도 프로세스(app 서버·worker)가
이 모듈을 불러와 GitHub 접근만 로컬 git fixture로 바꾼다. 모델 호출은 대역이 아니라 실제 provider adapter가
로컬 가짜 제공자(AI_TEST_PROVIDER_BASE_URL)를 호출한다.

필요한 환경변수: AI_IT_FIXTURE = {"path": 원격 bare 저장소 경로, "base": 해시, "head": 해시, "merge_base": 해시}
- worker: AI_HANDLER_MODULES=scripts.ai_integration_patches 로 불러오면 import 시 apply_worker()가 실행된다.
- app 서버: scripts/ai_integration_app.py가 apply_app()을 호출한다.
"""
import json
import os
from pathlib import Path
from types import SimpleNamespace


def _fixture():
    return json.loads(os.environ['AI_IT_FIXTURE'])


def apply_worker():
    from app.services.ai import generation_service, input_builder
    from app.tasks import ai_prepare
    fx = _fixture()
    ai_prepare.authorize_ai = lambda *a, **k: ('repo', {})
    ai_prepare.user_github_token = lambda *a, **k: None
    ai_prepare.verify_unchanged = lambda *a, **k: None
    ai_prepare.PROTOCOLS = 'file'
    input_builder.remote_url_for = lambda url: Path(fx['path']).as_uri()
    generation_service.authorize_ai = lambda db, user_id, repo_id, action, policy: (None, {'private': False})


def apply_app():
    from sqlalchemy import text

    from app.api.ai import inputs, jobs, pulls
    from app.collectors.github.ai_pr_collector import CapturedRevision, PRFilesPage, PRPage
    from app.schemas.ai import InventoryCoverage, canonical_hash
    fx = _fixture()

    def authorize(db, user_id, repo_id, action, policy):
        row = db.execute(text('SELECT id, url, github_id FROM repositories WHERE id = :i'), {'i': repo_id}).one_or_none()
        if row is None:
            from app.schemas.ai import ErrorCode
            from app.services.ai.access import AIError
            raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '저장소를 찾을 수 없습니다.')
        return SimpleNamespace(id=row.id, url=row.url, github_id=row.github_id), {'private': False}

    def capture(client, ref, pr_number, base, head):
        return CapturedRevision(
            repository_id=ref.repository_id, pr_number=pr_number, github_pr_id=9000 + pr_number, base_sha=fx['base'], head_sha=fx['head'],
            merge_base_sha=fx['merge_base'], state_at_capture='open',
            metadata={'title': 'Add feature', 'body': 'Adds the feature', 'body_truncated': False, 'changed_files': None},
            revision_fingerprint=canonical_hash({'repo': ref.repository_id, 'pr': pr_number, 'head': fx['head'], 'base': fx['base']}))

    def prs(client, ref, state, cursor, limit):
        return PRPage(items=[{'number': 7, 'github_pr_id': 9007, 'title': 'Add feature', 'state': state, 'draft': False, 'author_login': 'dev',
                              'base_ref': 'main', 'base_sha': fx['base'], 'head_ref': 'feature', 'head_sha': fx['head'],
                              'head_repo_full_name': ref.full_name, 'head_repo_deleted': False, 'is_fork': False, 'updated_at': None}],
                      next_cursor=None)

    def files(client, ref, number, cursor, limit):
        return PRFilesPage(files=[{'path': 'main.py', 'old_path': None, 'change_type': 'modified', 'additions': 1, 'deletions': 1,
                                   'blob_sha': None, 'has_patch': True}], next_cursor=None, base_sha=fx['base'], head_sha=fx['head'],
                           pr_total_files=1, inventory_coverage=InventoryCoverage.complete)
    for module in (inputs, jobs, pulls):
        module.authorize_ai = authorize
    inputs.capture_revision = capture
    inputs.user_github_token = lambda db, uid: None
    inputs.GitHubClient = lambda **kw: None
    pulls._token = lambda db, uid: None
    pulls.GitHubClient = lambda **kw: None
    pulls.list_ai_prs = prs
    pulls.list_pr_files = files
    # 분석 API(GET /repositories 등)의 GitHub 권한 확인도 외부망 없이 대역으로 둔다. fixture 저장소는 GitHub에
    # 같은 numeric ID로 존재하지 않으므로, 실제 GitHub가 돌려줄 결과(없음 → 목록에서 제외)와 같은 404를 낸다.
    from fastapi import HTTPException
    from app.services import repository_access

    def metadata(url, token=None, *, public_fallback=False):
        raise HTTPException(404, '저장소를 찾을 수 없거나 접근 권한이 없습니다.')
    repository_access.github_metadata = metadata


if os.environ.get('AI_IT_FIXTURE') and os.environ.get('AI_IT_ROLE') == 'worker':
    apply_worker()
