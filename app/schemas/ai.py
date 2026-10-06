"""AI 기능 공통 계약 DTO (S0).

수집·백엔드·시각화가 같은 정의를 사용한다. enum 값과 필드 의미는 단계가 바뀌어도
임의로 바꾸지 않으며, 변경은 docs/AI_INTERFACE_CONTRACT.md의 버전 규칙을 따른다.
"""
import hashlib
import json
import re
from datetime import datetime
from enum import Enum
from typing import Annotated, Any
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

CONTRACT_VERSION = "1.1"

_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


def _check_sha(value: str) -> str:
    if not _SHA_RE.fullmatch(value):
        raise ValueError("Git 객체 ID(소문자 hex 40 또는 64자)가 아닙니다.")
    return value


def _check_hex64(value: str) -> str:
    if not _HEX64_RE.fullmatch(value):
        raise ValueError("sha256 hex 64자가 아닙니다.")
    return value


def _check_repo_path(value: str) -> str:
    if not value or value != value.strip():
        raise ValueError("경로가 비었거나 공백으로 시작/끝납니다.")
    if "\x00" in value or "\\" in value or value.startswith("/"):
        raise ValueError("저장소 상대 경로(/ 구분)여야 합니다.")
    if any(part in ("", ".", "..") for part in value.split("/")):
        raise ValueError("경로에 빈 구간·'.'·'..'을 사용할 수 없습니다.")
    return value


GitSha = Annotated[str, AfterValidator(_check_sha)]
Sha256Hex = Annotated[str, AfterValidator(_check_hex64)]
RepoPath = Annotated[str, AfterValidator(_check_repo_path)]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=500)]
LongText = Annotated[str, StringConstraints(min_length=1, max_length=5000)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ── enum ────────────────────────────────────────────────────────────────

class JobKind(str, Enum):
    prepare = "prepare"
    summary = "summary"
    review = "review"


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    retry_wait = "retry_wait"
    cancel_requested = "cancel_requested"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


TERMINAL_JOB_STATUSES = frozenset({JobStatus.succeeded, JobStatus.failed, JobStatus.cancelled})


class JobStage(str, Enum):
    input_preparation = "input_preparation"
    queued = "queued"
    provider_call = "provider_call"
    output_validation = "output_validation"
    result_storage = "result_storage"


class InputStatus(str, Enum):
    building = "building"
    ready = "ready"
    failed = "failed"
    expired = "expired"


class InventoryCoverage(str, Enum):
    complete = "complete"
    partial = "partial"
    unknown = "unknown"


class ContentCoverage(str, Enum):
    """selected_content_coverage / analysis_coverage 공용 값."""
    complete = "complete"
    partial = "partial"
    none = "none"


class Side(str, Enum):
    LEFT = "LEFT"
    RIGHT = "RIGHT"


class ChangeType(str, Enum):
    added = "added"
    modified = "modified"
    deleted = "deleted"
    renamed = "renamed"


class ContentState(str, Enum):
    read = "read"
    excluded = "excluded"
    failed = "failed"


class Limitation(str, Enum):
    """제외·누락 사유. 파일별 exclusion_reason과 coverage.limitations가 공유한다."""
    not_selected = "not_selected"
    unsupported_language = "unsupported_language"
    too_large = "too_large"
    truncated = "truncated"
    generated_file = "generated_file"
    binary = "binary"
    link_or_submodule = "link_or_submodule"
    secret_suspected = "secret_suspected"
    fetch_failed = "fetch_failed"
    analysis_failed = "analysis_failed"


class StructureValidation(str, Enum):
    passed = "passed"
    failed = "failed"


class HumanValidation(str, Enum):
    unverified = "unverified"
    valid = "valid"
    false_positive = "false_positive"
    uncertain = "uncertain"


class Verdict(str, Enum):
    valid = "valid"
    false_positive = "false_positive"
    uncertain = "uncertain"


class ErrorCode(str, Enum):
    AUTH_REQUIRED = "AUTH_REQUIRED"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"
    TRANSFER_NOT_ALLOWED = "TRANSFER_NOT_ALLOWED"
    CSRF_FAILED = "CSRF_FAILED"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    PR_CHANGED = "PR_CHANGED"
    PREVIEW_CHANGED = "PREVIEW_CHANGED"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    INPUT_EXPIRED = "INPUT_EXPIRED"
    RESULT_EXPIRED = "RESULT_EXPIRED"
    INPUT_TOO_LARGE = "INPUT_TOO_LARGE"
    INVALID_SELECTION = "INVALID_SELECTION"
    INVALID_STATE = "INVALID_STATE"
    USER_LIMIT = "USER_LIMIT"
    BUDGET_LIMIT = "BUDGET_LIMIT"
    UPSTREAM_RATE_LIMIT = "UPSTREAM_RATE_LIMIT"
    ACCESS_CHECK_UNAVAILABLE = "ACCESS_CHECK_UNAVAILABLE"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"


ERROR_HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.AUTH_REQUIRED: 401,
    ErrorCode.REAUTH_REQUIRED: 401,
    ErrorCode.TRANSFER_NOT_ALLOWED: 403,
    ErrorCode.CSRF_FAILED: 403,
    ErrorCode.RESOURCE_NOT_FOUND: 404,
    ErrorCode.PR_CHANGED: 409,
    ErrorCode.PREVIEW_CHANGED: 409,
    ErrorCode.VERSION_CONFLICT: 409,
    ErrorCode.IDEMPOTENCY_CONFLICT: 409,
    ErrorCode.INPUT_EXPIRED: 410,
    ErrorCode.RESULT_EXPIRED: 410,
    ErrorCode.INPUT_TOO_LARGE: 413,
    ErrorCode.INVALID_SELECTION: 422,
    ErrorCode.INVALID_STATE: 422,
    ErrorCode.USER_LIMIT: 429,
    ErrorCode.BUDGET_LIMIT: 429,
    ErrorCode.UPSTREAM_RATE_LIMIT: 429,
    ErrorCode.ACCESS_CHECK_UNAVAILABLE: 503,
    ErrorCode.UPSTREAM_UNAVAILABLE: 503,
}


# ── 리비전·위치 ─────────────────────────────────────────────────────────

class PRRevision(_Strict):
    repo_id: int = Field(gt=0)
    pr_number: int = Field(gt=0)
    base_sha: GitSha
    head_sha: GitSha
    merge_base_sha: GitSha


class CodeLocation(_Strict):
    """줄 위치. 화면 순번이나 모델이 만든 번호가 아니라 실제 commit의 줄 번호다."""
    path: RepoPath
    side: Side
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    revision_id: UUID

    @model_validator(mode="after")
    def _ordered(self):
        if self.end_line < self.start_line:
            raise ValueError("end_line은 start_line 이상이어야 합니다.")
        return self


# ── coverage ────────────────────────────────────────────────────────────

class Coverage(_Strict):
    """목록 확보·선택 코드 확보·실제 분석 범위를 분리해 표현한다. job.status와 별개."""
    inventory_coverage: InventoryCoverage
    selected_content_coverage: ContentCoverage
    analysis_coverage: ContentCoverage
    selected_files: int = Field(ge=0)
    pr_total_files: int | None = Field(default=None, ge=0)
    limitations: list[Limitation] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self):
        if self.inventory_coverage is InventoryCoverage.unknown and self.pr_total_files is not None:
            raise ValueError("목록 범위가 unknown이면 pr_total_files는 null이어야 합니다.")
        if self.inventory_coverage is InventoryCoverage.complete and self.pr_total_files is None:
            raise ValueError("목록 범위가 complete이면 pr_total_files가 필요합니다.")
        if self.pr_total_files is not None and self.selected_files > self.pr_total_files:
            raise ValueError("selected_files가 pr_total_files보다 클 수 없습니다.")
        if self.selected_content_coverage is ContentCoverage.none and \
                self.analysis_coverage is not ContentCoverage.none:
            raise ValueError("코드를 확보하지 못한 범위는 분석 범위가 될 수 없습니다.")
        if self.selected_content_coverage is ContentCoverage.partial and \
                self.analysis_coverage is ContentCoverage.complete:
            raise ValueError("일부만 확보한 코드를 complete로 분석할 수 없습니다.")
        if len(set(self.limitations)) != len(self.limitations):
            raise ValueError("limitations에 중복이 있습니다.")
        return self


# ── 입력 manifest ───────────────────────────────────────────────────────

class Hunk(_Strict):
    old_start: int = Field(ge=0)
    old_lines: int = Field(ge=0)
    new_start: int = Field(ge=0)
    new_lines: int = Field(ge=0)


class ManifestFile(_Strict):
    path: RepoPath
    old_path: RepoPath | None = None
    change_type: ChangeType
    old_blob_oid: GitSha | None = None
    new_blob_oid: GitSha | None = None
    content_hash: Sha256Hex | None = None
    language: str | None = Field(default=None, max_length=64)
    content_state: ContentState
    exclusion_reason: Limitation | None = None
    hunks: list[Hunk] = Field(default_factory=list)

    @property
    def sides(self) -> frozenset[Side]:
        """줄을 가질 수 있는 side. 삭제 파일은 LEFT만, 새 파일은 RIGHT만."""
        if self.change_type is ChangeType.deleted:
            return frozenset({Side.LEFT})
        if self.change_type is ChangeType.added:
            return frozenset({Side.RIGHT})
        return frozenset({Side.LEFT, Side.RIGHT})

    @model_validator(mode="after")
    def _consistent(self):
        ct = self.change_type
        if ct is ChangeType.added and (self.old_path is not None or self.old_blob_oid is not None):
            raise ValueError("새 파일은 old_path/old_blob_oid를 가질 수 없습니다.")
        if ct is ChangeType.deleted and self.new_blob_oid is not None:
            raise ValueError("삭제 파일은 new_blob_oid를 가질 수 없습니다.")
        if ct is ChangeType.renamed:
            if self.old_path is None or self.old_path == self.path:
                raise ValueError("이름 변경 파일은 서로 다른 old_path가 필요합니다.")
        elif self.old_path is not None and self.old_path != self.path:
            raise ValueError("old_path는 이름 변경 파일에만 사용합니다.")
        if self.content_state is ContentState.read:
            if self.content_hash is None:
                raise ValueError("읽은 파일은 content_hash가 필요합니다.")
            if self.exclusion_reason is not None:
                raise ValueError("읽은 파일에 exclusion_reason을 둘 수 없습니다.")
        else:
            if self.exclusion_reason is None:
                raise ValueError("제외·실패 파일은 exclusion_reason이 필요합니다.")
            if self.content_hash is not None:
                raise ValueError("읽지 않은 파일에 content_hash를 둘 수 없습니다.")
        return self


class PRTransferMetadata(_Strict):
    """AI에 함께 보내는 PR 설명. manifest에 포함되어 manifest_hash에 반영된다."""
    title: Annotated[str, StringConstraints(max_length=1000)] = ""
    body: Annotated[str, StringConstraints(max_length=8000)] = ""
    body_truncated: bool = False


class InputManifest(_Strict):
    """사용자에게 미리보기로 보이고 실제 AI 전송 후보와 같아야 하는 목록."""
    contract_version: str = CONTRACT_VERSION
    revision_id: UUID
    revision: PRRevision
    policy_version: ShortText
    pr_metadata_hash: Sha256Hex | None = None
    pr_metadata: PRTransferMetadata | None = None
    files: list[ManifestFile]

    @model_validator(mode="after")
    def _unique_paths(self):
        paths = [f.path for f in self.files]
        if len(set(paths)) != len(paths):
            raise ValueError("manifest에 같은 path가 중복됩니다.")
        if self.pr_metadata is not None and self.pr_metadata_hash != canonical_hash(self.pr_metadata):
            raise ValueError("pr_metadata_hash가 pr_metadata와 일치하지 않습니다.")
        return self


def canonical_json(obj: BaseModel | dict[str, Any]) -> bytes:
    """키 순서·공백과 무관한 결정적 직렬화(UTF-8)."""
    data = obj.model_dump(mode="json") if isinstance(obj, BaseModel) else obj
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def canonical_hash(obj: BaseModel | dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(obj)).hexdigest()


# ── 요청 ────────────────────────────────────────────────────────────────

class PrepareInputRequest(_Strict):
    """POST /inputs. owner는 서버가 세션에서 정한다."""
    repository_id: int = Field(gt=0)
    pr_number: int = Field(gt=0)
    expected_base_sha: GitSha
    expected_head_sha: GitSha
    selected_paths: list[RepoPath] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def _unique(self):
        if len(set(self.selected_paths)) != len(self.selected_paths):
            raise ValueError("selected_paths에 중복이 있습니다.")
        return self


# ── 작업·결과 ───────────────────────────────────────────────────────────

class JobState(_Strict):
    job_id: UUID
    kind: JobKind
    status: JobStatus
    stage: JobStage | None = None
    coverage: Coverage
    result_id: UUID | None = None
    retry_after_seconds: int | None = Field(default=None, ge=0)
    cancel_requested: bool = False
    error_code: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _consistent(self):
        if self.kind is JobKind.prepare:
            if self.result_id is not None:
                raise ValueError("prepare 작업은 AI 결과가 없으므로 result_id를 둘 수 없습니다.")
        elif self.status is JobStatus.succeeded and self.result_id is None:
            raise ValueError("succeeded 생성 작업은 result_id가 필요합니다.")
        elif self.status is not JobStatus.succeeded and self.result_id is not None:
            raise ValueError("성공하지 않은 작업에 result_id를 둘 수 없습니다.")
        if self.status is JobStatus.failed and not self.error_code:
            raise ValueError("failed 작업은 error_code가 필요합니다.")
        if self.status is JobStatus.succeeded and self.error_code is not None:
            raise ValueError("succeeded 작업에 error_code를 둘 수 없습니다.")
        if self.status in TERMINAL_JOB_STATUSES and self.stage is not None:
            raise ValueError("종료된 작업에는 stage가 없습니다.")
        return self


class Finding(_Strict):
    """리뷰 지적. priority는 검토 순서(1이 먼저)이며 장애 등급이 아니다."""
    ordinal: int = Field(ge=1)
    title: ShortText
    category: ShortText
    priority: int = Field(ge=1)
    condition: LongText
    impact: LongText
    evidence_ids: list[ShortText] = Field(min_length=1)
    verification: LongText
    suggestion: LongText | None = None
    limitations: list[Limitation] = Field(default_factory=list)
    location: CodeLocation
    human_validation: HumanValidation = HumanValidation.unverified


class ErrorBody(_Strict):
    code: ErrorCode
    message: ShortText
    retryable: bool
    request_id: ShortText
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelope(_Strict):
    error: ErrorBody


# ── 입력 준비·전송 미리보기 (S5) ────────────────────────────────────────

class DiffLineKind(str, Enum):
    context = "context"
    added = "added"
    removed = "removed"


class DiffLine(_Strict):
    kind: DiffLineKind
    left_line: int | None = Field(default=None, ge=1)     # 변경 전(LEFT) 줄 번호. 추가된 줄은 없다.
    right_line: int | None = Field(default=None, ge=1)    # 변경 후(RIGHT) 줄 번호. 삭제된 줄은 없다.
    text: str


class TokenEstimate(_Strict):
    """전송 전 대략적인 입력 크기. 모델 profile별 정확한 확인은 실행 직전에 다시 한다."""
    input_tokens: int = Field(ge=0)
    method: str
    final_check: str = "before_generation"


class InputFileView(_Strict):
    file_id: UUID
    path: RepoPath
    old_path: RepoPath | None = None
    change_type: ChangeType
    language: str | None = None
    content_state: ContentState
    exclusion_reason: Limitation | None = None
    side_availability: str
    byte_count: int = Field(ge=0)
    content_hash: Sha256Hex | None = None
    hunks: list[Hunk] = Field(default_factory=list)


class PrepareJobView(_Strict):
    job_id: UUID
    status: JobStatus
    stage: JobStage | None = None
    error_code: str | None = None


class InputPreview(_Strict):
    """GET /inputs/{id}: 사용자에게 보이는 전송 후보. 실제 AI 전송 후보와 같은 manifest다."""
    input_id: UUID
    status: InputStatus
    repository_id: int
    pr_number: int
    base_sha: GitSha
    head_sha: GitSha
    merge_base_sha: GitSha | None = None
    pr_state: str
    pr_title: str = ""
    pr_body: str = ""
    pr_body_truncated: bool = False
    coverage: Coverage
    files: list[InputFileView] = Field(default_factory=list)
    manifest_hash: Sha256Hex | None = None       # 생성 요청의 consent_manifest_hash로 되돌려 보낸다
    policy_version: str
    provider_profile: str | None = None
    token_estimate: TokenEstimate | None = None
    valid_until: datetime | None = None          # 이 시각까지 새 실행을 시작할 수 있다
    expires_at: datetime                         # 이 시각에 원문 코드가 삭제된다
    execution_valid: bool = False
    raw_available: bool = False
    reviewable: bool = False                     # 읽은 파일이 하나 이상이어서 AI에 보낼 내용이 있다
    prepare_job: PrepareJobView | None = None


class InputFileContent(_Strict):
    """GET /inputs/{id}/files/{file_id}: 보관된 고정 diff(맥락 포함). 작성자만 볼 수 있다."""
    file_id: UUID
    path: RepoPath
    old_path: RepoPath | None = None
    change_type: ChangeType
    language: str | None = None
    side_availability: str
    content_state: ContentState
    exclusion_reason: Limitation | None = None
    content_hash: Sha256Hex | None = None
    lines: list[DiffLine] = Field(default_factory=list)
