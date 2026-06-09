from git import Repo

def collect_commit_file_changes(repo_path: str):

    repo = Repo(repo_path)

    changes = []
    skipped = 0
    processed = 0

    print("ENTER collect_commit_file_changes")

    for commit in repo.iter_commits():

        processed += 1

        if processed % 100 == 0:
            print("processed commits:", processed)

        # 테스트용 제한
        if processed >= 1000:
            print("TEST STOP AT 1000")
            break

        try:

            stats_files = commit.stats.files

            for file, stats in stats_files.items():

                changes.append({
                    "commit_hash": commit.hexsha,
                    "path": file,
                    "additions": stats["insertions"],
                    "deletions": stats["deletions"]
                })

        except Exception:

            skipped += 1
            continue

    print("processed commits:", processed)
    print("skipped commits:", skipped)
    print("changes collected:", len(changes))

    return changes