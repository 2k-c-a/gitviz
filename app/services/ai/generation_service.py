"""설명·리뷰 생성 실행(summary / review handler).

순서: 처리권·권한·정책·동의·키·만료 확인 → 입력 복호화·프롬프트 → 로컬 한도 검사 → **재검증 → count_tokens**(코드가 외부로
나가는 첫 호출) → **재검증 → 비용 예약 → 호출** → 정산 → 출력 검증 → 접근 재확인 → 결과·상태 commit.

- 외부로 코드가 나가는 호출(count_tokens·생성) 직전마다 권한(저장소 접근·전송 허용·정책)·동의(manifest)·입력 유효기간·
  모델 허용·사용자 key(삭제·파기)·취소를 다시 확인한다. 이전 확인에 기대지 않는다.
- 호출 시간 동안 DB transaction은 열려 있지 않다(세션은 확인·기록마다 짧게 열고 닫는다).
- 호출이 일어났다면 결과가 어떻든 비용을 정산한다. 결과를 알 수 없으면(outcome_uncertain) 예약을 남겨 자동 재호출을 막는다.
- 취소·처리권 상실·접근 상실 이후에는 결과를 성공으로 공개하지 않는다. 결과 저장 실패는 자동으로 다시 호출하지 않는다.
- summary와 review는 독립된 호출이다. review 프롬프트에 summary 결과를 코드 사실로 넣지 않는다.
- 로그에는 식별자·분류 코드만 남긴다(코드·모델 출력·key 없음).
"""
import hashlib
import logging
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, text

from app.models.ai_input import AIInput, AIInputFile
from app.schemas.ai import InputManifest
from app.services.ai import credentials, prompts, result_store, token_budget, usage_service, validators
from app.services.ai.access import AIAction, AIError, authorize_ai
from app.services.ai.crypto import InputCryptoError, decrypt_input
from app.services.ai.job_runner import HandlerResult, JobContext, JobFailed, RetryableError
from app.services.ai.lease import CancelRequested, LeaseLost
from app.services.ai.providers import registry
from app.services.ai.providers.base import GenerationRequest, ProviderError, ProviderFailure, TokenLimitExceeded
from app.services.ai.credentials import CredentialUnavailable

logger = logging.getLogger('gitviz.ai.generate')

_RETRYABLE_ACCESS = {'ACCESS_CHECK_UNAVAILABLE', 'UPSTREAM_RATE_LIMIT'}
_PROFILE_REASONS = {'profile_changed', 'profile_unknown'}
_FINISH_ERRORS = {'truncated': 'OUTPUT_TRUNCATED', 'refused': 'PROVIDER_REFUSED', 'empty': 'OUTPUT_INVALID',
                  'incomplete': 'OUTPUT_INVALID'}


@dataclass(frozen=True)
class JobInfo:
    job_id: UUID
    owner_id: UUID
    repository_id: int
    input_id: UUID
    kind: str
    profile_id: str
    consent_hash: str | None
    revision_id: UUID
    policy_version: str


def _load_job(ctx: JobContext) -> JobInfo:
    with ctx.session_factory() as db:
        row = db.execute(text("""
            SELECT j.owner_user_id, j.repository_id, j.input_id, j.kind, j.provider_profile, j.consent_manifest_hash,
                   j.deleted_at, i.revision_id, i.policy_version
            FROM ai_jobs j JOIN ai_inputs i ON i.id = j.input_id WHERE j.id = :j"""),
                         {'j': ctx.lease.job_id}).one_or_none()
    if row is None or row.deleted_at is not None or row.provider_profile is None:
        raise JobFailed('INVALID_STATE')
    return JobInfo(job_id=ctx.lease.job_id, owner_id=row.owner_user_id, repository_id=row.repository_id,
                   input_id=row.input_id, kind=row.kind, profile_id=row.provider_profile,
                   consent_hash=row.consent_manifest_hash, revision_id=row.revision_id,
                   policy_version=row.policy_version)


def _authorize(ctx: JobContext, job: JobInfo, action: AIAction, *, retryable: bool) -> None:
    """저장소 접근·(외부 전송이면) 전송 허용·정책을 지금 다시 확인한다. 장애를 허용으로 추정하지 않는다."""
    with ctx.session_factory() as db:
        try:
            authorize_ai(db, job.owner_id, job.repository_id, action, job.policy_version)
        except HTTPException:
            raise JobFailed('REAUTH_REQUIRED') from None
        except AIError as exc:
            code = exc.code.value
            if retryable and code in _RETRYABLE_ACCESS:
                raise RetryableError(code) from None     # 아직 아무것도 보내지 않았다면 다시 시도해도 안전하다
            raise JobFailed('ACCESS_REVOKED' if code == 'RESOURCE_NOT_FOUND' else code) from None


def _check_alive(ctx: JobContext) -> None:
    """처리권을 갱신하고 취소·상실을 확인한다."""
    result = ctx.heartbeat()
    if not result.alive:
        raise LeaseLost('처리권을 잃은 작업입니다.')
    if result.cancel_requested:
        raise CancelRequested('취소가 접수된 작업입니다.')


def revalidate(ctx: JobContext, job: JobInfo) -> None:
    """코드를 외부로 보내는 호출 직전의 재검증.

    가장 오래 걸리는 접근 권한·전송 정책 확인(GitHub)을 먼저 하고, 마지막에 처리권·취소·입력·동의·모델·key를 한 번에 확인한다.
    그래야 느린 확인 중에 일어난 key 삭제·취소가 호출 직전 확인에 반영된다.
    """
    _authorize(ctx, job, AIAction.generate, retryable=True)
    _check_alive(ctx)
    with ctx.session_factory() as db:
        inp = db.execute(text("""SELECT status, valid_until, manifest_hash FROM ai_inputs WHERE id = :i"""),
                         {'i': job.input_id}).one_or_none()
        key = db.execute(text("""SELECT v.status, v.encrypted_key IS NOT NULL AS has_secret
                                 FROM ai_jobs j JOIN ai_credential_versions v ON v.id = j.credential_version_id
                                 WHERE j.id = :j"""), {'j': job.job_id}).one_or_none()
        now = db.execute(text('SELECT now()')).scalar()
    if inp is None or inp.status != 'ready' or inp.valid_until is None or inp.valid_until <= now:
        raise JobFailed('INPUT_EXPIRED')
    if job.consent_hash is None or inp.manifest_hash is None or inp.manifest_hash.strip() != job.consent_hash.strip():
        raise JobFailed('PREVIEW_CHANGED')              # 동의한 코드 범위와 다르다
    if not registry.is_allowed(job.profile_id):
        raise JobFailed('PROFILE_NOT_ALLOWED')
    if key is None or key.status == 'deleted' or not key.has_secret:
        raise JobFailed('CREDENTIAL_UNAVAILABLE')       # 삭제·파기된 key로는 보내지 않는다


def _credential_failure(reason: str) -> JobFailed:
    return JobFailed('PROFILE_CHANGED' if reason in _PROFILE_REASONS else 'CREDENTIAL_UNAVAILABLE')


def _prepare(ctx: JobContext, job: JobInfo):
    """고정된 key로 provider를 만들고(평문 key는 provider 안에만 있다) 보낼 자료를 복호화·렌더링한다."""
    with ctx.session_factory() as db:
        try:
            provider = credentials.provider_for_job(db, job.job_id)
        except CredentialUnavailable as exc:
            raise _credential_failure(exc.reason) from None
        row = db.execute(select(AIInput).where(AIInput.id == job.input_id)).scalar_one()
        manifest = InputManifest(**row.manifest)
        input_coverage = dict(row.coverage)
        stored = db.execute(select(AIInputFile).where(AIInputFile.input_id == row.id)).scalars().all()
        payloads, file_ids = {}, {}
        for f in stored:
            file_ids[f.path] = f.id
            if f.content_state != 'read':
                continue
            if f.encrypted_payload is None:
                raise JobFailed('INPUT_EXPIRED')
            try:
                plain = decrypt_input(bytes(f.encrypted_payload), f.key_version, aad=f'{row.id}:{f.id}'.encode())
            except InputCryptoError:
                raise JobFailed('INPUT_INTEGRITY') from None
            if hashlib.sha256(plain).hexdigest() != f.content_hash:
                raise JobFailed('INPUT_INTEGRITY')
            payloads[f.path] = plain
    sent = prompts.build_sent_input(manifest, payloads, file_ids)
    if not sent.files:
        raise JobFailed('NO_REVIEWABLE_CONTENT')
    rendered = prompts.render_request(job.kind, sent)
    request = GenerationRequest(kind=job.kind, system=rendered.system, user=rendered.user)
    try:
        token_budget.check_input_limit(provider.profile, provider.estimate_tokens(request))
    except TokenLimitExceeded:
        raise JobFailed('INPUT_TOO_LARGE') from None
    return provider, request, sent, result_store.result_coverage(input_coverage, len(sent.files))


def _provider_failure(failure: ProviderFailure):
    """호출이 처리되지 않았음이 확실한 실패를 작업 오류로 바꾼다."""
    if failure.retryable:
        return RetryableError(failure.error_code)
    return JobFailed(failure.error_code)


def _count_tokens(provider, request: GenerationRequest) -> int:
    try:
        return provider.count_tokens(request)
    except ProviderError as err:                       # count_tokens는 과금되지 않는다: 예약·불명 처리가 필요 없다
        raise _provider_failure(err.failure) from None


def _reserve(ctx: JobContext, job: JobInfo, amount: Decimal) -> None:
    """key 행을 먼저 잠그고(삭제와 직렬화) 작업 행을 잠가 취소·처리권을 확인하면서 예산을 예약한다."""
    with ctx.session_factory() as db:
        locked = db.execute(text("""SELECT v.status, v.encrypted_key IS NOT NULL AS has_secret
                                    FROM ai_credential_versions v WHERE v.id = (SELECT credential_version_id FROM ai_jobs
                                    WHERE id = :j) FOR SHARE"""), {'j': job.job_id}).one_or_none()
        if locked is None or locked.status == 'deleted' or not locked.has_secret:
            raise JobFailed('CREDENTIAL_UNAVAILABLE')
        try:
            usage_service.reserve_attempt(db, ctx.lease, amount)
        except AIError as exc:
            db.rollback()
            raise JobFailed(exc.code.value) from None     # BUDGET_LIMIT 등: 호출하지 않았다
        db.commit()


def _record_request_id(ctx: JobContext, request_id: str | None) -> None:
    if not request_id:
        return
    with ctx.session_factory() as db:
        db.execute(text("""UPDATE ai_attempts SET provider_request_id = :r WHERE job_id = :j AND attempt_no = :n"""),
                   {'r': request_id[:200], 'j': ctx.lease.job_id, 'n': ctx.lease.attempt_no})
        db.commit()


def _settle(ctx: JobContext, response, reserved: Decimal) -> None:
    """호출이 처리됐다: 결과가 쓸모없더라도 비용은 든다. usage가 없으면 예약액으로 정산한다."""
    actual = response.cost if response.cost is not None else reserved
    usage = response.usage
    with ctx.session_factory() as db:
        usage_service.settle_attempt(db, ctx.lease.job_id, ctx.lease.attempt_no, actual,
                                     input_tokens=usage.billed_input_tokens if usage else None,
                                     output_tokens=usage.output_tokens if usage else None)
        db.commit()


def _after_failed_call(ctx: JobContext, err: ProviderError):
    """호출 중 실패: 처리되지 않았다면 예약을 해제하고, 결과를 모르면 예약을 남긴다(자동 재호출 금지)."""
    failure = err.failure
    with ctx.session_factory() as db:
        if failure.outcome_uncertain:
            usage_service.mark_uncertain(db, ctx.lease.job_id, ctx.lease.attempt_no, error_code=failure.error_code)
        else:
            usage_service.release_attempt(db, ctx.lease.job_id, ctx.lease.attempt_no)
        db.commit()
    if failure.outcome_uncertain:
        return JobFailed(failure.error_code)
    return _provider_failure(failure)


def generate_handler(ctx: JobContext) -> HandlerResult:
    job = _load_job(ctx)
    revalidate(ctx, job)                                   # 1. 코드를 읽기 전에
    provider, request, sent, coverage = _prepare(ctx, job)
    profile = provider.profile
    revalidate(ctx, job)                                   # 2. count_tokens 직전(코드가 처음 외부로 나간다)
    counted = _count_tokens(provider, request)
    try:
        token_budget.check_input_limit(profile, counted)
    except TokenLimitExceeded:
        raise JobFailed('INPUT_TOO_LARGE') from None
    revalidate(ctx, job)                                   # 3. 생성 호출 직전
    reserved = token_budget.max_cost(profile, counted)
    _reserve(ctx, job, reserved)
    try:
        response = provider.generate(request)
    except ProviderError as err:
        raise _after_failed_call(ctx, err) from None
    except Exception as exc:                               # 어떤 이유로든 호출 결과를 알 수 없다
        logger.error('provider call crashed job=%s error=%s', job.job_id, type(exc).__name__)
        raise _after_failed_call(ctx, ProviderError(ProviderFailure('unknown_error', retryable=False,
                                                                    outcome_uncertain=True))) from None
    _settle(ctx, response, reserved)
    _record_request_id(ctx, response.request_id)
    _check_alive(ctx)                                      # 취소·처리권 상실 이후에는 결과를 공개하지 않는다
    if response.finish != 'complete':
        raise JobFailed(_FINISH_ERRORS.get(response.finish, 'OUTPUT_INVALID'))
    validated = validators.validate_output(job.kind, response, sent, revision_id=job.revision_id)
    if not validated.ok:
        logger.info('output rejected job=%s issues=%s', job.job_id, sorted({i.code for i in validated.issues}))
        raise JobFailed('OUTPUT_VALIDATION_FAILED')
    _authorize(ctx, job, AIAction.read, retryable=False)   # 호출 중 접근 권한을 잃었다면 결과를 남기지 않는다
    _check_alive(ctx)
    lease = ctx.lease
    return HandlerResult(work=lambda db: result_store.save_validated_result(db, lease, validated, coverage=coverage),
                         storage_retryable=False)
