"""입력의 저장·확정·조회·삭제·만료. 코드 원문은 AES-GCM으로 암호화해 파일 행에만 둔다.

- 확정(finalize_input)은 코드 암호화·파일 행·manifest/hash·coverage·상태 전이를 한 transaction으로 저장한다.
  입력 준비 작업의 종료 확정(job_service.finish_job)의 work로 실행되어 작업 상태와 함께 커밋된다.
- 확정 뒤에는 파일을 바꾸거나 추가할 수 없다(DB trigger). 코드를 더하려면 새 입력을 준비한다.
- 로그·큐·audit에는 코드 원문과 PR 설명 본문을 넣지 않는다.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.ai_settings import get_ai_settings
from app.models.ai_input import AIInput, AIInputFile, PRRevision as PRRevisionRow
from app.models.ai_usage import AuditEvent
from app.schemas.ai import (ContentState, Coverage, ErrorCode, InputFileContent, InputFileView, InputManifest,
                            InputPreview, InputStatus, JobStage, JobStatus, PrepareJobView, Side)
from app.services.ai import input_builder, job_service
from app.services.ai.access import AIError, ensure_owner
from app.services.ai.content_policy import POLICY_VERSION
from app.services.ai.crypto import InputCryptoError, decrypt_input, encrypt_input

PLACEHOLDER_COVERAGE = {'inventory_coverage': 'unknown', 'selected_content_coverage': 'none',
                        'analysis_coverage': 'none', 'selected_files': 0, 'pr_total_files': None, 'limitations': []}


class InputNotBuilding(Exception):
    """준비 중이 아닌 입력을 확정하려 했다(이미 확정·실패·삭제됨)."""


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def audit(db: Session, *, actor_id: UUID | None, repository_id: int | None, action: str, object_id: UUID | str | None,
          request_id: str, object_type: str = 'input', details: dict | None = None) -> None:
    """감사 기록. 코드·메모·토큰 본문은 넣지 않는다(DB CHECK도 막는다)."""
    db.add(AuditEvent(actor_user_id=actor_id, repository_id=repository_id, action=action, object_type=object_type,
                      object_id=str(object_id) if object_id else None, request_id=request_id,
                      details=details or {}))


# ── 생성 ────────────────────────────────────────────────────────────────

def upsert_revision(db: Session, owner_id: UUID, repository_id: int, captured) -> UUID:
    """같은 (owner, repo, fingerprint)의 리비전은 재사용한다. 리비전 행은 불변이다."""
    db.execute(text("""
        INSERT INTO pr_revisions (owner_user_id, repository_id, pr_number, github_pr_id, base_sha, head_sha,
                                  merge_base_sha, revision_fingerprint, state_at_capture, metadata)
        VALUES (:o, :r, :n, :g, :b, :h, :m, :f, :s, CAST(:meta AS jsonb))
        ON CONFLICT (owner_user_id, repository_id, revision_fingerprint) DO NOTHING"""),
               {'o': owner_id, 'r': repository_id, 'n': captured.pr_number, 'g': captured.github_pr_id,
                'b': captured.base_sha, 'h': captured.head_sha, 'm': captured.merge_base_sha,
                'f': captured.revision_fingerprint, 's': captured.state_at_capture,
                'meta': json.dumps(captured.metadata)})
    return db.execute(select(PRRevisionRow.id).where(
        PRRevisionRow.owner_user_id == owner_id, PRRevisionRow.repository_id == repository_id,
        PRRevisionRow.revision_fingerprint == captured.revision_fingerprint)).scalar_one()


def create_building_input(db: Session, owner_id: UUID, repository_id: int, revision_id: UUID,
                          now: datetime) -> UUID:
    settings = get_ai_settings()
    row = AIInput(owner_user_id=owner_id, repository_id=repository_id, revision_id=revision_id, status='building',
                  coverage=PLACEHOLDER_COVERAGE, policy_version=POLICY_VERSION,
                  expires_at=now + timedelta(days=settings.input_retention_days), created_at=now, updated_at=now)
    db.add(row)
    db.flush()
    return row.id


def create_input_and_job(db: Session, *, owner_id: UUID, repository_id: int, captured, selected_paths: list[str],
                         route: str, idempotency_key: str, body_hash: str, now: datetime | None = None):
    """리비전·building 입력·prepare 작업·Idempotency-Key·outbox를 한 transaction으로 저장한다.

    같은 key·같은 요청, 또는 같은 fingerprint의 사용 가능한 준비가 있으면 새로 만들지 않고 기존 것을 돌려준다.
    """
    now = _now(now)
    metadata = input_builder.transfer_metadata(captured.metadata)
    paths = sorted(selected_paths)
    fingerprint = input_builder.prepare_fingerprint(captured.revision_fingerprint, paths, metadata)

    def factory(session: Session) -> UUID:
        revision_id = upsert_revision(session, owner_id, repository_id, captured)
        return create_building_input(session, owner_id, repository_id, revision_id, now)

    return job_service.create_job(
        db, owner_id=owner_id, repository_id=repository_id, input_id=None, kind='prepare', fingerprint=fingerprint,
        route=route, idempotency_key=idempotency_key, body_hash=body_hash, input_factory=factory,
        # 리비전 행은 불변이고 같은 SHA면 재사용되므로, 이 입력이 보낼 PR 설명은 작업에 함께 고정해 둔다.
        request_options={'selected_paths': paths, 'pr_number': captured.pr_number,
                         'expected_base_sha': captured.base_sha, 'expected_head_sha': captured.head_sha,
                         'pr_metadata': metadata.model_dump(mode='json'),
                         'pr_total_files': captured.metadata.get('changed_files')},
        now=now)


# ── 확정 ────────────────────────────────────────────────────────────────

def finalize_input(db: Session, input_id: UUID, built, collected, *, now: datetime | None = None) -> None:
    """준비를 마친 입력을 확정한다(준비 작업의 종료 확정과 같은 transaction에서 호출)."""
    now = _now(now)
    settings = get_ai_settings()
    row = db.execute(select(AIInput).where(AIInput.id == input_id).with_for_update()
                     .execution_options(populate_existing=True)).scalar_one_or_none()
    if row is None or row.status != 'building':
        raise InputNotBuilding('준비 중인 입력이 아닙니다.')
    for item in collected.files:
        file_id = uuid4()
        entry = item.manifest
        payload = None
        if item.payload is not None:
            payload = encrypt_input(item.payload, settings.active_key_version,
                                    aad=f'{input_id}:{file_id}'.encode())
        db.add(AIInputFile(
            id=file_id, input_id=input_id, path=entry.path, old_path=entry.old_path,
            side_availability=item.side_availability, language=entry.language, content_state=entry.content_state.value,
            reason=entry.exclusion_reason.value if entry.exclusion_reason else None,
            blob_oids={'old': entry.old_blob_oid, 'new': entry.new_blob_oid}, content_hash=entry.content_hash,
            encrypted_payload=payload, key_version=settings.active_key_version if payload is not None else None,
            line_map=item.line_map, byte_count=item.byte_count))
    db.flush()
    row.manifest = built.manifest.model_dump(mode='json')
    row.manifest_hash = built.manifest_hash
    row.input_hash = built.input_hash
    row.coverage = built.coverage.model_dump(mode='json')
    row.finalized_at = now
    row.valid_until = now + timedelta(hours=settings.input_valid_hours)
    row.status = 'ready'
    row.updated_at = now
    db.flush()


def fail_input(db: Session, input_id: UUID, now: datetime | None = None) -> bool:
    """준비 중인 입력을 실패로 닫는다. 이미 닫혔으면 아무것도 하지 않는다."""
    result = db.execute(text("UPDATE ai_inputs SET status = 'failed', updated_at = :now "
                             "WHERE id = :i AND status = 'building'"), {'now': _now(now), 'i': input_id})
    return result.rowcount == 1


# ── 조회 ────────────────────────────────────────────────────────────────

def _load_owned(db: Session, input_id: UUID, owner_id: UUID) -> AIInput:
    row = db.execute(select(AIInput).where(AIInput.id == input_id)
                     .execution_options(populate_existing=True)).scalar_one_or_none()
    if row is None:
        raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '입력을 찾을 수 없습니다.')
    ensure_owner(row.owner_user_id, owner_id)          # 다른 작성자의 자료는 존재를 숨긴다
    return row


load_owned = _load_owned


def effective_status(row: AIInput, now: datetime) -> str:
    if row.status == 'ready' and now >= row.expires_at:
        return 'expired'
    return row.status


def load_preview(db: Session, input_id: UUID, owner_id: UUID, *, now: datetime | None = None) -> InputPreview:
    now = _now(now)
    row = _load_owned(db, input_id, owner_id)
    revision = db.execute(select(PRRevisionRow).where(PRRevisionRow.id == row.revision_id)).scalar_one()
    status = effective_status(row, now)
    manifest = InputManifest(**row.manifest) if row.manifest else None
    files = []
    stored = {f.path: f for f in db.execute(select(AIInputFile).where(AIInputFile.input_id == row.id)).scalars()}
    if manifest is not None:
        for entry in manifest.files:
            f = stored.get(entry.path)
            if f is None:
                continue
            files.append(InputFileView(
                file_id=f.id, path=entry.path, old_path=entry.old_path, change_type=entry.change_type,
                language=entry.language, content_state=entry.content_state, exclusion_reason=entry.exclusion_reason,
                side_availability=f.side_availability, byte_count=f.byte_count, content_hash=entry.content_hash,
                hunks=entry.hunks))
    read_files = [f for f in files if f.content_state is ContentState.read]
    raw_available = status == 'ready' and any(f.encrypted_payload is not None for f in stored.values())
    metadata = manifest.pr_metadata if manifest and manifest.pr_metadata else input_builder.transfer_metadata(
        revision.pr_metadata or {})
    token_estimate = (input_builder.estimate_tokens(sum(f.byte_count for f in read_files), metadata, len(read_files))
                      if status in ('ready', 'expired') and manifest is not None else None)
    job = db.execute(text("""SELECT id, status, error_code FROM ai_jobs WHERE input_id = :i AND kind = 'prepare'
                             ORDER BY generation DESC LIMIT 1"""), {'i': row.id}).one_or_none()
    prepare_job = None
    if job is not None:
        stage = JobStage.input_preparation if job.status in ('queued', 'running', 'retry_wait', 'cancel_requested') else None
        prepare_job = PrepareJobView(job_id=job.id, status=JobStatus(job.status), stage=stage,
                                     error_code=job.error_code)
    settings = get_ai_settings()
    return InputPreview(
        input_id=row.id, status=InputStatus(status), repository_id=row.repository_id, pr_number=revision.pr_number,
        base_sha=revision.base_sha, head_sha=revision.head_sha, merge_base_sha=revision.merge_base_sha,
        pr_state=revision.state_at_capture, pr_title=metadata.title, pr_body=metadata.body,
        pr_body_truncated=metadata.body_truncated, coverage=Coverage(**row.coverage), files=files,
        manifest_hash=row.manifest_hash, policy_version=row.policy_version, provider_profile=settings.provider_profile,
        token_estimate=token_estimate, valid_until=row.valid_until, expires_at=row.expires_at,
        execution_valid=status == 'ready' and row.valid_until is not None and now < row.valid_until,
        raw_available=raw_available, reviewable=bool(read_files), prepare_job=prepare_job)


def read_file_content(db: Session, input_id: UUID, file_id: UUID, owner_id: UUID, *, side: str | None = None,
                      start_line: int | None = None, end_line: int | None = None, request_id: str = '-',
                      now: datetime | None = None) -> InputFileContent:
    """보관된 고정 diff를 줄 번호와 함께 돌려준다. side·범위를 주면 그 부분만(작성자만, 실제 보관 자료)."""
    now = _now(now)
    row = _load_owned(db, input_id, owner_id)
    if (start_line is None) != (end_line is None) or (start_line is not None and side is None):
        raise AIError(ErrorCode.INVALID_SELECTION, '줄 범위는 side와 시작·끝 줄을 함께 지정해야 합니다.')
    if side is not None and side not in (Side.LEFT.value, Side.RIGHT.value):
        raise AIError(ErrorCode.INVALID_SELECTION, 'side는 LEFT 또는 RIGHT여야 합니다.')
    if start_line is not None and (start_line < 1 or end_line < start_line):
        raise AIError(ErrorCode.INVALID_SELECTION, '줄 범위가 올바르지 않습니다.')
    status = effective_status(row, now)
    if status == 'expired':
        raise AIError(ErrorCode.INPUT_EXPIRED, '원문 코드의 보관 기간이 지났거나 삭제됐습니다.')
    if status != 'ready':
        raise AIError(ErrorCode.INVALID_STATE, '준비가 끝난 입력이 아닙니다.')
    f = db.execute(select(AIInputFile).where(AIInputFile.id == file_id, AIInputFile.input_id == row.id)
                   ).scalar_one_or_none()
    if f is None:
        raise AIError(ErrorCode.RESOURCE_NOT_FOUND, '파일을 찾을 수 없습니다.')
    if side is not None and f.side_availability not in ('BOTH', side):
        raise AIError(ErrorCode.INVALID_SELECTION, '이 파일에는 해당 side가 없습니다.')
    manifest = InputManifest(**row.manifest)
    entry = next(e for e in manifest.files if e.path == f.path)
    lines = []
    if f.content_state == 'read':
        if f.encrypted_payload is None:
            raise AIError(ErrorCode.INPUT_EXPIRED, '원문 코드의 보관 기간이 지났거나 삭제됐습니다.')
        try:
            plain = decrypt_input(bytes(f.encrypted_payload), f.key_version, aad=f'{row.id}:{f.id}'.encode())
            if hashlib.sha256(plain).hexdigest() != f.content_hash:
                raise InputCryptoError('content hash mismatch')
        except InputCryptoError:
            audit(db, actor_id=owner_id, repository_id=row.repository_id, action='input.integrity_failure',
                  object_id=f.id, request_id=request_id, object_type='input_file')
            db.commit()
            raise AIError(ErrorCode.INVALID_STATE, '보관된 코드의 무결성을 확인할 수 없습니다.') from None
        lines = input_builder.diff_lines(plain.decode('utf-8'))
        if side is not None:
            key = 'left_line' if side == Side.LEFT.value else 'right_line'
            lines = [l for l in lines if getattr(l, key) is not None
                     and (start_line is None or start_line <= getattr(l, key) <= end_line)]
    audit(db, actor_id=owner_id, repository_id=row.repository_id, action='input.file_viewed', object_id=f.id,
          request_id=request_id, object_type='input_file')
    db.commit()
    return InputFileContent(file_id=f.id, path=f.path, old_path=entry.old_path, change_type=entry.change_type,
                            language=f.language, side_availability=f.side_availability,
                            content_state=ContentState(f.content_state),
                            exclusion_reason=entry.exclusion_reason, content_hash=f.content_hash, lines=lines)


# ── 삭제·만료 ───────────────────────────────────────────────────────────

def _purge_payloads(db: Session, input_id: UUID) -> int:
    result = db.execute(text("""UPDATE ai_input_files SET encrypted_payload = NULL, key_version = NULL
                                WHERE input_id = :i AND encrypted_payload IS NOT NULL"""), {'i': input_id})
    return result.rowcount


def _cancel_linked_jobs(db: Session, input_id: UUID, now: datetime) -> int:
    """이 입력을 쓰는 진행 중 작업(준비·생성)에 취소를 접수한다. 종료된 작업은 그대로 둔다."""
    rows = db.execute(text("""SELECT id, owner_user_id FROM ai_jobs WHERE input_id = :i AND deleted_at IS NULL
                              AND status IN ('queued', 'running', 'retry_wait', 'cancel_requested')"""),
                      {'i': input_id}).all()
    db.rollback()
    for job in rows:
        try:
            job_service.request_cancel(db, job.id, job.owner_user_id, now=now)
        except AIError:
            pass                                         # 그 사이 삭제·종료된 작업
    return len(rows)


def delete_input(db: Session, input_id: UUID, owner_id: UUID, *, request_id: str = '-',
                 now: datetime | None = None) -> str:
    """원문 삭제와 연결 작업 취소를 접수한다. 입력 행·manifest·이미 만들어진 결과는 남긴다.

    순서가 중요하다: 작업 취소를 먼저 접수해야 진행 중인 준비 작업이 취소 뒤에 '성공'으로 확정되지 못한다.
    """
    now = _now(now)
    row = _load_owned(db, input_id, owner_id)
    repository_id = row.repository_id
    db.rollback()
    _cancel_linked_jobs(db, input_id, now)
    locked = db.execute(select(AIInput).where(AIInput.id == input_id).with_for_update()
                        .execution_options(populate_existing=True)).scalar_one()
    purged = _purge_payloads(db, input_id) if locked.status != 'building' else 0
    if locked.status == 'building':
        locked.status, locked.updated_at = 'failed', now
    elif locked.status == 'ready':
        locked.status, locked.updated_at = 'expired', now
    # failed/expired는 이미 닫힌 입력이라 행을 다시 쓰지 않는다(남은 원문 삭제만 멱등하게 반복).
    audit(db, actor_id=owner_id, repository_id=repository_id, action='input.deleted', object_id=input_id,
          request_id=request_id, details={'files_purged': purged})
    db.commit()
    return locked.status


def run_input_maintenance(db: Session, *, now: datetime | None = None, limit: int = 100) -> dict:
    """보관 기간이 지난 원문 삭제, 끝난 준비 작업에 묶인 building 입력 정리. 참조 여부와 무관하게 기한이 우선한다."""
    now = _now(now)
    stats = {'expired': 0, 'payloads_purged': 0, 'failed': 0, 'jobs_cancelled': 0}
    for input_id in db.execute(text("""SELECT id FROM ai_inputs WHERE status = 'ready' AND expires_at <= :now
                                       ORDER BY expires_at LIMIT :l FOR UPDATE SKIP LOCKED"""),
                               {'now': now, 'l': limit}).scalars().all():
        stats['payloads_purged'] += _purge_payloads(db, input_id)
        db.execute(text("UPDATE ai_inputs SET status = 'expired', updated_at = :now WHERE id = :i"),
                   {'now': now, 'i': input_id})
        stats['expired'] += 1
        db.commit()
        stats['jobs_cancelled'] += _cancel_linked_jobs(db, input_id, now)
    stale = db.execute(text("""
        SELECT i.id FROM ai_inputs i WHERE i.status = 'building' AND (i.expires_at <= :now OR NOT EXISTS (
            SELECT 1 FROM ai_jobs j WHERE j.input_id = i.id AND j.kind = 'prepare'
              AND j.status IN ('queued', 'running', 'retry_wait', 'cancel_requested')))
        ORDER BY i.created_at LIMIT :l FOR UPDATE OF i SKIP LOCKED"""), {'now': now, 'l': limit}).scalars().all()
    for input_id in stale:
        stats['failed'] += 1 if fail_input(db, input_id, now) else 0
    db.commit()
    return stats
