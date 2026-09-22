"""분석 작업 진행 상황 기록 도우미 (업무지시서 3-1).

수집 태스크가 도는 동안 analysis_jobs.progress_detail(jsonb)와 status를
실시간 갱신한다. 프론트엔드가 GET status로 폴링해 진행률을 표시.

진행 상황 write는 별도 짧은 세션으로 커밋한다(메인 수집 트랜잭션과 분리해
오래 열린 트랜잭션에 묶이지 않고 폴링이 항상 최신값을 보게 하기 위함).
"""

from sqlalchemy import text
from app.core.database import SessionLocal


class ProgressReporter:
    def __init__(self, job_id: int):
        self.job_id = job_id
        self.detail = {
            "commits_collected": 0,
            "prs_collected": 0,
            "status_message": "",
            "github_rate_limit_hits": 0,
            "total_wait_seconds": 0,
            # 'collect' | 'identity' | 'derived' | 'snapshot' | 'done' — 프론트 모달
            # 파이프라인(4단계) 표시가 이 값을 기준으로 각 단계의 불을 켠다.
            "stage": "collect",
        }

    def _flush(self, status: str = None):
        """progress_detail(+선택적 status)를 짧은 세션으로 즉시 커밋."""
        db = SessionLocal()
        try:
            if status is not None:
                db.execute(
                    text("UPDATE analysis_jobs SET progress_detail = :d, status = :s WHERE id = :i"),
                    {"d": _as_json(self.detail), "s": status, "i": self.job_id},
                )
            else:
                db.execute(
                    text("UPDATE analysis_jobs SET progress_detail = :d WHERE id = :i"),
                    {"d": _as_json(self.detail), "i": self.job_id},
                )
            db.commit()
        finally:
            db.close()

    def update(self, message: str = None, **fields):
        """진행 수치/메시지 갱신 후 기록."""
        if message is not None:
            self.detail["status_message"] = message
        for k, v in fields.items():
            self.detail[k] = v
        self._flush()

    def set(self, key: str, value):
        self.detail[key] = value

    def on_rate_limit(self, wait_seconds: int):
        """GitHub 제한 발생 시: 카운터 증가 + status='rate_limited'.

        대기 시간을 문자열 메시지로 한 번만 박아두면(예: "3531초 대기 중") 실제로는
        긴 time.sleep() 한 번이 도는 동안 그 숫자가 그대로 굳어버려, 정상 대기 중인지
        워커가 죽어서 멈춘 건지 화면만 봐서 구분이 안 된다. 그래서 절대 시각
        resume_at을 저장해두고, 조회 시점(app/api/repositories.py)에 남은 시간을
        매번 다시 계산한다 — 폴링마다 자연스럽게 카운트다운되고, resume_at이 한참
        지나도 여전히 rate_limited면 "멈춘 작업"으로 판정할 수 있다.
        """
        from datetime import datetime, timezone, timedelta

        now = datetime.now(timezone.utc)
        self.detail["github_rate_limit_hits"] += 1
        self.detail["total_wait_seconds"] += wait_seconds
        self.detail["status_message"] = "GitHub rate limit — 대기 중"
        self.detail["wait_seconds"] = wait_seconds
        self.detail["rate_limited_at"] = now.isoformat()
        self.detail["resume_at"] = (now + timedelta(seconds=wait_seconds)).isoformat()
        self._flush(status="rate_limited")

    def on_resume(self):
        """대기 종료 후 running 복귀."""
        self.detail["status_message"] = "수집 재개"
        self.detail.pop("resume_at", None)
        self.detail.pop("wait_seconds", None)
        self._flush(status="running")


def _as_json(d: dict):
    import json
    return json.dumps(d, ensure_ascii=False)
