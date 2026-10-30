from __future__ import annotations

import dataclasses
import importlib.util
import subprocess
import sys
import unittest
from types import SimpleNamespace
from pathlib import Path
from typing import Any
from uuid import UUID

from app.workspace.adapters import ai_core


class AiCoreTests(unittest.TestCase):
    def test_legacy_assembler_pr_commit_hash_and_profile_equality(self) -> None:
        schemas = importlib.import_module('app.schemas.ai')
        profile = ai_core.profile('gpt-5.4-v1')
        metadata = schemas.PRTransferMetadata(title='fixture', body='explicit metadata')
        manifest = schemas.ManifestFile(path='app.py', change_type='modified', content_state='read', content_hash='a' * 64,
            old_blob_oid='a' * 40, new_blob_oid='b' * 40)
        collected = SimpleNamespace(files=[SimpleNamespace(manifest=manifest, payload=b'x=2')], total_bytes=3,
            coverage=schemas.Coverage(inventory_coverage='complete', selected_content_coverage='complete', analysis_coverage='none', selected_files=1, pr_total_files=1))
        for source in ('pr', 'commit'):
            revision = schemas.PRRevision(repo_id=1, pr_number=1 if source == 'pr' else None, source_type=source,
                base_sha='a' * 40, head_sha='b' * 40, merge_base_sha='a' * 40)
            options = {'revision': revision, 'revision_id': UUID(int=77), 'metadata': metadata}
            expected = ai_core.builder.assemble(collected, profile=profile, **options)
            actual = ai_core.legacy_assemble(collected, profile_value=profile, **options)
            self.assertEqual(actual.manifest_hash, expected.manifest_hash)
            self.assertEqual(actual.input_hash, expected.input_hash)
            self.assertEqual(actual.token_estimate, expected.token_estimate)
        self.assertNotIn('app.core.ai_settings', sys.modules)

    def test_exercised_core_has_no_server_or_network_import(self) -> None:
        code = '''
import sys,os
os.environ['AI_OLLAMA_MODEL']='SERVER_ENV_MUST_NOT_LEAK'
from app.workspace.adapters import ai_core
assert ai_core.profile('ollama-local-v1').model != 'SERVER_ENV_MUST_NOT_LEAK'
assert ai_core.exclusion('a.py', b'x=1', b'x=2') is None
assert ai_core.profile('gpt-5.4-v1').id == 'gpt-5.4-v1'
for name in ('app.main','app.database','app.core.database','app.core.ai_settings','app.services.ai.access', 'app.collectors.git.clone_manager','sqlalchemy','celery','dotenv','httpx','requests'):
 assert name not in sys.modules, name
'''
        result = subprocess.run([sys.executable, '-B', '-c', code], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_existing_pr_commit_validation_and_policy_equality(self) -> None:
        path = Path(__file__).resolve().parents[2] / 'tests/server/test_ai_output_validation.py'
        spec = importlib.util.spec_from_file_location('p04_golden_validation', path)
        assert spec is not None and spec.loader is not None
        golden = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(golden)
        sent, _ = golden.make_sent()
        for revision in (UUID(int=77), UUID(int=88)):
            for output in (golden.review(golden.finding()), golden.review(golden.finding(location={'path': 'other.py', 'side': 'RIGHT', 'start_line': 1, 'end_line': 2, 'quote': 'invented'}))):
                expected = golden.validate_output('review', output, sent, revision_id=revision)
                actual = ai_core.legacy_validate('review', output, sent, revision_id=revision)
                self.assertEqual(dataclasses.asdict(actual), dataclasses.asdict(expected))
                self.assertEqual(actual.human_validation.value, 'unverified')
        for path_name, before, after in [('a.py', b'x=1', b'x=2'), ('a.py', b'password="abcdefghijklmnop"', b'redacted=1'), ('a.min.js', b'generated', b'generated'), ('.env', None, b'never-read')]:
            expected: Any = ai_core.policy.path_exclusion(path_name)
            if expected is None:
                expected = next((r for value in (before, after) if value is not None for r in [ai_core.policy.content_exclusion(value.decode(), byte_count=len(value))] if r is not None), None)
            self.assertEqual(ai_core.exclusion(path_name, before, after), expected.value if expected else None)

    def test_profile_provider_port_is_injected_without_execution(self) -> None:
        value = ai_core.profile('ollama-local-v1', {'AI_OLLAMA_MODEL': 'explicit-local-fixture'})
        self.assertEqual(value.model, 'explicit-local-fixture')
        calls: list[str] = []
        def fake(profile: Any) -> dict[str, str]:
            calls.append(profile.id)
            return {'provider': profile.provider}
        self.assertEqual(ai_core.provider_port(value, fake), {'provider': 'ollama'})
        self.assertEqual(calls, ['ollama-local-v1'])
