"""Reuse policy/profile/legacy validation without server DB, credentials or I/O.

Legacy PR/commit DTOs are equality ports only; local snapshot IDs are never
inserted into remote revision/repository fields. Local review execution is P05.
"""
from __future__ import annotations

import importlib
from typing import Any, Callable

policy = importlib.import_module('app.services.ai.content_policy')
builder = importlib.import_module('app.services.ai.input_builder')
validators = importlib.import_module('app.services.ai.validators')
registry = importlib.import_module('app.services.ai.providers.registry')
difftext = importlib.import_module('app.services.ai.difftext')
POLICY_VERSION: str = policy.POLICY_VERSION
MAX_FILE_BYTES: int = policy.MAX_FILE_BYTES


def exclusion(path: str, before: bytes | None, after: bytes | None,
              old_path: str | None = None, modes: tuple[str | None, str | None] = (None, None)) -> str | None:
    reason = policy.mode_exclusion(*modes) or policy.path_exclusion(path, old_path=old_path)
    if reason is not None:
        return str(reason.value)
    # Inspect complete pre/post images, including deleted/context secrets.
    for value in (before, after):
        if value is None:
            continue
        if len(value) > MAX_FILE_BYTES:
            return 'too_large'
        if b'\0' in value:
            return 'binary'
        try:
            text = value.decode('utf-8', errors='strict')
        except UnicodeError:
            return 'binary'
        reason = policy.content_exclusion(text, byte_count=len(value))
        if reason is not None:
            return str(reason.value)
    return None


def legacy_validate(kind: str, output: Any, sent: Any, **options: Any) -> Any:
    return validators.validate_output(kind, output, sent, **options)


def legacy_assemble(collected: Any, *, profile_value: Any, **options: Any) -> Any:
    return builder.assemble(collected, profile=profile_value, **options)


def profile(profile_id: str, environment: dict[str, str] | None = None) -> Any:
    return registry.get_profile(profile_id, environment=environment or {})


def provider_port(profile_value: Any, factory: Callable[[Any], Any]) -> Any:
    # A caller must inject a provider. This port never selects a network adapter.
    return factory(profile_value)
