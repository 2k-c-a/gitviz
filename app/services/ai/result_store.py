"""검증을 통과한 생성 결과를 저장한다.

`save_validated_result`는 job_service.finish_job의 `work`로 실행되어 작업 상태 확정과 같은 transaction에서
result·findings를 만든다. 둘은 함께 커밋되거나 함께 사라진다: 결과 없이 succeeded인 작업도, 성공하지 않은 작업의
결과도 없다. 취소가 접수된 작업은 finish_job이 work를 실행하지 않는다.
결과 원본(payload)과 지적은 DB trigger로 변경이 막힌다. 모델 결과의 사람 검토 상태는 항상 unverified로 시작한다.
"""
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.ai_settings import get_ai_settings
from app.models.ai_result import AIFinding, AIResult
from app.schemas.ai import ContentCoverage, Coverage, HumanValidation, StructureValidation, canonical_hash
from app.services.ai.lease import Lease
from app.services.ai.validators import ValidatedResult


def finding_fingerprint(path: str, side: str, start_line: int, end_line: int, category: str, title: str) -> str:
    """같은 위치·같은 주제의 지적을 재생성 사이에도 알아보기 위한 해시(피드백 이력 연결용). 모델 문장 전체는 넣지 않는다."""
    return canonical_hash(['finding-v1', path, side, start_line, end_line, category, title.casefold()])


def result_coverage(input_coverage: dict, sent_files: int) -> dict:
    """결과의 분석 범위. 입력(준비 단계)의 coverage는 그대로 두고 analysis_coverage만 실제로 모델에 보낸 범위로 채운다.

    선택한 파일을 모두 보냈으면 complete, 일부만 보냈으면 partial, 하나도 못 보냈으면 none이다.
    선택 범위 밖(PR의 나머지 파일)은 inventory·limitations(not_selected)가 따로 나타낸다.
    """
    base = Coverage(**input_coverage)
    selected = base.selected_files
    if sent_files <= 0 or base.selected_content_coverage is ContentCoverage.none:
        analysis = ContentCoverage.none
    elif sent_files >= selected and base.selected_content_coverage is ContentCoverage.complete:
        analysis = ContentCoverage.complete
    else:
        analysis = ContentCoverage.partial
    return base.model_copy(update={'analysis_coverage': analysis}).model_dump(mode='json')


def save_validated_result(db: Session, lease: Lease, validated: ValidatedResult, *, coverage: dict,
                          now: datetime | None = None) -> UUID:
    """작업(처리권 소유자의 finish_job 안)에서 결과와 지적을 저장하고 result_id를 돌려준다. commit은 호출자가 한다."""
    if (not validated.ok or validated.payload is None or validated.payload_hash is None
            or validated.structure_validation is not StructureValidation.passed):
        raise ValueError('검증을 통과하지 못한 결과는 저장할 수 없습니다.')
    if validated.human_validation is not HumanValidation.unverified:
        raise ValueError('새 결과의 human_validation은 unverified여야 합니다.')
    now = now or datetime.now(timezone.utc)
    job = db.execute(text("SELECT kind, owner_user_id, repository_id FROM ai_jobs WHERE id = :j"),
                     {'j': lease.job_id}).one_or_none()
    if job is None or job.kind != validated.kind or job.owner_user_id != lease.owner_user_id \
            or job.repository_id != lease.repository_id:
        raise ValueError('결과가 작업과 맞지 않습니다.')
    result = AIResult(job_id=lease.job_id, owner_user_id=job.owner_user_id, repository_id=job.repository_id,
                      payload=validated.payload, payload_hash=validated.payload_hash, coverage=dict(coverage),
                      structure_validation=validated.structure_validation.value,
                      human_validation=HumanValidation.unverified.value,
                      expires_at=now + timedelta(days=get_ai_settings().result_retention_days))
    db.add(result)
    db.flush()
    stored = validated.payload.get('findings') or []
    if len(stored) != len(validated.findings):
        raise ValueError('지적 수가 맞지 않습니다.')
    for finding, raw in zip(validated.findings, stored):
        loc = finding.location
        db.add(AIFinding(
            result_id=result.id, ordinal=finding.ordinal, priority=finding.priority, category=finding.category,
            title=finding.title, detail=dict(raw), source_file_id=UUID(raw['file_id']), side=loc.side.value,
            start_line=loc.start_line, end_line=loc.end_line,
            fingerprint=finding_fingerprint(loc.path, loc.side.value, loc.start_line, loc.end_line, finding.category,
                                            finding.title)))
    db.flush()
    return result.id
