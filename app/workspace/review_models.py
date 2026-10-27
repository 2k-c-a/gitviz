"""Local review output has its own severity schema; remote DTOs stay unchanged."""
from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .contracts import ProtocolError, decode
from .scoped_files import ScopeError


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Evidence(StrictModel):
    fileId: str = Field(min_length=36, max_length=36)
    facet: Literal['staged', 'unstaged', 'untracked', 'selected']
    path: str = Field(min_length=1, max_length=4096)
    side: Literal['LEFT', 'RIGHT']
    startLine: int = Field(ge=1, le=2147483647)
    endLine: int = Field(ge=1, le=2147483647)
    quote: str = Field(min_length=3, max_length=1000)


class Finding(StrictModel):
    category: Literal['correctness', 'security', 'performance', 'quality', 'testing']
    priority: Literal['P0', 'P1', 'P2', 'P3']
    severity: Literal['critical', 'high', 'medium', 'low', 'unknown']
    severityReason: str = Field(min_length=1, max_length=1000)
    condition: str = Field(min_length=1, max_length=1000)
    impact: str = Field(min_length=1, max_length=1000)
    evidence: list[Evidence] = Field(min_length=1, max_length=3)
    suggestion: str = Field(min_length=1, max_length=2000)
    verification: Literal['not_executed']
    limitations: list[str] = Field(max_length=10)


class ReviewOutput(StrictModel):
    schemaVersion: Literal['workspace-review-v1']
    findings: list[Finding] = Field(max_length=30)
    limitations: list[str] = Field(max_length=10)


def validate_output(raw: bytes, sent: list[dict[str, Any]]) -> dict[str, Any]:
    if len(raw) > 65536:
        raise ScopeError('OUTPUT_LIMIT')
    try:
        value = ReviewOutput.model_validate(decode(raw)).model_dump()
    except (ValidationError, ProtocolError, ValueError):
        raise ScopeError('OUTPUT_INVALID') from None
    files = {file['fileId']: file for file in sent}
    seen: set[str] = set()
    for finding in value['findings']:
        identity = json.dumps(finding, sort_keys=True)
        if identity in seen:
            raise ScopeError('OUTPUT_INVALID')
        seen.add(identity)
        for evidence in finding['evidence']:
            file = files.get(evidence['fileId'])
            if not file or file['facet'] != evidence['facet'] or file['path'] != evidence['path']:
                raise ScopeError('OUTPUT_INVALID')
            start, end = evidence['startLine'], evidence['endLine']
            if end < start or end - start > 199:
                raise ScopeError('OUTPUT_INVALID')
            side = 'left_line' if evidence['side'] == 'LEFT' else 'right_line'
            lines = {line[side]: line['text'] for line in file['lines'] if line.get(side) is not None}
            if any(line not in lines for line in range(start, end + 1)):
                raise ScopeError('OUTPUT_INVALID')
            quote = '\n'.join(lines[line] for line in range(start, end + 1))
            # Exact source excerpt, never fuzzy matching or live file reads.
            if len(' '.join(evidence['quote'].split())) < 3 or evidence['quote'] not in quote:
                raise ScopeError('OUTPUT_INVALID')
        finding['humanValidation'] = 'unverified'
    for limitation in value['limitations'] + [s for f in value['findings'] for s in f['limitations']]:
        if not limitation or len(limitation) > 1000:
            raise ScopeError('OUTPUT_INVALID')
    return value
