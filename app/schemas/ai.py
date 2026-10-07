"""AI 기능 공통 계약 DTO (S0).

수집·백엔드·시각화가 같은 정의를 사용한다. enum 값과 필드 의미는 단계가 바뀌어도
임의로 바꾸지 않으며, 변경은 docs/AI_INTERFACE_CONTRACT.md의 버전 규칙을 따른다.
"""
import hashlib
import json
import re
from datetime import date, datetime
from enum import Enum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    model_validator,
)

CONTRACT_VERSION = "1.3"

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
    CREDENTIAL_REQUIRED = "CREDENTIAL_REQUIRED"
    CREDENTIAL_INVALID = "CREDENTIAL_INVALID"
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
    ErrorCode.CREDENTIAL_REQUIRED: 422,
    ErrorCode.CREDENTIAL_INVALID: 422,
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
    """전송 전 로컬 추정(보수적). 제공자 토큰 수 확인은 사용자 동의 뒤 실행 직전에 다시 한다."""
    input_tokens: int = Field(ge=0)
    method: str
    final_check: str = "before_generation"
    profile: str | None = None
    limit: int | None = None                 # profile의 입력 토큰 한도
    within_limit: bool | None = None         # False면 이 입력으로는 AI 생성을 시작할 수 없다
    max_output_tokens: int | None = None
    estimated_max_cost: str | None = None    # 입력 추정 + 최대 출력 기준 비용 상한(예약액)
    currency: str | None = None


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
    transfer: 'TransferView | None' = None       # 선택된 제공자·모델·key(동의 화면에 표시)


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


# ── 모델 출력 schema (S6) ───────────────────────────────────────────────
# 모델이 만드는 JSON의 구조다. 구조가 맞아도 내용이 맞다는 뜻은 아니며, 파일·줄 참조는 validators가 실제 전송한
# 자료와 대조한다. 모델은 줄 번호를 지어낼 수 있으므로 위치는 전송한 줄 번호 안에서만 인정한다.

SUMMARY_SCHEMA_VERSION = "summary-output-v1"
REVIEW_SCHEMA_VERSION = "review-output-v1"
MAX_FINDINGS = 10


class _StrictOutput(BaseModel):
    """모델 출력 전용: 타입을 강제 변환하지 않는다('1'이나 true를 정수로 받아들이지 않는다)."""
    model_config = ConfigDict(extra="forbid", strict=True)

MAX_FILE_CHANGES = 100

Handle = Annotated[str, StringConstraints(pattern=r"^F[0-9]{1,4}$")]
_Text = Annotated[str, StringConstraints(min_length=1, max_length=1500)]
_Quote = Annotated[str, StringConstraints(min_length=3, max_length=300)]


class ModelLocation(_StrictOutput):
    path: RepoPath
    side: Literal["LEFT", "RIGHT"]
    start_line: int = Field(ge=1, le=10_000_000)
    end_line: int = Field(ge=1, le=10_000_000)
    quote: _Quote            # 그 줄 범위에서 그대로 가져온 코드 조각(근거 확인용)


class ModelFinding(_StrictOutput):
    title: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    category: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{1,39}$")]
    priority: int = Field(ge=1, le=1000)       # 검토 순서. 장애 등급이 아니다.
    condition: _Text
    impact: _Text
    evidence_ids: list[Handle] = Field(min_length=1, max_length=5)
    location: ModelLocation
    verification: _Text
    suggestion: _Text | None = None
    limitations: list[Annotated[str, StringConstraints(min_length=1, max_length=300)]] = Field(
        default_factory=list, max_length=3)


class ReviewOutput(_StrictOutput):
    schema_version: str
    findings: list[ModelFinding] = Field(max_length=MAX_FINDINGS)

    @model_validator(mode="after")
    def _version(self):
        if self.schema_version != REVIEW_SCHEMA_VERSION:
            raise ValueError("schema_version이 올바르지 않습니다.")
        return self


class FileChange(_StrictOutput):
    path: RepoPath
    summary: Annotated[str, StringConstraints(min_length=1, max_length=800)]
    evidence_ids: list[Handle] = Field(min_length=1, max_length=5)


class SummaryOutput(_StrictOutput):
    schema_version: str
    overview: Annotated[str, StringConstraints(min_length=1, max_length=2000)]       # 코드에서 관찰한 사실
    author_intent: Annotated[str, StringConstraints(min_length=1, max_length=1000)] | None = None   # PR 설명이 말하는 의도
    file_changes: list[FileChange] = Field(max_length=MAX_FILE_CHANGES)
    unknowns: list[Annotated[str, StringConstraints(min_length=1, max_length=500)]] = Field(max_length=20)
    evidence_ids: list[Handle] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def _version(self):
        if self.schema_version != SUMMARY_SCHEMA_VERSION:
            raise ValueError("schema_version이 올바르지 않습니다.")
        return self


# ── 사용자 API key(BYOK)·제공자 선택·전송 동의 (S6.5) ───────────────────────────
# API key는 요청 본문으로만 들어오고(SecretStr: 실수로 로그에 찍혀도 가려진다) 응답에는 마스킹된 힌트만 나간다.

PROFILE_ID_PATTERN = r"^[a-z0-9][a-z0-9._-]{2,63}$"
ProfileId = Annotated[str, StringConstraints(pattern=PROFILE_ID_PATTERN)]


class SupportedModel(_Strict):
    profile_id: str
    provider: str
    model: str
    display_name: str
    currency: str
    input_price_per_mtok: str
    output_price_per_mtok: str
    price_version: str
    context_window: int
    max_input_tokens: int
    max_output_tokens: int
    allowed: bool = True          # 서버 관리자가 허용한 profile인가


class SupportedProvider(_Strict):
    id: str
    display_name: str
    key_format: str
    models: list[SupportedModel]


class ProvidersResponse(_Strict):
    providers: list[SupportedProvider]
    external_transfer_allowed: bool


class RegisterKeyRequest(_Strict):
    api_key: SecretStr = Field(min_length=16, max_length=400)
    profile_id: ProfileId | None = None       # 이 key로 기본 사용할 모델(지원 목록의 profile)
    verify: bool = True                        # 제공자에 key를 확인한다(코드는 보내지 않는다)


class CredentialView(_Strict):
    provider: str
    status: Literal["active"] = "active"
    version: int = Field(ge=1)
    key_hint: str                              # 마스킹된 힌트(sk-ant-…a1b2). 전체 key는 어디에도 돌려주지 않는다
    preferred_profile_id: str | None = None
    validated_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    in_flight_jobs: int = Field(ge=0, default=0)


class CredentialChange(_Strict):
    credential: CredentialView
    changed: bool                              # 같은 key를 다시 보냈으면 false(새 버전을 만들지 않는다)
    previous_version: int | None = None


class CredentialList(_Strict):
    credentials: list[CredentialView]


class CredentialDeleted(_Strict):
    provider: str
    status: Literal["deleted"] = "deleted"
    jobs_cancelled: int = Field(ge=0)


class TransferConsent(_Strict):
    """전송 동의: 사용자가 확인한 코드 범위(manifest_hash)와 제공자·모델·key 버전."""
    input_id: UUID
    manifest_hash: Sha256Hex
    provider: str
    profile_id: ProfileId
    credential_version: int = Field(ge=1)
    policy_version: ShortText


class TransferView(_Strict):
    """미리보기에서 사용자가 확인할 전송 대상(동의 화면용)."""
    provider: str
    profile_id: str
    model: str
    external_transfer_allowed: bool
    credential_configured: bool
    credential_version: int | None = None
    key_hint: str | None = None


InputPreview.model_rebuild()   # 뒤에 정의된 TransferView 참조를 확정한다


# ── 생성·결과·피드백 API (S8) ───────────────────────────────────────────

class CreateJobRequest(_Strict):
    """POST /jobs: 동의한 전송 내용(코드 범위·제공자·모델·key 버전)으로 설명·리뷰 생성을 접수한다."""
    kind: Literal["summary", "review"]
    consent: TransferConsent
    force_regenerate: bool = False        # 이미 성공한 같은 설정의 결과가 있어도 새 generation을 만든다


class RetryJobRequest(_Strict):
    """POST /jobs/{id}/retry: 실패·취소된 작업의 명시적 재생성. 비용·전송이 다시 생기므로 동의를 다시 받는다."""
    consent: TransferConsent


class JobView(JobState):
    """작업 상태 응답. stage는 실행 중일 때만 있고 모델 진행률은 알려주지 않는다."""
    repository_id: int
    input_id: UUID
    generation: int = Field(ge=0)
    provider: str | None = None
    model: str | None = None
    outcome_uncertain: bool = False       # 호출 결과를 몰라 비용이 이미 발생했을 수 있다
    created_at: datetime
    finished_at: datetime | None = None


class JobList(_Strict):
    jobs: list[JobView]
    next_cursor: str | None = None


class ResultView(_Strict):
    """GET /results/{id}: 검증을 통과한 불변 결과. human_validation은 모델 결과의 사람 검토 상태(저장 시 unverified)다."""
    result_id: UUID
    job_id: UUID
    kind: Literal["summary", "review"]
    repository_id: int
    input_id: UUID
    payload: dict[str, Any]
    payload_hash: Sha256Hex
    coverage: Coverage
    structure_validation: StructureValidation
    human_validation: HumanValidation
    provider: str | None = None
    model: str | None = None
    finding_count: int = Field(ge=0)
    created_at: datetime
    expires_at: datetime


class FeedbackRequest(_Strict):
    verdict: Verdict
    note: Annotated[str, StringConstraints(max_length=2000)] | None = None
    expected_version: int = Field(ge=0)    # 처음이면 0, 수정이면 마지막으로 본 version


class FeedbackView(_Strict):
    verdict: Verdict
    note: str | None = None
    version: int = Field(ge=1)
    updated_at: datetime


class FindingView(_Strict):
    finding_id: UUID
    result_id: UUID
    source_file_id: UUID
    finding: Finding                       # human_validation에는 내 피드백이 반영된다(원본 결과는 바뀌지 않는다)
    quote: str | None = None
    feedback: FeedbackView | None = None


class FindingList(_Strict):
    result_id: UUID
    findings: list[FindingView]
    next_cursor: str | None = None


class UsageView(_Strict):
    period_start: date
    currency: str
    user_limit: str
    user_spent: str
    user_reserved: str
    user_remaining: str
    server_budget_enabled: bool
    uncertain_reservations: int = Field(ge=0)
    daily_jobs_used: int = Field(ge=0)
    daily_job_limit: int = Field(ge=0)
