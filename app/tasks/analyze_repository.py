from app.core.celery_app import celery
from app.core.database import SessionLocal

from app.models.repository import Repository
from app.models.commit import Commit
from app.models.file import File
from app.models.contributor import Contributor
from app.models.commit_file_change import CommitFileChange

from app.collectors.git.clone_manager import clone_repository
from app.collectors.git.commit_collector import collect_commits
from app.collectors.git.file_collector import collect_files
from app.collectors.git.commit_file_change_collector import collect_commit_file_changes


@celery.task
def analyze_repository(repository_id: int):

    print("🔥 TASK STARTED:", repository_id)

    db = SessionLocal()

    try:
        # -----------------------
        # 1. Repository load
        # -----------------------
        repository = db.query(Repository).filter(
            Repository.id == repository_id
        ).first()

        if not repository:
            print("❌ Repository not found")
            return

        print("📦 repository:", repository.github_url)

        repository.status = "PROCESSING"
        db.commit()

        # -----------------------
        # 2. Clone repo
        # -----------------------
        repo_name = repository.github_url.split("/")[-1]

        repo_path = clone_repository(
            repository.github_url,
            repo_name
        )

        print("📁 repo_path:", repo_path)

        # -----------------------
        # 3. Collect data
        # -----------------------
        print("START collect_commits")
        commits = collect_commits(repo_path)
        print("END collect_commits")

        print("START collect_files")
        files = collect_files(repo_path)
        print("END collect_files")

        print("START collect_commit_file_changes")
        changes = collect_commit_file_changes(repo_path)
        print("END collect_commit_file_changes")

        # debug sample
        print("🔍 sample changes:")
        for c in changes[:5]:
            print(c)

        # -----------------------
        # 4. Contributors (FIXED)
        # -----------------------
        contributor_cache = set()

        for c in commits:

            email = c["email"]

            if email not in contributor_cache:

                contributor = db.query(Contributor).filter(
                    Contributor.primary_email == email,
                    Contributor.repository_id == repository.id
                ).first()

                if not contributor:
                    contributor = Contributor(
                        repository_id=repository.id,
                        primary_email=email,
                        display_name=c["author"]
                    )
                    db.add(contributor)

                contributor_cache.add(email)

        # -----------------------
        # 5. Commits
        # -----------------------
        commit_map = {}

        print("START SAVE COMMITS")
        
        for c in commits:

            existing_commit = db.query(Commit).filter(
                Commit.commit_hash == c["hash"]
            ).first()

            if existing_commit:
                commit_map[c["hash"]] = existing_commit
                continue

            commit_obj = Commit(
                repository_id=repository.id,
                commit_hash=c["hash"],
                author_name=c["author"],
                author_email=c["email"],
                message=c["message"],
                committed_at=c["date"]
            )

            db.add(commit_obj)
            db.flush()  # 중요

            commit_map[c["hash"]] = commit_obj
            
        print("END SAVE COMMIT")
        # -----------------------
        # 6. Files
        # -----------------------
        file_map = {}
        
        print("START SAVE FILES")
        
        for f in files:

            path = f["path"]

            existing_file = db.query(File).filter(
                File.repository_id == repository.id,
                File.path == path
            ).first()

            if existing_file:
                file_map[path] = existing_file
                continue

            file_obj = File(
                repository_id=repository.id,
                path=path,
                extension=f["extension"]
            )

            db.add(file_obj)
            db.flush()

            file_map[path] = file_obj
        print("END SAVE FILES")
        db.commit()

        # -----------------------
        # 7. Commit File Changes (FIXED CORE)
        # -----------------------
        print("START SAVE CHANGES")

        processed_changes = 0

        for change in changes:

            processed_changes += 1

            if processed_changes % 10000 == 0:
                print("processed changes:", processed_changes)

            commit = commit_map.get(change["commit_hash"])
            file = file_map.get(change["path"])

            if not commit or not file:
                continue

            existing_change = db.query(CommitFileChange).filter(
                CommitFileChange.commit_id == commit.id,
                CommitFileChange.file_id == file.id
            ).first()

            if existing_change:
                continue

            db.add(
                CommitFileChange(
                    commit_id=commit.id,
                    file_id=file.id,
                    additions=change["additions"],
                    deletions=change["deletions"]
                )
            )

        print("END SAVE CHANGES")
        db.commit()

        # -----------------------
        # 8. Done
        # -----------------------
        repository.status = "DONE"
        db.commit()

        print("✅ TASK COMPLETED")

    except Exception as e:
        import traceback
        
        print("ERROR:",e)
        traceback.print_exc()
        
        db.rollback()

    finally:
        db.close()