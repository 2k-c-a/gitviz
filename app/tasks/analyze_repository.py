import os
from bisect import bisect_right
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

from app.core.celery_app import celery
from app.core.database import SessionLocal

from app.models.repository import Repository
from app.models.analysis_job import AnalysisJob
from app.models.commit import Commit
from app.models.file import File
from app.models.contributor import Contributor
from app.models.commit_file_change import CommitFileChange
from app.models.pull_request import PullRequest
from app.models.pr_commit import PrCommit
from app.models.pr_review import PrReview
from app.models.pull_request_file import PullRequestFile
from app.models.release import Release

from app.collectors.git.clone_manager import clone_repository, get_default_branch
from app.collectors.git.commit_collector import collect_commits
from app.collectors.git.file_collector import collect_files, collect_current_tree_paths
from app.collectors.git.commit_file_change_collector import collect_commit_file_changes
from app.collectors.git.commit_classifier import classify_commit
from app.collectors.github.github_client import GitHubClient
from app.collectors.github.pr_collector import collect_pull_requests
from app.collectors.github.graphql_pr_collector import (
    GraphQLClient,
    collect_pr_details_graphql,
    collect_pr_details_graphql_batch,
)
from app.collectors.github.release_collector import collect_releases
from app.collectors.identity.resolver import run_identity_resolution
from app.tasks.progress import ProgressReporter


# -----------------------
# 유틸: 정규화 및 헬퍼
# -----------------------
def normalize_hash(h: str) -> str:
    if not h:
        return ""
    return h.strip().lower()


def normalize_path(p: str) -> str:
    if not p:
        return ""
    p = p.strip()
    p = p.replace("\\", "/")
    p = os.path.normpath(p).replace("\\", "/")
    if p == ".":
        return ""
    return p


def chunked(iterable: List, size: int):
    """Yield successive chunks from iterable."""
    for i in range(0, len(iterable), size):
        yield iterable[i : i + size]


def _resolve_github_token(db, user_id: Optional[str]) -> Optional[str]:
    """분석을 요청한 사용자의 GitHub 토큰을 우선 사용하고, 없거나 실패하면
    공용 GITHUB_TOKEN으로 폴백한다. 사용자별 토큰을 쓰면 공용 토큰 하나를
    여러 분석이 나눠 쓰지 않고 각자 시간당 5000회 한도를 따로 가진다.

    users 테이블은 app/database.py의 별도 async Base가 관리하므로 여기(sync 태스크)
    에서는 ORM이 아니라 raw SQL로 직접 조회한다.
    """
    shared_token = os.getenv("GITHUB_TOKEN")
    if not user_id:
        return shared_token

    try:
        row = db.execute(
            text("SELECT access_token_encrypted FROM users WHERE id = CAST(:uid AS uuid)"),
            {"uid": user_id},
        ).first()
        if not row or not row[0]:
            print(f"⚠️ user_id={user_id} 토큰 없음 — 공용 토큰으로 폴백")
            return shared_token

        from app.services.auth_service import decrypt_token
        token = decrypt_token(row[0])
        print(f"🔑 user_id={user_id} 개인 GitHub 토큰 사용")
        return token
    except Exception as e:
        print(f"⚠️ 사용자 토큰 조회/복호화 실패({e}) — 공용 토큰으로 폴백")
        return shared_token


# -----------------------
# 메인 태스크
# -----------------------
@celery.task
def analyze_repository(repository_id: int, user_id: str | None = None):
    """
    Repository 분석 태스크
    - clone repository
    - collect commits, files, changes
    - normalize, dedupe, upsert (ON CONFLICT DO NOTHING)
    - commit_file_changes 저장 (upsert)

    user_id가 있으면 그 사용자의 GitHub OAuth 토큰(개인 rate limit)으로 수집하고,
    없거나 조회/복호화에 실패하면 공용 GITHUB_TOKEN으로 폴백한다.
    """
    print("🔥 TASK STARTED:", repository_id)

    db = SessionLocal()

    try:
        # 1) Repository load
        repository = db.query(Repository).filter(Repository.id == repository_id).first()
        if not repository:
            print("❌ Repository not found:", repository_id)
            return

        print("📦 repository:", repository.url)

        job = (
            db.query(AnalysisJob)
            .filter(AnalysisJob.repository_id == repository_id)
            .order_by(AnalysisJob.id.desc())
            .first()
        )
        if job:
            job.status = "running"
            job.started_at = datetime.now(timezone.utc)
        db.commit()

        # 진행 상황 보고기 (analysis_jobs.progress_detail 실시간 갱신)
        progress = ProgressReporter(job.id) if job else None

        def _report(msg=None, **fields):
            if progress:
                progress.update(message=msg, **fields)

        _report("저장소 복제 중...", stage="collect")

        # 2) Clone repo
        repo_name = repository.url.rstrip("/").split("/")[-1].removesuffix(".git")
        repo_path = clone_repository(repository.url, repo_name)
        print("📁 repo_path:", repo_path)

        default_branch = get_default_branch(repo_path)
        if default_branch and repository.default_branch != default_branch:
            repository.default_branch = default_branch
            db.commit()
        print("🌿 default_branch:", repository.default_branch)

        # collect_files()는 --all(전체 이력)이라 이미 삭제/이동된 경로도 섞여 있다.
        # 기본 브랜치 HEAD 기준 실제 존재 경로 집합을 구해 files.is_deleted에 반영한다
        # (그래야 "디렉토리 트리"/GitHub 링크가 더 이상 없는 경로를 가리키지 않는다).
        live_paths = set()
        if repository.default_branch:
            try:
                live_paths = collect_current_tree_paths(repo_path, repository.default_branch)
            except Exception as e:
                print("⚠️ collect_current_tree_paths 실패, is_deleted 갱신 건너뜀:", e)
        print("🌳 live_paths:", len(live_paths))

        # 증분 커서 결정: 이미 저장된 커밋 중 가장 최근 committed_at.
        # git --since는 committer date 근사 필터라 누락 방지 버퍼를 빼서 넉넉히 읽고,
        # 최종 중복은 DB upsert(ON CONFLICT)가 제거한다. 첫 수집(커밋 0개)이면 전체.
        force_full = os.getenv("FORCE_FULL_RECOLLECT", "0") == "1"
        buffer_days = int(os.getenv("INCREMENTAL_BUFFER_DAYS", "7"))
        max_committed_at = (
            db.query(Commit.committed_at)
            .filter(Commit.repository_id == repository.id)
            .order_by(Commit.committed_at.desc())
            .limit(1)
            .scalar()
        )
        since = None
        if max_committed_at and not force_full:
            since_dt = max_committed_at - timedelta(days=buffer_days)
            since = since_dt.date().isoformat()
            print(f"INCREMENTAL collect since={since} (cursor={max_committed_at}, buffer={buffer_days}d)")
        else:
            print("FULL collect (no cursor or forced)")

        # 3) Collect data
        print("START collect_commits")
        commits = collect_commits(repo_path, since=since) or []
        print("END collect_commits commits_count=", len(commits))

        print("START collect_files")
        files = collect_files(repo_path) or []
        print("END collect_files files_count=", len(files))

        print("START collect_commit_file_changes")
        changes = collect_commit_file_changes(repo_path, since=since) or []
        print("END collect_commit_file_changes changes_count=", len(changes))

        # 샘플 출력 (디버그)
        print("🔍 sample changes:")
        for c in changes[:5]:
            print(c)

        # DB 세션 재생성(안정성)
        db.close()
        db = SessionLocal()
        repository = db.query(Repository).filter(Repository.id == repository_id).first()
        print("DB RECONNECTED")

        # -----------------------
        # 4) Contributors 저장 (정규화 적용)
        # -----------------------
        for c in commits:
            c_hash = normalize_hash(c.get("hash") or c.get("commit_hash") or "")
            c["hash"] = c_hash
            c["email"] = (c.get("email") or "").strip().lower()
            c["author"] = (c.get("author") or "").strip()

        # 기존 이메일을 한 번에 로드 (이메일당 SELECT 1회씩 날리던 비용 제거)
        contributor_cache = {
            e
            for (e,) in db.query(Contributor.primary_email)
            .filter(Contributor.repository_id == repository.id)
            .all()
        }

        for c in commits:
            email = c["email"]
            if not email:
                continue
            if email in contributor_cache:
                continue

            db.add(Contributor(
                repository_id=repository.id,
                primary_email=email,
                display_name=c["author"],
            ))
            contributor_cache.add(email)

        db.commit()

        # -----------------------
        # 5) Commits 저장 및 commit_map 구성 (정규화 + dedupe + upsert)
        # -----------------------
        print("LOAD EXISTING COMMITS")
        # 레포별 기존 커밋 hash만 로드 (membership check 용도 — ORM 객체 불필요)
        existing_commits = {
            normalize_hash(h)
            for (h,) in db.query(Commit.sha)
            .filter(Commit.repository_id == repository.id)
            .all()
        }

        # Deduplicate commits by normalized hash (application-level)
        unique_commits_map: Dict[str, Dict] = {}
        for c in commits:
            h = normalize_hash(c.get("hash") or c.get("commit_hash") or "")
            if not h:
                continue
            if h not in unique_commits_map:
                unique_commits_map[h] = {
                    "hash": h,
                    "author": (c.get("author") or "").strip(),
                    "email": (c.get("email") or "").strip().lower(),
                    "message": c.get("message"),
                    "date": c.get("date"),
                }

        deduped_commits = list(unique_commits_map.values())
        print(
            "deduped commits:", len(deduped_commits), "original commits:", len(commits)
        )

        # Prepare rows for upsert (only those not already in existing_commits)
        rows = []
        for c in deduped_commits:
            h = c["hash"]
            if not h:
                continue
            if h in existing_commits:
                continue
            ctype, csource, cconf = classify_commit(c.get("message") or "")
            rows.append(
                {
                    "repository_id": repository.id,
                    "sha": h,
                    "author_name": c.get("author"),
                    "author_email": c.get("email"),
                    "message": c.get("message"),
                    "committed_at": c.get("date"),
                    "commit_type": ctype,
                    "commit_type_source": csource,
                    "commit_type_confidence": cconf,
                }
            )

        # Upsert in chunks using PostgreSQL ON CONFLICT DO NOTHING
        CHUNK_SIZE = 1000
        if rows:
            for chunk in chunked(rows, CHUNK_SIZE):
                if not chunk:
                    continue
                stmt = insert(Commit).values(chunk)
                # index_elements should match the unique constraint/index in DB.
                stmt = stmt.on_conflict_do_nothing(
                    index_elements=["repository_id", "sha"]
                )
                db.execute(stmt)
            db.commit()

        # commit_map: (hash → id) 정수 매핑만 조회
        # ORM 객체를 쓰면 commit() 후 expire되어 .id 접근마다 SELECT가 발생하므로 금지
        commit_map: Dict[str, int] = {
            normalize_hash(h): cid
            for cid, h in db.query(Commit.id, Commit.sha)
            .filter(Commit.repository_id == repository.id)
            .all()
        }
        print(
            "counts:",
            "commits_extracted=",
            len(commits),
            "deduped_commits=",
            len(deduped_commits),
            "commit_map_keys=",
            len(commit_map),
        )
        print("END SAVE COMMITS")
        _report(f"커밋 {len(commit_map)}개 저장 완료", commits_collected=len(commit_map))

        # -----------------------
        # 6) Files 저장 및 file_map 구성 (정규화)
        # -----------------------
        print("LOAD EXISTING FILES")
        for f in files:
            f["path"] = normalize_path(f.get("path") or "")

        existing_files = {
            normalize_path(p)
            for (p,) in db.query(File.path)
            .filter(File.repository_id == repository.id)
            .all()
        }

        print("START SAVE FILES")
        file_rows = []
        for f in files:
            path = normalize_path(f.get("path") or "")
            if not path:
                continue
            if path in existing_files:
                continue
            ext = path.split(".")[-1] if "." in os.path.basename(path) else None
            file_rows.append({
                "repository_id": repository.id,
                "path": path,
                "extension": ext,
            })

        if file_rows:
            for chunk in chunked(file_rows, CHUNK_SIZE):
                stmt = insert(File).values(chunk)
                stmt = stmt.on_conflict_do_nothing(index_elements=["repository_id", "path"])
                db.execute(stmt)
        db.commit()

        # file_map 조회에 앞서 is_deleted를 현재 기본 브랜치 기준으로 갱신한다.
        # (신규/기존 행 모두 대상 — 삭제된 경로가 되살아난 경우까지 한 번에 맞춰준다)
        if live_paths:
            all_files = db.query(File.id, File.path, File.is_deleted).filter(
                File.repository_id == repository.id
            ).all()
            deleted_ids = [fid for fid, p, _ in all_files if normalize_path(p) not in live_paths]
            revived_ids = [fid for fid, p, d in all_files if normalize_path(p) in live_paths and d]
            if deleted_ids:
                for chunk in chunked(deleted_ids, CHUNK_SIZE):
                    db.execute(text("UPDATE files SET is_deleted = true WHERE id = ANY(:ids)"), {"ids": chunk})
            if revived_ids:
                for chunk in chunked(revived_ids, CHUNK_SIZE):
                    db.execute(text("UPDATE files SET is_deleted = false WHERE id = ANY(:ids)"), {"ids": chunk})
            db.commit()
            print(f"🗑️ is_deleted 갱신: deleted={len(deleted_ids)} revived={len(revived_ids)}")

        # file_map: (path → id) 정수 매핑만 조회 (ORM expire 비용 제거)
        file_map: Dict[str, int] = {
            normalize_path(p): fid
            for fid, p in db.query(File.id, File.path)
            .filter(File.repository_id == repository.id)
            .all()
        }
        print(
            "counts:", "files_extracted=", len(files), "file_map_keys=", len(file_map)
        )
        print("END SAVE FILES")

        # -----------------------
        # 7) Changes 저장 (정규화 + upsert)
        # -----------------------
        print("LOAD EXISTING CHANGES")
        # existing_changes는 해당 레포의 커밋들에 한정해서 로드 (컬럼만 조회 — ORM 객체 비용 제거)
        existing_changes = set(
            (commit_id, file_id)
            for commit_id, file_id in db.query(
                CommitFileChange.commit_id, CommitFileChange.file_id
            )
            .join(Commit, Commit.id == CommitFileChange.commit_id)
            .filter(Commit.repository_id == repository.id)
            .all()
        )

        print("START SAVE CHANGES")
        processed_changes = 0
        missing_commit = 0
        missing_file = 0

        # 보완 재조회 제한 (성능 보호)
        MAX_SINGLE_LOOKUPS = 500
        single_lookup_count = 0

        # Prepare rows for commit_file_changes upsert
        change_rows = []
        for change in changes:
            processed_changes += 1
            if processed_changes % 10000 == 0:
                print("processed changes:", processed_changes)

            # 정규화
            commit_hash = normalize_hash(change.get("commit_hash") or "")
            path = normalize_path(change.get("path") or "")

            commit_id = commit_map.get(commit_hash)
            file_id = file_map.get(path)

            # 소수 보완 재조회 (디버그/보완용)
            if commit_id is None and single_lookup_count < MAX_SINGLE_LOOKUPS and commit_hash:
                db_commit = (
                    db.query(Commit.id)
                    .filter(Commit.repository_id == repository.id)
                    .filter(Commit.sha.ilike(f"{commit_hash}%"))
                    .first()
                )
                if db_commit:
                    commit_id = db_commit[0]
                    commit_map[commit_hash] = commit_id
                single_lookup_count += 1

            if file_id is None and single_lookup_count < MAX_SINGLE_LOOKUPS and path:
                db_file = (
                    db.query(File.id)
                    .filter(File.repository_id == repository.id)
                    .filter(File.path == path)
                    .first()
                )
                if db_file:
                    file_id = db_file[0]
                    file_map[path] = file_id
                single_lookup_count += 1

            if commit_id is None:
                missing_commit += 1
                if missing_commit <= 10:
                    print("MISSING COMMIT:", commit_hash)
                continue

            if file_id is None:
                missing_file += 1
                if missing_file <= 10:
                    print("MISSING FILE:", path)
                continue

            key = (commit_id, file_id)
            if key in existing_changes:
                continue

            # accumulate row for upsert
            change_rows.append(
                {
                    "commit_id": commit_id,
                    "file_id": file_id,
                    "additions": change.get("additions", 0),
                    "deletions": change.get("deletions", 0),
                }
            )
            existing_changes.add(key)

        print("missing commit:", missing_commit)
        print("missing file:", missing_file)
        print("change_rows to insert:", len(change_rows))
        print("END PREPARE CHANGES")

        # Upsert commit_file_changes in chunks
        if change_rows:
            for chunk in chunked(change_rows, CHUNK_SIZE):
                if not chunk:
                    continue
                stmt = insert(CommitFileChange).values(chunk)
                # conflict target should match unique constraint on (commit_id, file_id)
                stmt = stmt.on_conflict_do_nothing(
                    index_elements=["commit_id", "file_id"]
                )
                db.execute(stmt)
            db.commit()

        print("saved changes (unique pairs):", len(existing_changes))
        print("END SAVE CHANGES")

        # commits.changed_file_count 갱신 (large-commit 필터용)
        db.execute(text("""
            UPDATE commits c SET changed_file_count = sub.cnt
            FROM (SELECT commit_id, COUNT(*) AS cnt FROM commit_file_changes GROUP BY commit_id) sub
            WHERE sub.commit_id = c.id AND c.repository_id = :rid
        """), {"rid": repository.id})
        # 파일 변경 내역이 없던 커밋은 0
        db.execute(text(
            "UPDATE commits SET changed_file_count = 0 "
            "WHERE repository_id = :rid AND changed_file_count IS NULL"
        ), {"rid": repository.id})
        db.commit()

        # is_large_commit 플래그 (업무지시서 3-2: 절대 임계 또는 전체 파일 대비 비율)
        large_threshold = int(os.getenv("LARGE_CHANGE_THRESHOLD", "15"))
        large_pct = float(os.getenv("LARGE_CHANGE_PCT", "0.10"))
        total_files = len(file_map)
        db.execute(text("""
            UPDATE commits SET is_large_commit = (
                changed_file_count > :thr
                OR (:total > 0 AND changed_file_count > :total * :pct)
            )
            WHERE repository_id = :rid
        """), {"thr": large_threshold, "total": total_files, "pct": large_pct, "rid": repository.id})
        db.commit()

        # -----------------------
        # 8) GitHub API 수집
        # -----------------------
        github_token = _resolve_github_token(db, user_id)
        url_parts = repository.url.rstrip("/").split("/")
        owner = url_parts[-2]
        repo_name_gh = url_parts[-1].removesuffix(".git")

        rl_cb = progress.on_rate_limit if progress else None
        rs_cb = progress.on_resume if progress else None
        client = GitHubClient(token=github_token, on_rate_limit=rl_cb, on_resume=rs_cb)

        # ---- Pull Requests ----
        print("START collect_pull_requests")
        _report("PR 목록 수집 중...")
        prs_data = collect_pull_requests(client, owner, repo_name_gh)
        print(f"END collect_pull_requests prs_count={len(prs_data)}")

        LARGE_PR_THRESHOLD = 15
        LARGE_PR_PCT = 0.10
        total_files_count = len(file_map)

        existing_pr_numbers = {
            pr.pr_number
            for pr in db.query(PullRequest.pr_number)
            .filter(PullRequest.repository_id == repository.id)
            .all()
        }

        pr_rows = []
        for pr in prs_data:
            pr_number = pr["pr_number"]
            if pr_number in existing_pr_numbers:
                continue
            file_count = pr["changed_file_count"] or 0
            is_large = (
                file_count > LARGE_PR_THRESHOLD
                or (total_files_count > 0 and file_count > total_files_count * LARGE_PR_PCT)
            )
            pr_rows.append({
                "repository_id": repository.id,
                "pr_number": pr_number,
                "title": pr["title"],
                "state": pr["state"],
                "author_login": pr.get("author_login"),
                "base_branch": pr["base_branch"],
                "head_branch": pr["head_branch"],
                "merged_at": pr["merged_at"],
                "opened_at": pr["opened_at"],
                "closed_at": pr.get("closed_at"),
                "changed_file_count": file_count,
                "is_large_pr": is_large,
                "merged_commit_sha": pr["merged_commit_sha"],
            })

        if pr_rows:
            for chunk in chunked(pr_rows, CHUNK_SIZE):
                stmt = insert(PullRequest).values(chunk)
                stmt = stmt.on_conflict_do_nothing(
                    index_elements=["repository_id", "pr_number"]
                )
                db.execute(stmt)
            db.commit()

        # (id, pr_number) 튜플만 조회 — 루프 중 commit()에 의한 ORM expire/refresh 비용 제거
        saved_prs = (
            db.query(PullRequest.id, PullRequest.pr_number)
            .filter(PullRequest.repository_id == repository.id)
            .all()
        )
        print(f"saved pull_requests={len(saved_prs)}")
        _report(f"PR {len(saved_prs)}개 저장 완료, 상세 수집 시작", prs_collected=len(saved_prs))

        # PR이 0개이면 co_change_unit fallback 처리
        if len(saved_prs) == 0:
            print("⚠️ No PRs found — co_change_unit fallback to 'commit'")

        # ---- PR Commits / Reviews / Files (GraphQL) ----
        print("START collect_pr_details")
        gql_client = GraphQLClient(token=github_token, on_rate_limit=rl_cb, on_resume=rs_cb)
        pr_commit_rows = []
        pr_review_rows = []
        pr_file_rows = []

        existing_pr_commit_keys = set(
            (r.pull_request_id, r.commit_sha)
            for r in db.query(PrCommit.pull_request_id, PrCommit.commit_sha).all()
        )

        def _review_ts_key(ts) -> str:
            # DB는 timestamptz(datetime), GraphQL은 "2026-01-01T00:00:00Z" 문자열 — UTC ISO 문자열로 통일
            if ts is None:
                return ""
            if isinstance(ts, datetime):
                return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            return str(ts)

        existing_pr_review_keys = set(
            (r.pull_request_id, r.reviewer_login, _review_ts_key(r.submitted_at))
            for r in db.query(PrReview.pull_request_id, PrReview.reviewer_login, PrReview.submitted_at).all()
        )

        # 이미 상세 수집이 끝난 PR은 GraphQL 호출 자체를 스킵 (merged PR은 커밋이 반드시 1개 이상)
        prs_with_details = {
            pid
            for (pid,) in db.query(PrCommit.pull_request_id)
            .join(PullRequest, PullRequest.id == PrCommit.pull_request_id)
            .filter(PullRequest.repository_id == repository.id)
            .distinct()
            .all()
        }
        print(f"  PRs already collected (skip): {len(prs_with_details)}")

        def normalize_sha(h):
            return h.strip().lower() if h else ""

        def _flush_pr_rows():
            nonlocal pr_commit_rows, pr_review_rows, pr_file_rows
            if pr_commit_rows:
                for chunk in chunked(pr_commit_rows, CHUNK_SIZE):
                    db.execute(insert(PrCommit).values(chunk).on_conflict_do_nothing(
                        index_elements=["pull_request_id", "commit_sha"]))
                pr_commit_rows = []
            if pr_review_rows:
                db.add_all([PrReview(**r) for r in pr_review_rows])
                pr_review_rows = []
            if pr_file_rows:
                for chunk in chunked(pr_file_rows, CHUNK_SIZE):
                    db.execute(insert(PullRequestFile).values(chunk).on_conflict_do_nothing(
                        index_elements=["pull_request_id", "filename"]))
                pr_file_rows = []
            db.commit()

        def _accumulate(pr_id, details):
            # PR Commits
            for sha_raw in details["commits"]:
                sha = normalize_sha(sha_raw)
                key = (pr_id, sha)
                if key in existing_pr_commit_keys:
                    continue
                pr_commit_rows.append({
                    "pull_request_id": pr_id,
                    "commit_id": commit_map.get(sha),
                    "commit_sha": sha,
                })
                existing_pr_commit_keys.add(key)
            # PR Reviews
            for rv in details["reviews"]:
                rv_key = (pr_id, rv["reviewer_login"], _review_ts_key(rv["submitted_at"]))
                if rv_key in existing_pr_review_keys:
                    continue
                pr_review_rows.append({
                    "pull_request_id": pr_id,
                    "reviewer_login": rv["reviewer_login"],
                    "state": rv["state"],
                    "submitted_at": rv["submitted_at"],
                })
                existing_pr_review_keys.add(rv_key)
            # PR Files
            for pf in details["files"]:
                filename = pf["filename"]
                pr_file_rows.append({
                    "pull_request_id": pr_id,
                    "file_id": file_map.get(normalize_path(filename)),
                    "filename": filename,
                    "additions": pf["additions"],
                    "deletions": pf["deletions"],
                    "change_type": pf["status"],
                })

        # 배치 수집: alias로 PR 여러 개를 한 요청에 묶어 왕복 횟수를 줄인다.
        to_fetch = [(pid, num) for (pid, num) in saved_prs if pid not in prs_with_details]
        PR_BATCH = int(os.getenv("PR_DETAILS_BATCH", "20"))
        processed = 0

        for batch in chunked(to_fetch, PR_BATCH):
            numbers = [num for (_pid, num) in batch]
            try:
                details_map = collect_pr_details_graphql_batch(gql_client, owner, repo_name_gh, numbers)
            except Exception as e:
                # 배치 실패 시 per-PR 폴백 (한 PR 오류가 배치 전체를 막지 않게)
                print(f"  batch GraphQL error (PRs {numbers[:3]}...): {e}; per-PR 폴백")
                details_map = {}
                for _pid, num in batch:
                    try:
                        details_map[num] = collect_pr_details_graphql(gql_client, owner, repo_name_gh, num)
                    except Exception as e2:
                        print(f"  GraphQL error PR#{num}: {e2}")

            for pr_id, pr_number in batch:
                details = details_map.get(pr_number)
                if details:
                    _accumulate(pr_id, details)

            processed += len(batch)
            print(f"  PR details progress: {processed}/{len(to_fetch)}")
            _flush_pr_rows()

        _flush_pr_rows()
        print(f"END collect_pr_details")

        # PR 목록 API(list endpoint, collect_pull_requests에서 사용)는 changed_files 필드를
        # 아예 안 줘서(GitHub는 단건 조회 GET /pulls/{number}에서만 이 필드를 내려줌) 위에서
        # 계산한 is_large_pr이 항상 file_count=0 기준으로 잘못 판정돼 있었다(대형/머지 PR이
        # 전혀 안 걸러지고 Coupling·Network의 co-change 신호를 오염시킴). 방금 GraphQL로
        # pull_request_files에 실제 파일 목록이 다 채워졌으니, 그 실제 개수 기준으로
        # changed_file_count·is_large_pr을 다시 계산해 덮어쓴다.
        db.execute(text("""
            UPDATE pull_requests pr SET
                changed_file_count = sub.cnt,
                is_large_pr = (sub.cnt > :thr OR (:total > 0 AND sub.cnt > :total * :pct))
            FROM (
                SELECT pull_request_id, COUNT(*) AS cnt
                FROM pull_request_files
                GROUP BY pull_request_id
            ) sub
            WHERE sub.pull_request_id = pr.id AND pr.repository_id = :rid
        """), {"rid": repository.id, "thr": LARGE_PR_THRESHOLD, "total": total_files_count, "pct": LARGE_PR_PCT})
        db.commit()
        print("END recompute is_large_pr from real pull_request_files counts")

        # ---- Releases ----
        print("START collect_releases")
        _report("릴리스 수집 중...")
        releases_data = collect_releases(client, owner, repo_name_gh)
        print(f"END collect_releases releases_count={len(releases_data)}")

        existing_tags = {
            r.tag_name
            for r in db.query(Release.tag_name)
            .filter(Release.repository_id == repository.id)
            .all()
        }

        # 커밋 시각 로드 (committed_at은 timestamptz -> tz-aware datetime)
        commit_times = [
            ca
            for (ca,) in db.query(Commit.committed_at)
            .filter(Commit.repository_id == repository.id)
            .all()
            if ca is not None
        ]
        commit_times.sort()

        def _parse_github_dt(iso_str: str) -> Optional[datetime]:
            # GitHub API format: "2023-03-14T00:00:00Z"
            if not iso_str:
                return None
            try:
                return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            except ValueError:
                return None

        # Sort by published_at to calculate commit_count between releases
        releases_data.sort(key=lambda r: r["published_at"] or "")

        release_rows = []
        for idx, rel in enumerate(releases_data):
            tag = rel["tag_name"]
            if tag in existing_tags:
                continue

            # commit_count: commits between previous release and this one
            current_dt = _parse_github_dt(rel["published_at"])
            prev_dt = _parse_github_dt(releases_data[idx - 1]["published_at"]) if idx > 0 else None

            if current_dt is None:
                commit_count = 0
            else:
                hi = bisect_right(commit_times, current_dt)
                lo = bisect_right(commit_times, prev_dt) if prev_dt else 0
                commit_count = hi - lo

            release_rows.append({
                "repository_id": repository.id,
                "tag_name": tag,
                "name": rel["name"],
                "base_commit_sha": rel["base_commit_sha"],
                "is_prerelease": rel["is_prerelease"],
                "published_at": rel["published_at"],
                "release_type": rel["release_type"],
                "commit_count": commit_count,
            })

        if release_rows:
            for chunk in chunked(release_rows, CHUNK_SIZE):
                stmt = insert(Release).values(chunk)
                stmt = stmt.on_conflict_do_nothing(index_elements=["repository_id", "tag_name"])
                db.execute(stmt)
            db.commit()

        print(f"saved releases={len(release_rows)}")
        print(f"GitHub rate_limit_hits={client.rate_limit_hits} total_wait={client.total_wait_seconds}s")

        # -----------------------
        # 8.5) Contributor Identity Resolution 1~2단계 (업무지시서 3-6)
        # -----------------------
        print("START identity_resolution")
        _report("기여자 신원 정리 중...", stage="identity")
        # 2단계(이메일 검색)는 Search API 30회/분 제한이라 태스크 안에서는 상한을 둔다.
        # 전체 실행은 scripts/run_identity_resolution.py --stage2-full 사용.
        stage2_limit = int(os.getenv("IDENTITY_STAGE2_LIMIT", "200"))
        resolution = run_identity_resolution(
            db, repository.id, client=client, stage2_limit=stage2_limit
        )
        print("identity_resolution:", resolution)
        print("END identity_resolution")

        # -----------------------
        # 9) 완료 처리
        # -----------------------
        # 파생 지표 계산: PR review_duration_hours 갱신 (merged - opened, 시간 단위)
        # (changed_file_count/is_large_commit/is_large_pr 등 나머지 파생 지표는 수집
        #  단계에서 이미 계산됨 — 여기서는 identity resolution 이후에만 가능한
        #  review duration을 마저 채운다)
        _report("파생 지표 계산 중...", stage="derived")
        db.execute(text("""
            UPDATE pull_requests
            SET review_duration_hours = FLOOR(EXTRACT(EPOCH FROM (merged_at - opened_at)) / 3600)
            WHERE repository_id = :rid
              AND merged_at IS NOT NULL AND opened_at IS NOT NULL
              AND review_duration_hours IS NULL
        """), {"rid": repository.id})

        # 분석 결과 스냅샷 저장: job/repository의 완료 상태를 확정 커밋한다
        _report("분석 결과 저장 중...", stage="snapshot")
        now = datetime.now(timezone.utc)
        repository.last_analyzed_at = now
        if job:
            job = db.merge(job)
            job.status = "done"
            job.completed_at = now
            job.last_api_fetched_at = now
            last_commit = (
                db.query(Commit.sha, Commit.committed_at)
                .filter(Commit.repository_id == repository.id)
                .order_by(Commit.committed_at.desc())
                .first()
            )
            if last_commit:
                job.last_commit_sha, job.last_commit_at = last_commit
        db.commit()
        _report("분석 완료", stage="done")
        print("✅ TASK COMPLETED")

    except Exception as e:
        import traceback

        print("ERROR:", e)
        traceback.print_exc()
        try:
            db.rollback()
            failed_job = (
                db.query(AnalysisJob)
                .filter(AnalysisJob.repository_id == repository_id)
                .order_by(AnalysisJob.id.desc())
                .first()
            )
            if failed_job:
                failed_job.status = "failed"
                failed_job.error_message = str(e)
                failed_job.completed_at = datetime.now(timezone.utc)
            db.commit()
        except Exception:
            pass
    finally:
        try:
            db.close()
        except Exception:
            pass
