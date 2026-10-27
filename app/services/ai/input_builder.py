"""고정 입력 조립: 전송 후보 manifest·해시·토큰 예상·diff 줄 보기. DB·네트워크를 쓰지 않는다.

- manifest_hash: 사용자가 미리본 manifest(PR 설명·파일별 content_hash·언어·제외 사유 포함)의 해시.
  생성 요청의 consent_manifest_hash로 되돌아와 실제 전송 후보와 같은지 확인하는 데 쓴다.
- input_hash: 실행 fingerprint에 들어가는 입력 해시(manifest + 순서 + 문맥 크기 + 수집 방식).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from app.collectors.ai_errors import CollectionError
from app.schemas.ai import (Coverage, ErrorCode, InputManifest, PRRevision,
                            PRTransferMetadata, TokenEstimate, canonical_hash)
from app.services.ai import token_budget
from app.services.ai.providers.registry import current_profile
from app.services.ai.difftext import diff_lines  # noqa: F401  (S5 호환: 여기서도 가져다 쓴다)
from app.services.ai.content_policy import POLICY_VERSION
if TYPE_CHECKING:
    from app.services.ai.access import AIError

COLLECTOR_VERSION = 'change-diff-context25-v2'
TOKEN_METHOD = 'local-conservative-v1'


@dataclass
class BuiltInput:
    manifest: InputManifest
    manifest_hash: str
    input_hash: str
    coverage: Coverage
    token_estimate: TokenEstimate


def transfer_metadata(metadata: dict) -> PRTransferMetadata:
    """수집한 PR 메타데이터에서 AI로 보내는 부분(제목·설명)만 고른다."""
    return PRTransferMetadata(title=str(metadata.get('title') or '')[:1000], body=str(metadata.get('body') or '')[:8000],
                              body_truncated=bool(metadata.get('body_truncated')))


def prepare_fingerprint(revision_fingerprint: str, selected_paths: list[str], metadata: PRTransferMetadata,
                        policy_version: str = POLICY_VERSION) -> str:
    """입력 준비 작업의 fingerprint. 같은 비교 SHA라도 선택·PR 설명·정책이 다르면 다른 입력이다."""
    return canonical_hash({'kind': 'prepare', 'revision': revision_fingerprint, 'paths': sorted(selected_paths),
                           'metadata': canonical_hash(metadata), 'policy': policy_version,
                           'collector': COLLECTOR_VERSION})


def estimate_tokens(payload_bytes: int, metadata: PRTransferMetadata, file_count: int = 0, *,
                    profile=None) -> TokenEstimate:
    """전송 미리보기의 토큰·비용 추정. 사용자가 고른 profile(없으면 기본)의 한도와 비교해 한도 초과 여부를 함께 알린다."""
    profile = profile or current_profile()
    tokens = token_budget.estimate_input_tokens(
        profile, 'review', payload_bytes=payload_bytes, description_chars=len(metadata.title) + len(metadata.body),
        file_count=file_count)
    return TokenEstimate(input_tokens=tokens, method=TOKEN_METHOD, profile=profile.id,
                         limit=profile.max_input_tokens, within_limit=tokens <= profile.max_input_tokens,
                         max_output_tokens=profile.max_output_tokens,
                         estimated_max_cost=str(token_budget.max_cost(profile, tokens)), currency=profile.currency)


def assemble(collected, *, revision: PRRevision, revision_id: UUID, metadata: PRTransferMetadata,
             policy_version: str = POLICY_VERSION, profile=None) -> BuiltInput:
    manifest = InputManifest(revision_id=revision_id, revision=revision, policy_version=policy_version,
                             pr_metadata_hash=canonical_hash(metadata), pr_metadata=metadata,
                             files=[f.manifest for f in collected.files],
                             changed_files=getattr(collected, 'changed_files', None))
    manifest_hash = canonical_hash(manifest)
    input_hash = canonical_hash({'manifest': manifest_hash, 'order': [f.manifest.path for f in collected.files],
                                 'collector': COLLECTOR_VERSION})
    return BuiltInput(manifest=manifest, manifest_hash=manifest_hash, input_hash=input_hash,
                      coverage=collected.coverage,
                      token_estimate=estimate_tokens(collected.total_bytes, metadata,
                                                     sum(1 for f in collected.files if f.payload is not None), profile=profile))


def remote_url_for(repo_url: str) -> str:
    """검증된 canonical GitHub URL(https://github.com/owner/repo)로 git remote를 만든다."""
    return repo_url.rstrip('/') + '.git'


CODE_BY_COLLECTION_ERROR = {ErrorCode.INVALID_STATE: 'MERGE_BASE_UNAVAILABLE'}


def error_code_for(exc: CollectionError) -> str:
    """작업 error_code(문자열). 원문 예외 메시지는 담지 않는다."""
    return CODE_BY_COLLECTION_ERROR.get(exc.code, exc.code.value)


def to_ai_error(exc: CollectionError) -> AIError:
    from app.services.ai.access import AIError
    headers = {}
    retry_after = getattr(exc, 'retry_after', None)
    if retry_after:
        headers['Retry-After'] = str(retry_after)
    return AIError(exc.code, exc.message, retryable=exc.retryable, headers=headers)


# Stable collector contract; importing this pure assembler must not load the
# server's clone manager, DB authorization or Git credentials.
CONTEXT_LINES = 25
