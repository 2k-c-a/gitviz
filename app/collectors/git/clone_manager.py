from git import Repo
import os


def clone_repository(github_url: str, repo_name: str):

    repo_path = f"storage/repos/{repo_name}"

    if os.path.exists(repo_path):
        return repo_path

    Repo.clone_from(
        github_url,
        repo_path
    )

    return repo_path