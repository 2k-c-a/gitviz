from app.core.celery_app import celery
from app.core.database import SessionLocal
from app.models.repository import Repository
from app.models.commit import Commit
from app.collectors.git.clone_manager import clone_repository
from app.collectors.git.commit_collector import collect_commits


@celery.task
def analyze_repository(repository_id: int):

    db = SessionLocal()

    try:
        repository = db.query(Repository).filter(
            Repository.id == repository_id
        ).first()

        repository.status = "PROCESSING"
        db.commit()

        repo_name = repository.github_url.split("/")[-1]

        repo_path = clone_repository(
            repository.github_url,
            repo_name
        )

        commits = collect_commits(repo_path)

        print("repo_path:", repo_path)
        print("commits count:", len(commits))

        for c in commits:

            existing_commit = db.query(Commit).filter(
                Commit.commit_hash == c["hash"]
            ).first()

            if existing_commit:
                continue

            db.add(
                Commit(
                    repository_id=repository.id,
                    commit_hash=c["hash"],
                    author_name=c["author"],
                    author_email=c["email"],
                    message=c["message"],
                    committed_at=c["date"]
                )
            )

        db.commit()

        repository.status = "DONE"
        db.commit()

    except Exception as e:
        print("ERROR:", e)
        db.rollback()

    finally:
        db.close()