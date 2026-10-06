"""고정 입력 조립: 전송 후보 manifest·해시·토큰 예상·diff 줄 보기. DB·네트워크를 쓰지 않는다.

- manifest_hash: 사용자가 미리본 manifest(PR 설명·파일별 content_hash·언어·제외 사유 포함)의 해시.
  생성 요청의 consent_manifest_hash로 되돌아와 실제 전송 후보와 같은지 확인하는 데 쓴다.
- input_hash: 실행 fingerprint에 들어가는 입력 해시(manifest + 순서 + 문맥 크기 + 수집 방식).
"""
import math
import re
from dataclasses import dataclass
from uuid import UUID

from app.collectors.ai_errors import CollectionError
from app.collectors.git import ai_diff_collector
from app.schemas.ai import (Coverage, DiffLine, DiffLineKind, ErrorCode, InputManifest, PRRevision,
                            PRTransferMetadata, TokenEstimate, canonical_hash)
from app.services.ai.access import AIError
from app.services.ai.content_policy import POLICY_VERSION

COLLECTOR_VERSION = 'merge-base-diff-context25-v1'
TOKEN_METHOD = 'bytes/4+overhead (mock; 모델 profile별 확인은 실행 직전에 다시 한다)'
TOKEN_OVERHEAD = 200
_HUNK_RE = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')


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


def estimate_tokens(payload_bytes: int, metadata: PRTransferMetadata) -> TokenEstimate:
    chars = len(metadata.title) + len(metadata.body)
    tokens = math.ceil(payload_bytes / 4) + math.ceil(chars / 4) + TOKEN_OVERHEAD
    return TokenEstimate(input_tokens=tokens, method=TOKEN_METHOD)


def assemble(collected, *, revision: PRRevision, revision_id: UUID, metadata: PRTransferMetadata,
             policy_version: str = POLICY_VERSION) -> BuiltInput:
    manifest = InputManifest(revision_id=revision_id, revision=revision, policy_version=policy_version,
                             pr_metadata_hash=canonical_hash(metadata), pr_metadata=metadata,
                             files=[f.manifest for f in collected.files])
    manifest_hash = canonical_hash(manifest)
    input_hash = canonical_hash({'manifest': manifest_hash, 'order': [f.manifest.path for f in collected.files],
                                 'collector': COLLECTOR_VERSION})
    return BuiltInput(manifest=manifest, manifest_hash=manifest_hash, input_hash=input_hash,
                      coverage=collected.coverage,
                      token_estimate=estimate_tokens(collected.total_bytes, metadata))


def diff_lines(text: str) -> list[DiffLine]:
    """보관한 unified diff를 줄 번호가 붙은 줄 목록으로 푼다(실제 commit의 1부터 시작하는 번호)."""
    lines, old_no, new_no, in_hunk = [], 0, 0, False
    for line in text.splitlines():
        m = _HUNK_RE.match(line)
        if m:
            old_no, new_no, in_hunk = int(m.group(1)), int(m.group(3)), True
            continue
        if not in_hunk or line.startswith('\\'):
            continue
        if line.startswith('-'):
            lines.append(DiffLine(kind=DiffLineKind.removed, left_line=old_no, text=line[1:]))
            old_no += 1
        elif line.startswith('+'):
            lines.append(DiffLine(kind=DiffLineKind.added, right_line=new_no, text=line[1:]))
            new_no += 1
        else:
            lines.append(DiffLine(kind=DiffLineKind.context, left_line=old_no, right_line=new_no, text=line[1:]))
            old_no += 1
            new_no += 1
    return lines


def remote_url_for(repo_url: str) -> str:
    """검증된 canonical GitHub URL(https://github.com/owner/repo)로 git remote를 만든다."""
    return repo_url.rstrip('/') + '.git'


CODE_BY_COLLECTION_ERROR = {ErrorCode.INVALID_STATE: 'MERGE_BASE_UNAVAILABLE'}


def error_code_for(exc: CollectionError) -> str:
    """작업 error_code(문자열). 원문 예외 메시지는 담지 않는다."""
    return CODE_BY_COLLECTION_ERROR.get(exc.code, exc.code.value)


def to_ai_error(exc: CollectionError) -> AIError:
    headers = {}
    retry_after = getattr(exc, 'retry_after', None)
    if retry_after:
        headers['Retry-After'] = str(retry_after)
    return AIError(exc.code, exc.message, retryable=exc.retryable, headers=headers)


CONTEXT_LINES = ai_diff_collector.CONTEXT_LINES
