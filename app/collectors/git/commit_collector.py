from git import Repo


def collect_commits(repo_path: str):

    repo = Repo(repo_path)

    commits = []

    for commit in repo.iter_commits():

        commits.append({
            "hash": commit.hexsha,
            "author": commit.author.name,
            "email": commit.author.email,
            "message": commit.message,
            "date": str(commit.committed_datetime)
        })

    return commits