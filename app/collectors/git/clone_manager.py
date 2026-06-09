from git import Repo
import os

def clone_repository(github_url: str, repo_name: str):

    repo_path = f"storage/repos/{repo_name}"

    print("CHECK PATH:", repo_path)

    if os.path.exists(repo_path):
        import shutil
        shutil.rmtree(repo_path)

    print("START CLONE")

    Repo.clone_from(
        github_url,
        repo_path
    )

    print("END CLONE")

    return repo_path