"""Conservative location extraction; raw output remains the authoritative result."""
from __future__ import annotations

import re
from typing import Any


def extract(output: list[dict[str, Any]], snapshot: dict[str, Any], root: str) -> list[dict[str, Any]]:
    known = {file['path'].replace('\\', '/').casefold(): file for file in snapshot['files'] if file['state'] == 'read'}
    locations: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int, str]] = set()
    for stream in ('stdout', 'stderr'):
        text = ''.join(chunk['text'] for chunk in output if chunk['stream'] == stream)
        text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)
        for line in text.splitlines():
            if len(line) > 8192:
                continue
            match = re.match(r'^(.+?):(\d{1,6}):(\d{1,6}):\s*(?:error\s+)?([A-Z]+\d{1,6})\b', line)
            tool = 'colon-diagnostic'
            if match is None:
                match = re.match(r'^(.+?)\((\d{1,6}),(\d{1,6})\):\s*error\s+(TS\d{1,6})\b', line)
                tool = 'typescript'
            if match is None:
                continue
            path = match[1].replace('\\', '/')
            prefix = root.replace('\\', '/').rstrip('/') + '/'
            if path.casefold().startswith(prefix.casefold()):
                path = path[len(prefix):]
            path = path.removeprefix('./')
            file = known.get(path.casefold())
            if file is None:
                continue
            row, column = int(match[2]), int(match[3])
            key = file['fileId'], row, column, match[4]
            if row < 1 or column < 1 or key in seen:
                continue
            seen.add(key)
            locations.append({'fileId': file['fileId'], 'path': file['path'], 'line': row, 'column': column,
                'columnEncoding': 'utf16' if tool == 'typescript' or match[4].startswith('TS') else 'unknown',
                'code': match[4], 'tool': tool, 'sourceHash': file['afterHash'], 'scope': 'selected-snapshot',
                'limitations': ['reported-location-not-causal-proof', 'live-file-hash-rechecked-before-editor-open']})
            if len(locations) == 5:
                return locations
    return locations
