"""Read-only frozen module/secret-file audit and SHA256 inventory. No app imports or execution."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader

FORBIDDEN = ('app.main', 'app.core', 'app.api', 'app.models', 'sqlalchemy', 'fastapi', 'dotenv', 'requests', 'httpx', 'httpx2', 'anthropic', 'celery')
REQUIRED = ('app.workspace.crypto', 'app.workspace.migrations', 'app.workspace.remote_connections', 'app.workspace.cloud_context', 'app.workspace.cloud_transport', 'app.workspace.impact')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, default=Path('build-output/agent/dist/agent'))
    parser.add_argument('--output', type=Path)
    options = parser.parse_args()
    root = options.directory
    if root.is_symlink() or getattr(root.lstat(), 'st_file_attributes', 0) & 0x400:
        raise ValueError('BUNDLE_REPARSE_POINT')
    executable = root / 'gitviz-workspace-agent.exe'
    archive = CArchiveReader(str(executable))
    embedded = archive.open_embedded_archive(next(name for name in archive.toc if name.endswith('.pyz')))
    modules = sorted(embedded.toc)
    if any(any(name == prefix or name.startswith(prefix + '.') for name in modules) for prefix in FORBIDDEN):
        raise ValueError('SERVER_MODULE_IN_AGENT')
    if any(name not in modules for name in REQUIRED):
        raise ValueError('REQUIRED_MODULE_MISSING')
    files: dict[str, str] = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400:
            raise ValueError('BUNDLE_REPARSE_POINT')
        relative = path.relative_to(root)
        if any(part.lower() in ('.git', '.validation', 'userdata') for part in relative.parts):
            raise ValueError('PRIVATE_DATA_IN_BUNDLE')
        if path.is_file():
            name = path.name.lower()
            if name == '.env' or name.startswith('.env.') or name == 'secret-check.bin' or name.endswith(('.dpapi', '.sqlite', '.sqlite3', '.db')):
                raise ValueError('PRIVATE_DATA_IN_BUNDLE')
            with path.open('rb') as stream:
                files[relative.as_posix()] = hashlib.file_digest(stream, 'sha256').hexdigest()
    report = {'classification': 'static-bundle-inventory-not-vulnerability-or-license-audit', 'moduleCount': len(modules),
              'forbiddenServerModules': False, 'privateDataFiles': False, 'modules': modules, 'sha256': files}
    if options.output:
        with options.output.open('x', encoding='utf8') as stream:
            json.dump(report, stream, indent=2)
            stream.write('\n')
    print(f'PASS agent bundle: modules={len(modules)} files={len(files)} server modules/private data absent')


if __name__ == '__main__':
    main()
