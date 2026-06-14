# app/collectors/git/file_collector.py
import subprocess
import os
from app.utils.normalize import normalize_path

def collect_files(repo_path: str):
    result = subprocess.run(
        ["git", "log", "--all", "--numstat", "--pretty=format:"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )

    path_set = set()
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        path = normalize_path(parts[2].strip())
        if path:
            path_set.add(path)

    files = []
    for path in path_set:
        extension = os.path.splitext(path)[1]
        if extension.startswith("."):
            extension = extension[1:]
        files.append({"path": path, "extension": extension or None})

    return files
