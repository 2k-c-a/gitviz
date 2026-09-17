# app/collectors/git/file_collector.py
import subprocess
import os
from app.utils.normalize import normalize_path, resolve_rename_path

def collect_files(repo_path: str):
    result = subprocess.run(
        ["git", "-c", "core.quotePath=false", "log", "--all", "--numstat", "--pretty=format:"],
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
        path = normalize_path(resolve_rename_path(parts[2].strip()))
        if path:
            path_set.add(path)

    files = []
    for path in path_set:
        extension = os.path.splitext(path)[1]
        if extension.startswith("."):
            extension = extension[1:]
        files.append({"path": path, "extension": extension or None})

    return files


def collect_current_tree_paths(repo_path: str, branch: str) -> set[str]:
    """
    collect_files()는 --all(모든 브랜치 전체 이력)에서 한 번이라도 등장한 경로를
    전부 모으므로 이미 삭제/이동되어 현재 기본 브랜치에는 없는 경로도 섞여 있다.
    이 함수는 <branch> HEAD 시점의 실제 존재 파일만 반환한다 — files.is_deleted를
    채워서 "디렉토리 트리"·GitHub 링크가 삭제된 경로를 가리키지 않게 하는 데 쓴다.
    """
    result = subprocess.run(
        ["git", "-c", "core.quotePath=false", "ls-tree", "-r", "--name-only", branch],
        cwd=repo_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return {
        normalize_path(line)
        for line in result.stdout.splitlines()
        if line.strip()
    }
