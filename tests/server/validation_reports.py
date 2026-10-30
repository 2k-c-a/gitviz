"""Paths for new validation runs; historical evidence is never a default output."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from uuid import uuid4


REPORT_ROOT = Path(__file__).resolve().parents[2] / 'reports' / 'validation'


def new_report_path(kind: str) -> Path:
    if not re.fullmatch(r'[a-z][a-z0-9-]*', kind):
        raise ValueError('Report kind must be a lowercase filename label.')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    return REPORT_ROOT / f'{kind}-{stamp}-{uuid4().hex[:8]}.json'


def write_validation_report(kind: str, payload: object) -> Path:
    path = new_report_path(kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
    return path
