"""작업 처리권(lease)과 그 상실 예외. job_service와 usage_service가 공유한다."""
from dataclasses import dataclass
from uuid import UUID


class LeaseLost(Exception):
    """처리권이 없거나 만료·교체됐다. 이 처리자는 상태를 확정할 수 없다."""


@dataclass
class Lease:
    job_id: UUID
    token: UUID
    attempt_no: int
    owner_user_id: UUID
    repository_id: int
    kind: str
