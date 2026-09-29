import subprocess

from git import Repo
from app.utils.normalize import normalize_hash


def collect_default_branch_shas(repo_path: str, branch: str) -> set[str]:
    """<branch>에서 도달 가능한(=GitHub 커밋 수에 포함되는) 커밋 SHA 전체.

    collect_commits()는 --all(모든 브랜치)을 읽어 stable/* 백포트처럼 기본 브랜치에
    없는 커밋까지 저장한다. 이 집합으로 commits.on_default_branch를 채워,
    화면의 커밋 수를 GitHub 표시값(기본 브랜치 기준)과 맞춘다.
    증분 수집이어도 전체를 읽는다 — 예전 커밋이 나중에 merge로 기본 브랜치에 들어올 수 있다.
    """
    result = subprocess.run(
        ["git", "rev-list", branch],
        cwd=repo_path, capture_output=True, text=True, check=True,
    )
    return {normalize_hash(line) for line in result.stdout.splitlines() if line.strip()}


def collect_commits(repo_path: str, since: str = None):
    """since(ISO 날짜 문자열)가 주어지면 그 시점 이후 커밋만 읽는다(증분).

    git --since는 committer date 기준 근사 필터다. 누락 방지 버퍼는 호출부에서
    빼서 넘기며, 최종 중복 제거는 DB upsert(ON CONFLICT)가 보장한다.
    """
    repo = Repo(repo_path)

    seen = set()
    commits = []

    rev_kwargs = {"since": since} if since else {}
    for commit in repo.iter_commits('--all', **rev_kwargs):
        sha = normalize_hash(commit.hexsha)
        if sha in seen:
            continue
        seen.add(sha)
        commits.append({
            "hash": sha,
            "author": (commit.author.name or "").strip(),
            "email": (commit.author.email or "").strip().lower(),
            "message": commit.message,
            "date": str(commit.committed_datetime)
        })

    return commits
