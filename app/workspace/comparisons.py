"""Snapshot measurements with explicit denominators and conservative comparisons."""
from __future__ import annotations

import ast
import base64
import difflib
import io
import json
import tokenize
import uuid
from collections import Counter
from typing import Any

from .patches import validate_encoding
from .scoped_files import ScopeError
from .snapshots import fingerprint, image_hash
from .validation import Validation


def measure(files: dict[str, bytes], typescript: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    symbols = complexity = 0
    python_files = 0
    unsupported: list[str] = []
    windows: list[str] = []
    duplication_files = 0
    for path, raw in files.items():
        if not path.endswith('.py'):
            unsupported.append(path)
            continue
        try:
            validate_encoding(path, raw)
            tree = ast.parse(raw.decode('utf-8-sig'))
            nodes = list(ast.walk(tree))
            if len(nodes) > 30000:
                raise ValueError()
        except (ValueError, SyntaxError, UnicodeError, RecursionError, ScopeError):
            unsupported.append(path)
            continue
        python_files += 1
        functions = [item for item in nodes if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))]
        symbols += len(functions)
        # Explicit AST branch count, not a claim about runtime or maintainability.
        complexity += len(functions) + sum(isinstance(item, (ast.If, ast.For, ast.AsyncFor, ast.While,
            ast.ExceptHandler, ast.IfExp, ast.comprehension)) for item in nodes)
        complexity += sum(max(0, len(item.values) - 1) for item in nodes if isinstance(item, ast.BoolOp))
        try:
            ignored = {tokenize.ENCODING, tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER}
            tokens = [(token.type, token.string) for token in tokenize.generate_tokens(io.StringIO(raw.decode('utf-8-sig')).readline) if token.type not in ignored]
            count = max(0, len(tokens) - 19)
            if len(tokens) > 60000 or len(windows) + count > 60000:
                raise ValueError()
            windows.extend(fingerprint(tokens[index:index + 20]) for index in range(count))
            duplication_files += 1
        except (tokenize.TokenError, ValueError, IndentationError):
            pass  # AST remains measured; duplication coverage is reported separately.
    counts = Counter(windows)
    js = [record for record in typescript or [] if record['functionCount'] is not None]
    js_duplication = [record for record in typescript or [] if record['tokenWindows'] is not None]
    js_paths = {record['path'] for record in js}
    js_windows = sum(record['tokenWindows'] for record in js_duplication)
    def duplication(total: int, repeated: int) -> dict[str, Any] | None:
        return {'tokenWindows': total, 'duplicatedTokenWindows': repeated, 'windowSize': 20,
            'normalization': 'comments-whitespace-only-exact-token-text'} if total else None
    return {'bytes': sum(map(len, files.values())), 'files': len(files), 'lines': sum(len(raw.splitlines()) for raw in files.values()),
        'pythonFunctionCount': symbols if python_files else None, 'pythonBranchMetric': complexity if python_files else None,
        'denominatorPythonFiles': python_files, 'unsupported': [path for path in unsupported if path not in js_paths],
        'javascriptFunctionCount': sum(record['functionCount'] for record in js) if js else None,
        'javascriptBranchMetric': sum(record['branchMetric'] for record in js) if js else None,
        'denominatorJavascriptFiles': len(js), 'denominatorPythonDuplicationFiles': duplication_files,
        'denominatorJavascriptDuplicationFiles': len(js_duplication),
        'javascriptDuplication': duplication(js_windows, sum(record['duplicateWindows'] for record in js_duplication)),
        'toolVersion': 'gitviz-snapshot-metrics-v1', 'pythonParserVersion': 'python-ast-v1',
        'javascriptParserVersion': 'typescript-6.0.3-metrics-v1', 'duplication': duplication(len(windows), sum(counts[window] > 1 for window in windows)),
        'limitations': ['selected-after-images-only', 'python-ast-branch-count-not-runtime-complexity',
            'branch-metric-is-functions-plus-file-level-branches', 'duplication-is-exact-token-windows-not-semantic-clones',
            'null-metric-has-no-supported-denominator', 'no-benchmark-no-speed-claim']}


class Comparisons:
    def __init__(self, validation: Validation):
        self.validation = validation
        self.snapshots = validation.snapshots
        self.db = validation.db

    def files(self, project: str, snapshot_id: str) -> tuple[dict[str, Any], dict[str, bytes]]:
        snapshot = self.snapshots.get(project, snapshot_id)
        files: dict[str, bytes] = {}
        for file in snapshot['files']:
            if file['state'] != 'read' or file['path'] in files:
                raise ScopeError('PATCH_SCOPE_UNSUPPORTED')
            row = self.db.execute('SELECT payload FROM change_file WHERE project_id=? AND snapshot_id=? AND id=?', (project, snapshot_id, file['fileId'])).fetchone()
            body = json.loads(self.snapshots.cipher.decrypt(row[0], self.snapshots.aad(project, snapshot_id, file['fileId'])))
            files[file['path']] = base64.b64decode(body['after'], validate=True) if body['after'] is not None else b''
        return snapshot, files

    def create(self, project: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
        self.validation.drain()
        def action() -> dict[str, Any]:
            before, old = self.files(project, payload['beforeSnapshotId'])
            after, new = self.files(project, payload['afterSnapshotId'])
            measurements = payload.get('typescriptMeasurements', {'before': None, 'after': None})
            for snapshot, sources, records in ((before, old, measurements['before']), (after, new, measurements['after'])):
                if records is None:
                    continue
                seen: set[str] = set()
                for record in records:
                    matches = [file for file in snapshot['files'] if file['fileId'] == record['fileId']]
                    if len(matches) != 1 or record['fileId'] in seen:
                        raise ScopeError('OUTPUT_INVALID')
                    seen.add(record['fileId'])
                    file = matches[0]
                    if (file['path'] != record['path'] or file['facet'] != record['facet'] or image_hash(sources[file['path']]) != record['sourceHash']
                            or record['toolVersion'] != 'typescript-6.0.3-metrics-v1'
                            or not file['path'].endswith(('.js', '.jsx', '.mjs', '.cjs', '.ts', '.tsx', '.mts', '.cts'))):
                        raise ScopeError('OUTPUT_INVALID')
                    if (record['functionCount'] is None) != (record['branchMetric'] is None):
                        raise ScopeError('OUTPUT_INVALID')
                    if (record['tokenWindows'] is None) != (record['duplicateWindows'] is None):
                        raise ScopeError('OUTPUT_INVALID')
                    if record['tokenWindows'] is not None and record['duplicateWindows'] > record['tokenWindows']:
                        raise ScopeError('OUTPUT_INVALID')
            proposal_id = payload['proposalId']
            purpose = None
            expected_effects: list[str] = []
            remaining_risks: list[str] = []
            if proposal_id is not None:
                row = self.db.execute('SELECT snapshot_id,payload FROM proposal WHERE project_id=? AND id=?', (project, proposal_id)).fetchone()
                if row is None or row[0] != before['snapshotId']:
                    raise ScopeError('PRECONDITION_FAILED')
                proposal = json.loads(self.snapshots.cipher.decrypt(row[1], f'GitVizWorkspace/project/{project}/proposal/{proposal_id}/schema-5'.encode()))
                purpose = proposal['purpose']
                expected_effects = proposal['expectedEffects']
                remaining_risks = proposal['risks']
            additions = deletions = changed = 0
            line_count = 0
            diff_measured = True
            for path in sorted(old.keys() | new.keys()):
                left, right = old.get(path, b''), new.get(path, b'')
                changed += left != right
                left_lines, right_lines = left.splitlines(), right.splitlines()
                line_count += len(left_lines) + len(right_lines)
                if max(len(left_lines), len(right_lines)) > 4000 or line_count > 20000:
                    diff_measured = False
                    continue
                for operation, i, j, k, end in difflib.SequenceMatcher(None, left_lines, right_lines, autojunk=True).get_opcodes():
                    if operation in ('delete', 'replace'):
                        deletions += j - i
                    if operation in ('insert', 'replace'):
                        additions += end - k
            reasons: list[str] = []
            if {(file['path'], file['facet']) for file in before['files']} != {(file['path'], file['facet']) for file in after['files']}:
                reasons.append('different-selected-facets')
            if old.keys() != new.keys():
                reasons.append('different-selected-paths')
            runs: list[dict[str, Any] | None] = []
            for name, snapshot in (('beforeRunId', before), ('afterRunId', after)):
                run_id = payload[name]
                if run_id is None:
                    reasons.append('baseline-not-executed' if name == 'beforeRunId' else 'after-not-executed')
                    runs.append(None)
                    continue
                run = self.validation.get(project, {'runId': run_id, 'offset': 0, 'limit': 1}, drain=False)
                if run['snapshotId'] != snapshot['snapshotId']:
                    raise ScopeError('PRECONDITION_FAILED')
                if run['status'] not in ('succeeded', 'failed') or run.get('inputChanged') is not False:
                    reasons.append('run-incomplete-or-input-unverified')
                runs.append(run)
            if all(run is not None for run in runs):
                left_run, right_run = runs
                assert left_run is not None and right_run is not None
                left_plan = self.validation.plan(project, left_run['planId'])['plan']
                right_plan = self.validation.plan(project, right_run['planId'])['plan']
                for field in ('executable', 'argv', 'cwdRelative', 'configHash', 'timeoutMs', 'outputLimit'):
                    if left_plan[field] != right_plan[field]:
                        reasons.append('different-command-or-config')
                        break
                for field in ('runtime', 'environmentHash', 'platform', 'configHash', 'dependencyFingerprint', 'unselectedFingerprint'):
                    if left_run['conditions'][field] != right_run['conditions'][field]:
                        reasons.append('different-runtime-or-environment')
                        break
                if any(run['conditions'][field] is None for run in (left_run, right_run) for field in ('dependencyFingerprint', 'unselectedFingerprint')):
                    reasons.append('dependency-environment-not-fully-fingerprinted')
            comparison = str(uuid.uuid4())
            result = {'comparisonId': comparison, 'projectId': project, 'beforeSnapshotId': before['snapshotId'], 'afterSnapshotId': after['snapshotId'],
                'proposalId': proposal_id, 'beforeInputHash': before['inputHash'], 'afterInputHash': after['inputHash'],
                'before': measure(old, measurements['before']), 'after': measure(new, measurements['after']), 'changedFiles': changed,
                'additions': additions if diff_measured else None, 'deletions': deletions if diff_measured else None,
                'diffToolVersion': 'difflib-sequence-matcher-autojunk-v1', 'diffLimitations': [] if diff_measured else ['diff-line-bound-not-measured'],
                'purpose': purpose, 'expectedEffects': expected_effects, 'remainingRisks': remaining_risks,
                'validationOutcomes': {'before': runs[0]['status'] if runs[0] is not None else None, 'after': runs[1]['status'] if runs[1] is not None else None},
                'validationStatus': 'not_comparable' if reasons else 'comparable', 'reasons': sorted(set(reasons)),
                'beforeRunId': payload['beforeRunId'], 'afterRunId': payload['afterRunId'],
                'testTransitions': None, 'performanceChange': None, 'qualityImprovement': None,
                'limitations': ['no-causal-quality-or-performance-claim', 'test-identity-unavailable', 'environment-equivalence-is-the-recorded-metadata-profile-only']}
            self.db.execute('INSERT INTO comparison VALUES(?,?,?,?,?,?,?,?)', (comparison, project, before['snapshotId'], after['snapshotId'],
                proposal_id, payload['beforeRunId'], payload['afterRunId'], self.validation.encrypt(project, comparison, result)))
            return {'comparisonId': comparison}
        reference = self.validation.projects.mutate(project, 'comparisons.create', key, payload, action)
        return self.get(project, reference)

    def get(self, project: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.validation.projects.grant(project)
        row = self.db.execute('SELECT payload FROM comparison WHERE project_id=? AND id=?', (project, payload['comparisonId'])).fetchone()
        if row is None:
            raise ScopeError('RESOURCE_NOT_FOUND')
        return self.validation.decrypt(project, payload['comparisonId'], row[0])
