from git import Repo
from app.utils.normalize import normalize_hash


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
