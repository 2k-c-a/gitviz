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


def collect_existing_paths(repo_path: str):
    """현재 HEAD 트리에 실제로 존재하는 파일 경로 집합을 반환한다.

    collect_files()는 `git log --all`로 '한 번이라도 등장한' 경로를 모으므로
    이미 삭제된 파일도 포함된다. 그 차집합이 files.is_deleted 판별 근거가 된다.

    반환:
        set[str]  정상 조회된 경로 집합 (빈 커밋 레포면 빈 집합)
        None      HEAD가 없거나 git 호출이 실패해 '알 수 없음'인 경우.
                  호출측은 None이면 삭제 마킹을 건너뛰어야 한다 — 조회 실패를
                  '파일이 하나도 없음'으로 오인해 전량 삭제 처리하면 안 된다.
    """
    # 주의: -z(NUL 구분)를 쓰면 git이 경로를 따옴표로 감싸지 않아 '진짜' 경로가
    # 나오지만, collect_files()가 쓰는 `git log --numstat`은 core.quotePath 기본값
    # 때문에 비ASCII 경로를 "\354\247\200..." 형태로 이스케이프해 내보낸다.
    # files.path에는 그 이스케이프된 문자열이 저장돼 있으므로, 여기서도 같은 표현을
    # 내야 비교가 성립한다. 표현을 맞추지 않으면 한글 등 비ASCII 이름 파일이
    # HEAD에 멀쩡히 있어도 전부 삭제로 오판된다.
    result = subprocess.run(
        ["git", "ls-tree", "-r", "HEAD", "--name-only"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,          # HEAD 없는 레포에서 죽지 않도록 직접 판정
    )
    if result.returncode != 0:
        print(f"⚠️ git ls-tree 실패(rc={result.returncode}) — 삭제 마킹 건너뜀: "
              f"{(result.stderr or '').strip()[:120]}")
        return None

    return {
        norm
        for raw in result.stdout.splitlines()
        if (norm := normalize_path(raw.strip()))
    }
