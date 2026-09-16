from git import Repo
import os
import subprocess


def get_default_branch(repo_path: str) -> str | None:
    """로컬 clone은 원격 저장소의 기본 브랜치를 checkout한 상태로 시작하므로
    HEAD가 가리키는 브랜치명이 곧 기본 브랜치(main/master/그 외)다.
    GitHub REST 호출 없이 이미 clone된 저장소에서 바로 알아낼 수 있다."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        branch = result.stdout.strip()
        return branch if branch and branch != "HEAD" else None
    except Exception:
        return None


def clone_repository(github_url: str, repo_name: str):

    repo_path = f"storage/repos/{repo_name}"

    print("CHECK PATH:", repo_path)

    if os.path.exists(repo_path):
        print("REPO ALREADY EXISTS")
        return repo_path

    print("START CLONE")

    Repo.clone_from(
        github_url,
        repo_path
    )

    print("END CLONE")

    return repo_path