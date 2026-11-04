"""작업 대기 만료 판단과 안전한 재시작. 수집 로직과 독립된 실행 정책."""
from datetime import datetime, timezone

ACTIVE_STATUSES = ("pending", "running", "rate_limited")
TERMINAL_STATUSES = ("done", "failed", "cancelled")
PENDING_TIMEOUT_SECONDS = 15 * 60
RUNNING_TIMEOUT_SECONDS = 6 * 3600
RATE_LIMIT_GRACE_SECONDS = 120
LOCK_NAMESPACE = 1735289201
# 분석 작업의 Celery 시간 제한. 강제 종료 시각을 응답 없음 판정(RUNNING_TIMEOUT_SECONDS)과 맞춰, 작업이
# 응답 없음으로 보일 때는 워커가 이미 끝났고 저장소 잠금도 풀려 있게 한다. 소프트 제한은 그보다 10분 먼저
# 걸려 작업을 실패로 기록할 시간을 준다. prefork 풀에서만 동작한다(solo 풀·Windows에서는 Celery가 적용하지 않음).
ANALYSIS_TIME_LIMIT_SECONDS = RUNNING_TIMEOUT_SECONDS
ANALYSIS_SOFT_TIME_LIMIT_SECONDS = RUNNING_TIMEOUT_SECONDS - 600


def _utc(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def job_staleness(job, now=None):
    now = _utc(now) or datetime.now(timezone.utc)
    result = {"remaining_seconds": None, "is_stale": False, "stale_seconds": None}
    if job.status not in ACTIVE_STATUSES:
        return result
    detail = job.progress_detail or {}
    resume = _utc(detail.get("resume_at")) if job.status == "rate_limited" else None
    if resume:
        overdue = (now - resume).total_seconds()
        result["remaining_seconds"] = max(0, int(-overdue))
        threshold = RATE_LIMIT_GRACE_SECONDS
    else:
        anchor = _utc(job.created_at) if job.status == "pending" else (_utc(job.started_at) or _utc(job.created_at))
        if anchor is None:
            return result
        overdue = (now - anchor).total_seconds()
        threshold = PENDING_TIMEOUT_SECONDS if job.status == "pending" else RUNNING_TIMEOUT_SECONDS
    if overdue > threshold:
        result.update(is_stale=True, stale_seconds=int(overdue))
    return result
