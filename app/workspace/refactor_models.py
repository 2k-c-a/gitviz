"""Independent refactor output; targets are calculated by the agent, never trusted from a model."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, ValidationError

from .contracts import ProtocolError, decode
from .patches import MAX_FILES, apply_patch_bytes
from .review_models import StrictModel
from .scoped_files import ScopeError
from .snapshots import image_hash


class RefactorChange(StrictModel):
    fileId: str = Field(min_length=36, max_length=36)
    facet: Literal['staged', 'unstaged', 'untracked', 'selected']
    path: str = Field(min_length=1, max_length=2048)
    preimageHash: str = Field(pattern=r'^[0-9a-f]{64}$')
    patch: str = Field(min_length=1, max_length=24000)


class RefactorOutput(StrictModel):
    schemaVersion: Literal['workspace-refactor-v1']
    purpose: str = Field(min_length=1, max_length=1000)
    expectedEffects: list[str] = Field(min_length=1, max_length=5)
    risks: list[str] = Field(min_length=1, max_length=5)
    validationSuggestions: list[str] = Field(min_length=1, max_length=5)
    changes: list[RefactorChange] = Field(min_length=1, max_length=MAX_FILES)
    limitations: list[str] = Field(max_length=10)


def output_schema(version: str) -> dict[str, Any]:
    if version == 'workspace-refactor-v1':
        return RefactorOutput.model_json_schema()
    if version == 'workspace-review-v1':
        from .review_models import ReviewOutput
        return ReviewOutput.model_json_schema()
    raise ScopeError('UNSUPPORTED_KIND')


def validate_refactor(raw: bytes, sent: list[dict[str, Any]]) -> dict[str, Any]:
    if len(raw) > 65536:
        raise ScopeError('OUTPUT_LIMIT')
    try:
        value = RefactorOutput.model_validate(decode(raw)).model_dump()
    except (ValidationError, ProtocolError, ValueError):
        raise ScopeError('OUTPUT_INVALID') from None
    known = {file['fileId']: file for file in sent}
    paths: set[str] = set()
    changed_lines = 0
    for change in value['changes']:
        source = known.get(change['fileId'])
        if (source is None or source['facet'] != change['facet'] or source['path'] != change['path']
                or change['path'].casefold() in paths):
            raise ScopeError('OUTPUT_INVALID')
        paths.add(change['path'].casefold())
        original = source['source'].encode()
        if image_hash(original) != change['preimageHash'] or change['preimageHash'] != source['preimageHash']:
            raise ScopeError('PRECONDITION_FAILED')
        target = apply_patch_bytes(change['path'], original, change['patch'])
        changed_lines += sum(line.startswith(('+', '-')) for line in change['patch'].split('\n')[2:])
        if changed_lines > 120:
            raise ScopeError('PATCH_TOO_LARGE')
        change['targetHash'] = image_hash(target)
    texts = [*value['expectedEffects'], *value['risks'], *value['validationSuggestions'], *value['limitations']]
    if any(not text or len(text) > 1000 for text in texts):
        raise ScopeError('OUTPUT_INVALID')
    return value
