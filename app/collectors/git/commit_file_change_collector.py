import subprocess
import os

from app.utils.normalize import resolve_rename_path

def normalize_hash(h: str) -> str:
    if not h:
        return ""
    return h.strip().lower()

def normalize_path(p: str) -> str:
    if not p:
        return ""
    p = p.strip()
    p = p.replace("\\", "/")
    p = os.path.normpath(p).replace("\\", "/")
    if p == ".":
        return ""
    return p

def collect_commit_file_changes(repo_path: str, since: str = None):
    """
    Returns list of dicts:
    {
    "commit_hash": "<full-lowercase-sha>",
    "path": "<normalized-path>",
    "additions": int,
    "deletions": int
    }

    since(ISO 날짜)가 주어지면 그 이후 커밋의 변경 내역만 읽는다(증분).
    겹침은 DB upsert(ON CONFLICT)가 걸러낸다.
    """
    changes = []

    print("ENTER collect_commit_file_changes")

    cmd = ["git", "-c", "core.quotePath=false", "log", "--all", "--numstat", "--pretty=format:COMMIT:%H"]
    if since:
        cmd.append(f"--since={since}")

    # Use text mode with errors='replace' to avoid silent data loss
    result = subprocess.run(
        cmd,
        cwd=repo_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )

    current_commit = None
    processed = 0

    for line in result.stdout.splitlines():
        if not line:
            continue

        if line.startswith("COMMIT:"):
            current_commit = line.replace("COMMIT:", "").strip()
            # normalize commit hash immediately
            current_commit = normalize_hash(current_commit)
            processed += 1
            if processed % 1000 == 0:
                print("processed commits:", processed)
            continue

        # numstat lines are: additions<TAB>deletions<TAB>path
        parts = line.split("\t")
        if len(parts) != 3:
            # skip non-numstat lines (e.g., merge info, rename lines)
            continue

        additions_raw = parts[0].strip()
        deletions_raw = parts[1].strip()
        path_raw = parts[2].strip()

        # handle binary files represented by '-'
        try:
            additions = 0 if additions_raw == "-" else int(additions_raw)
        except ValueError:
            additions = 0

        try:
            deletions = 0 if deletions_raw == "-" else int(deletions_raw)
        except ValueError:
            deletions = 0

        path = normalize_path(resolve_rename_path(path_raw))

        # skip empty path entries
        if not path or not current_commit:
            continue

        changes.append(
            {
                "commit_hash": current_commit,
                "path": path,
                "additions": additions,
                "deletions": deletions,
            }
        )

    print("processed commits:", processed)
    print("changes collected:", len(changes))

    return changes
