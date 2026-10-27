"""Read-only checks for organized documentation and preserved originals.

Run from any directory: python -B docs/audit/check_documents.py
No application imports, environment files, database, network, or writes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
BASELINE = DOCS / "audit" / "preservation-baseline.json"
RELOCATIONS = DOCS / "audit" / "relocation-map.json"
LINK = re.compile(r"(?<!!)\[[^\]\n]*\]\(([^)\n]+)\)")


def local_target(source: Path, value: str) -> tuple[Path, str] | None:
    value = value.strip()
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1]
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        return None
    target = (source.parent / unquote(parsed.path)).resolve() if parsed.path else source
    if not target.is_relative_to(ROOT):
        raise ValueError("link leaves the repository")
    return target, unquote(parsed.fragment)


def anchors(body: str) -> set[str]:
    """Support the simple headings and explicit anchors used in this doc set."""
    found = set(re.findall(r'<a\s+id=["\']([^"\']+)["\']', body))
    duplicates: dict[str, int] = {}
    fenced = False
    for line in body.splitlines():
        if line.startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if not match:
            continue
        heading = match[1].replace("`", "").lower()
        slug = re.sub(r"[^\w\- ]", "", heading).replace(" ", "-")
        count = duplicates.get(slug, 0)
        duplicates[slug] = count + 1
        found.add(f"{slug}-{count}" if count else slug)
    return found


def check() -> int:
    errors: list[str] = []
    try:
        manifest = json.loads(BASELINE.read_text(encoding="utf-8-sig"))
        protected = manifest["files"]
        if manifest["schema_version"] != 1 or not isinstance(protected, dict) or not protected:
            raise ValueError("invalid preservation manifest")
        relocation = json.loads(RELOCATIONS.read_text(encoding="utf-8-sig"))
        records = relocation["files"]
        if relocation["schema_version"] != 1 or not isinstance(records, dict) or set(records) != set(protected):
            raise ValueError("relocation map must cover exactly the original baseline")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"FAIL preservation manifest: {exc}")
        return 1

    archived: set[Path] = set()
    for name, expected in protected.items():
        record = records[name]
        if not isinstance(record, dict) or record.get("archive") != f"docs/archive/originals/{name}":
            errors.append(f"invalid archive mapping: {name}")
            continue
        original = (ROOT / name).resolve()
        target = (ROOT / record["archive"]).resolve()
        if not original.is_relative_to(ROOT) or not target.is_relative_to(ROOT / "docs/archive/originals") or Path(name).is_absolute():
            errors.append(f"unsafe baseline path: {name}")
            continue
        archived.add(target)
        if not target.is_file():
            errors.append(f"missing archived original: {name}")
        elif hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            errors.append(f"changed archived original: {name}")
        current = record.get("current")
        if current is not None:
            if not isinstance(current, str):
                errors.append(f"invalid current mapping: {name}")
                continue
            maintained = (ROOT / current).resolve()
            if not maintained.is_relative_to(ROOT) or not maintained.is_file():
                errors.append(f"missing or unsafe current mapping: {name}")
        if original.exists() and current != name:
            errors.append(f"stale original path remains: {name}")

    pages = {p.resolve() for p in DOCS.rglob("*.md")}
    if not pages:
        errors.append("no Markdown documents found")
    readme = (ROOT / "README.md").resolve()
    if readme.is_file():
        pages.add(readme)
    bodies = {p: p.read_text(encoding="utf-8-sig") for p in pages}
    edges: dict[Path, set[Path]] = {p: set() for p in pages}
    links_checked = 0
    for source, body in bodies.items():
        for value in LINK.findall(body):
            try:
                resolved = local_target(source, value)
            except ValueError as exc:
                errors.append(f"{source.relative_to(ROOT)}: {value}: {exc}")
                continue
            if resolved is None:
                continue
            links_checked += 1
            target, fragment = resolved
            if not target.is_file():
                errors.append(f"{source.relative_to(ROOT)}: missing link {value}")
                continue
            if target in pages:
                edges[source].add(target)
            if fragment and target.suffix == ".md":
                target_body = bodies.get(target)
                if target_body is None:
                    target_body = target.read_text(encoding="utf-8-sig")
                if fragment not in anchors(target_body):
                    errors.append(f"{source.relative_to(ROOT)}: missing anchor {value}")

    inventory = (DOCS / "audit" / "inventory.md").resolve()
    listed = set()
    for value in LINK.findall(bodies.get(inventory, "")):
        try:
            resolved = local_target(inventory, value)
        except ValueError:
            continue
        if resolved is not None:
            listed.add(resolved[0])
    for name, record in records.items():
        if (ROOT / record["archive"]).resolve() not in listed:
            errors.append(f"original absent from inventory links: {name}")

    start = (DOCS / "index.md").resolve()
    visited: set[Path] = set()
    pending = [start]
    while pending:
        page = pending.pop()
        if page in visited:
            continue
        visited.add(page)
        pending.extend(edges.get(page, set()) - visited)
    for page in sorted(pages - visited):
        errors.append(f"unreachable from docs/index.md: {page.relative_to(ROOT)}")

    if errors:
        for error in errors:
            print(f"FAIL {error}")
        return 1
    print(f"PASS relocated original hashes: {len(protected)}")
    print(f"PASS inventory coverage: {len(protected)}")
    print(f"PASS Markdown pages reachable: {len(pages)} (including archive and README)")
    print(f"PASS local links and anchors: {links_checked}")
    print("Scope: document structure and preservation only; no policy, DB, or live validation.")
    return 0


if __name__ == "__main__":
    sys.exit(check())
