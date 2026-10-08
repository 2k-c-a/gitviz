"""입력 준비 작업(prepare) handler. AI 제공자를 호출하지 않고 선택한 PR 코드를 고정해 암호화 저장한다.

흐름: 권한 재확인 → PR 변경 재확인 → 고정 코드 취득(DB transaction 밖) → manifest·해시 조립 →
작업 종료 확정과 같은 transaction에서 입력 확정. 취소가 접수되면 확정하지 않는다.
이 모듈을 import하면 'prepare' handler가 등록된다(celery include에 포함).
"""
import logging
import time

from fastapi import HTTPException
from sqlalchemy import select

from app.collectors.ai_errors import CollectionError, PRChanged
from app.collectors.git import ai_diff_collector
from app.collectors.github.ai_pr_collector import RepoRef, verify_unchanged
from app.collectors.github.github_client import GitHubClient
from app.core.ai_settings import get_ai_settings
from app.models.ai_input import AIInput, PRRevision as PRRevisionRow
from app.models.ai_job import AIJob
from app.models.repository import Repository
from app.schemas.ai import PRRevision
from app.services.ai import input_builder, input_store, job_runner
from app.services.ai.access import AIAction, AIError, authorize_ai
from app.services.ai.lease import CancelRequested
from app.services.repository_access import canonical_github_url, user_github_token

logger = logging.getLogger('gitviz.ai.prepare')

PROTOCOLS = 'https'      # 테스트가 local remote를 쓰기 위해 바꾼다(운영은 https만)

_PERMANENT_ACCESS_ERRORS = {'RESOURCE_NOT_FOUND', 'REAUTH_REQUIRED', 'AUTH_REQUIRED', 'INVALID_STATE'}


def _fail_permanently(ctx, input_id, error_code: str):
    """입력을 실패로 닫고 작업을 재시도 없이 실패시킨다."""
    with ctx.session_factory() as db:
        input_store.fail_input(db, input_id)
        db.commit()
    raise job_runner.JobFailed(error_code)


def _check_time(ctx, input_id, started: float, limit: int):
    ctx.raise_if_stopped()
    if ctx.cancel_requested:
        raise CancelRequested('취소가 접수된 작업입니다.')
    if time.monotonic() - started > limit:
        _fail_permanently(ctx, input_id, 'PREPARE_TIMEOUT')


def prepare_handler(ctx):
    started = time.monotonic()
    settings = get_ai_settings()
    with ctx.session_factory() as db:
        job = db.execute(select(AIJob).where(AIJob.id == ctx.lease.job_id)).scalar_one()
        inp = db.execute(select(AIInput).where(AIInput.id == job.input_id)).scalar_one()
        revision = db.execute(select(PRRevisionRow).where(PRRevisionRow.id == inp.revision_id)).scalar_one()
        repo = db.execute(select(Repository).where(Repository.id == job.repository_id)).scalar_one()
        input_id, owner_id, repository_id, policy = inp.id, job.owner_user_id, job.repository_id, inp.policy_version
        paths = list(job.request_options.get('selected_paths') or [])
        options = dict(job.request_options or {})
        captured = dict(revision_id=revision.id, pr_number=revision.pr_number, base_sha=revision.base_sha,
                        head_sha=revision.head_sha, merge_base_sha=revision.merge_base_sha,
                        metadata=dict(options.get('pr_metadata') or revision.pr_metadata or {}),
                        pr_total_files=options.get('pr_total_files'))
        repo_url = canonical_github_url(repo.url)
        github_id = repo.github_id
        try:
            token = user_github_token(db, owner_id)
            authorize_ai(db, owner_id, repository_id, AIAction.prepare, policy)    # 준비 시점 권한 재확인
        except HTTPException:
            _fail_permanently(ctx, input_id, 'REAUTH_REQUIRED')
        except AIError as exc:
            if exc.code.value in _PERMANENT_ACCESS_ERRORS:
                _fail_permanently(ctx, input_id, exc.code.value)
            raise job_runner.RetryableError(exc.code.value)
    if not paths:
        _fail_permanently(ctx, input_id, 'INVALID_SELECTION')
    owner_name, repo_name = repo_url.split('github.com/')[1].split('/')
    ref = RepoRef(repository_id=repository_id, github_id=github_id, owner=owner_name, name=repo_name)
    try:
        client = GitHubClient(token=token, timeout=15, max_retries=2, max_wait_seconds=10)
        _check_time(ctx, input_id, started, settings.prepare_time_limit_seconds)
        verify_unchanged(client, ref, captured['pr_number'], captured['base_sha'], captured['head_sha'])
        with ai_diff_collector.revision_workspace(input_builder.remote_url_for(repo_url), captured['merge_base_sha'],
                                                  captured['head_sha'], token=token, protocols=PROTOCOLS) as work:
            _check_time(ctx, input_id, started, settings.prepare_time_limit_seconds)
            collected = ai_diff_collector.collect_selected_code(
                work, captured['merge_base_sha'], captured['head_sha'], sorted(paths), max_files=settings.max_files,
                max_bytes=settings.max_input_bytes, protocols=PROTOCOLS,
                pr_total_files=_total_files({'changed_files': captured['pr_total_files']}))
        verify_unchanged(client, ref, captured['pr_number'], captured['base_sha'], captured['head_sha'])
        _check_time(ctx, input_id, started, settings.prepare_time_limit_seconds)
    except CollectionError as exc:
        # PR이 바뀌었다면 같은 리비전을 다시 시도해도 소용없다. 사용자가 새 SHA로 다시 준비해야 한다.
        if exc.retryable and not isinstance(exc, PRChanged):
            raise job_runner.RetryableError(input_builder.error_code_for(exc))
        _fail_permanently(ctx, input_id, input_builder.error_code_for(exc))
    built = input_builder.assemble(
        collected, revision=PRRevision(repo_id=repository_id, pr_number=captured['pr_number'],
                                       base_sha=captured['base_sha'], head_sha=captured['head_sha'],
                                       merge_base_sha=captured['merge_base_sha']),
        revision_id=captured['revision_id'], metadata=input_builder.transfer_metadata(captured['metadata']),
        policy_version=policy)
    return job_runner.HandlerResult(work=lambda db: input_store.finalize_input(db, input_id, built, collected))


def _total_files(metadata: dict):
    total = metadata.get('changed_files')
    return total if isinstance(total, int) and not isinstance(total, bool) and total >= 0 else None


job_runner.register_handler('prepare', prepare_handler)
